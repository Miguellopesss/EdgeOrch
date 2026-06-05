"""Automatic rebalance proposal and migration orchestration for EdgeOrch."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from config import Config
from provisioning_service import ProvisioningService, SUCCESS_RESULT_STATUSES


CLUSTER_MIGRATION_QUIET_SECONDS = 180
REBALANCE_DECISION_SECONDS = 10

if TYPE_CHECKING:
    from lease_manager import LeaseManager


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_float(value: Any) -> float:
    try:
        text = str(value).strip()
        if not text:
            return 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


class RebalanceManager:
    """Track rebalance proposals, user decisions, and migration jobs."""

    def __init__(self, service: ProvisioningService, config: Config, lease_manager: Optional["LeaseManager"] = None) -> None:
        self._service = service
        self._config = config
        self._lease_manager = lease_manager
        self._lock = threading.RLock()
        self._pending_proposal: Optional[Dict[str, Any]] = None
        self._proposal_timer: Optional[threading.Timer] = None
        self._active_migration: Optional[Dict[str, Any]] = None
        self._last_completed_migration: Optional[Dict[str, Any]] = None
        self._declined_topologies: set[str] = set()
        self._expired_proposals: set[str] = set()
        self._topology_key: str = ""
        self._topology_started_at: float = time.time()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = self._build_snapshot_locked()
            self._refresh_topology_locked(snapshot)
            if not self._active_migration:
                cluster_migration_recent = self._has_recent_cluster_migration_request_locked()
                if self._pending_proposal and self._is_proposal_expired(self._pending_proposal):
                    if not self._has_accepted_decision_locked(self._pending_proposal):
                        self._publish_rebalance_decision(self._pending_proposal, "accepted")
                        self._start_migration_locked(dict(self._pending_proposal), trigger="timeout")
                    self._pending_proposal = None
                    self._cancel_proposal_timer_locked()
                next_proposal = None if cluster_migration_recent else self._select_proposal_locked(snapshot)
                if not next_proposal:
                    self._pending_proposal = None
                    self._cancel_proposal_timer_locked()
                elif not self._pending_proposal or str(self._pending_proposal.get("id", "")).strip() != str(next_proposal.get("id", "")).strip():
                    self._pending_proposal = next_proposal
                else:
                    preserved = {
                        "created_at": self._pending_proposal.get("created_at"),
                        "created_at_iso": self._pending_proposal.get("created_at_iso"),
                        "expires_at": self._pending_proposal.get("expires_at"),
                    }
                    self._pending_proposal.update(next_proposal)
                    self._pending_proposal.update(preserved)

            return {
                "success": True,
                "proposal": self._serialize_proposal(self._pending_proposal),
                "active_migration": self._serialize_active_migration(self._active_migration),
                "last_completed_migration": self._serialize_completed_migration(self._last_completed_migration),
                "topology_key": self._topology_key,
            }

    def decide(self, proposal_id: str, accept: bool) -> Dict[str, Any]:
        with self._lock:
            if not self._pending_proposal or self._pending_proposal.get("id") != proposal_id:
                raise ValueError("This migration proposal is no longer available.")

            proposal = dict(self._pending_proposal)
            self._pending_proposal = None
            self._cancel_proposal_timer_locked()

            if not accept:
                self._publish_rebalance_decision(proposal, "declined")
                topology_key = str(proposal.get("topology_key", "")).strip()
                if topology_key:
                    self._declined_topologies.add(topology_key)
                return self.get_status()

            if self._has_accepted_decision_locked(proposal):
                return self.get_status()
            self._publish_rebalance_decision(proposal, "accepted")
            self._start_migration_locked(proposal, trigger="user")
            return self.get_status()

    def is_machine_locked(self, vmid: str) -> bool:
        with self._lock:
            if not self._active_migration:
                return False
            return (
                str(self._active_migration.get("status", "")).strip() == "running"
                and str(self._active_migration.get("vmid", "")).strip() == str(vmid).strip()
            )

    def _build_snapshot_locked(self) -> Dict[str, Any]:
        machines = self._service.list_cluster_managed_machines()
        if not isinstance(machines, list):
            machines = []

        worker_nodes = list(self._config.worker_proxmox_nodes) or sorted(
            {
                str(machine.get("proxmox_node", "")).strip()
                for machine in machines
                if str(machine.get("proxmox_node", "")).strip()
            }
        )

        counts: Dict[str, int] = {node: 0 for node in worker_nodes}
        by_node: Dict[str, List[Dict[str, Any]]] = {node: [] for node in worker_nodes}
        for machine in machines:
            node_name = str(machine.get("proxmox_node", "")).strip()
            if node_name not in by_node:
                continue
            counts[node_name] += 1
            by_node[node_name].append(machine)

        topology_parts = [
            (
                f"{machine.get('vmid','')}:{machine.get('proxmox_node','')}:"
                f"{machine.get('request_id','')}:{machine.get('owner_origin','')}"
            )
            for machine in sorted(
                machines,
                key=lambda item: (
                    str(item.get("proxmox_node", "")),
                    str(item.get("hostname", "")),
                    str(item.get("vmid", "")),
                ),
            )
        ]

        return {
            "machines": machines,
            "counts": counts,
            "by_node": by_node,
            "worker_nodes": worker_nodes,
            "topology_key": "|".join(topology_parts) or "empty",
        }

    def _refresh_topology_locked(self, snapshot: Dict[str, Any]) -> None:
        topology_key = str(snapshot.get("topology_key", "")).strip() or "empty"
        if topology_key == self._topology_key:
            if self._topology_started_at <= 0:
                self._topology_started_at = time.time()
            return

        self._topology_key = topology_key
        self._topology_started_at = time.time()
        self._pending_proposal = None
        self._expired_proposals.clear()
        self._cancel_proposal_timer_locked()

        stale_keys = [key for key in self._declined_topologies if key != topology_key]
        for key in stale_keys:
            self._declined_topologies.discard(key)

    def _select_proposal_locked(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        worker_nodes = list(snapshot.get("worker_nodes", []))
        if len(worker_nodes) < 2:
            return None
        total_machine_count = len(list(snapshot.get("machines", [])))
        counts = dict(snapshot.get("counts", {}))
        highest_count = max(counts.values(), default=0)
        lowest_count = min(counts.values(), default=0)
        allow_delete_backfill = total_machine_count >= 2 and highest_count >= 2 and lowest_count == 0
        if total_machine_count < self._config.rebalance_min_total_machines and not allow_delete_backfill:
            return None

        by_node = dict(snapshot.get("by_node", {}))
        topology_key = str(snapshot.get("topology_key", "")).strip() or "empty"
        source_candidates = sorted(worker_nodes, key=lambda node: (-counts.get(node, 0), node))
        target_candidates = sorted(worker_nodes, key=lambda node: (counts.get(node, 0), node))
        decisions = self._get_rebalance_decisions_locked(topology_key)
        declined_owners = self._get_declined_owners_locked(topology_key)

        candidate_proposals: List[Dict[str, Any]] = []
        for source_node in source_candidates:
            source_count = counts.get(source_node, 0)
            if source_count <= 0:
                continue

            for target_node in target_candidates:
                if target_node == source_node:
                    continue

                target_count = counts.get(target_node, 0)
                if source_count - target_count < self._config.rebalance_min_count_gap:
                    continue

                source_machines = list(by_node.get(source_node, []))
                source_machines.sort(
                    key=lambda machine: (
                        0 if str(machine.get("machine_status", "")).strip().lower() == "stopped" else 1,
                        float(machine.get("disk_gb") or 0),
                        str(machine.get("hostname", "")),
                    )
                )

                for machine in source_machines:
                    vmid = str(machine.get("vmid", "")).strip()
                    if not vmid:
                        continue

                    allowed_targets = self._service.get_allowed_migration_targets(source_node, vmid)
                    if target_node not in allowed_targets:
                        continue

                    candidate_proposals.append(
                        {
                            "topology_key": topology_key,
                            "proposal_key": self._build_proposal_key(vmid, source_node, target_node),
                            "source_node": source_node,
                            "source_node_label": self._config.worker_node_labels.get(source_node, source_node),
                            "target_node": target_node,
                            "target_node_label": self._config.worker_node_labels.get(target_node, target_node),
                            "timeout_seconds": self._config.rebalance_proposal_timeout_seconds,
                            "manual_decision": True,
                            "owner_origin": str(machine.get("owner_origin", "")).strip(),
                            "owner_reply_to": str(machine.get("owner_reply_to", "")).strip(),
                            "owned_by_current_client": bool(machine.get("owned_by_current_client")),
                            "machine": {
                                "vmid": vmid,
                                "hostname": str(machine.get("hostname", "")).strip() or f"ct-{vmid}",
                                "ip_address": str(machine.get("ip_address", "")).strip(),
                                "machine_status": str(machine.get("machine_status", "")).strip().lower(),
                                "cpu": machine.get("cpu"),
                                "memory_mb": machine.get("memory_mb"),
                                "disk_gb": machine.get("disk_gb"),
                                "network": str(machine.get("network", "")).strip(),
                            },
                            "distribution": counts,
                            "message": (
                                f"{source_node} is carrying more load than {target_node}. "
                                f"Machine {str(machine.get('hostname', '')).strip() or vmid} can be migrated to balance the cluster."
                            ),
                        }
                    )

        if not candidate_proposals:
            return None

        eligible_proposals = [
            candidate
            for candidate in candidate_proposals
            if str(candidate.get("owner_origin", "")).strip() not in declined_owners
        ]
        if not eligible_proposals:
            return None

        active_offer = self._get_active_offer_locked(topology_key, eligible_proposals)
        if active_offer:
            if bool(active_offer.get("owned_by_current_client")):
                return active_offer
            return None

        selected_candidate = None
        for candidate in eligible_proposals:
            proposal_key = str(candidate.get("proposal_key", "")).strip()
            if proposal_key in self._expired_proposals:
                continue
            decision = decisions.get(proposal_key)
            if decision == "accepted":
                return None
            if decision == "declined":
                continue
            selected_candidate = candidate
            break

        if not selected_candidate:
            return None

        selected_vmid = str(selected_candidate.get("machine", {}).get("vmid", "")).strip()

        timeout_seconds = REBALANCE_DECISION_SECONDS
        created_at = time.time()
        proposal = {
            **selected_candidate,
            "id": f"rb-0-{selected_vmid}",
            "created_at": created_at,
            "created_at_iso": _now_iso(),
            "expires_at": created_at + timeout_seconds,
            "timeout_seconds": timeout_seconds,
            "proposal_slot_index": 0,
        }
        self._publish_rebalance_decision(proposal, "offered")
        return proposal if bool(proposal.get("owned_by_current_client")) else None

    def _get_rebalance_decisions_locked(self, topology_key: str) -> Dict[str, str]:
        decisions_by_proposal: Dict[str, tuple[float, str]] = {}
        for item in self._service.list_rebalance_decisions():
            content = item.get("con")
            if not isinstance(content, dict):
                continue
            if str(content.get("type", "")).strip() != "rebalance_decision":
                continue
            if str(content.get("topology_key", "")).strip() != topology_key:
                continue
            proposal_key = str(content.get("proposal_key", "")).strip()
            decision = str(content.get("decision", "")).strip()
            if proposal_key and decision in {"accepted", "declined", "offered"}:
                decision_time = self._decision_sort_time(item, content)
                previous = decisions_by_proposal.get(proposal_key)
                if not previous or decision_time >= previous[0]:
                    decisions_by_proposal[proposal_key] = (decision_time, decision)
        return {proposal_key: decision for proposal_key, (_, decision) in decisions_by_proposal.items()}

    def _get_declined_owners_locked(self, topology_key: str) -> set[str]:
        latest_by_proposal: Dict[str, tuple[float, str, str]] = {}
        for item in self._service.list_rebalance_decisions():
            content = item.get("con")
            if not isinstance(content, dict):
                continue
            if str(content.get("type", "")).strip() != "rebalance_decision":
                continue
            if str(content.get("topology_key", "")).strip() != topology_key:
                continue

            proposal_key = str(content.get("proposal_key", "")).strip()
            decision = str(content.get("decision", "")).strip()
            owner_origin = str(content.get("owner_origin", "")).strip()
            if not proposal_key or decision not in {"accepted", "declined", "offered"}:
                continue

            decision_time = self._decision_sort_time(item, content)
            previous = latest_by_proposal.get(proposal_key)
            if not previous or decision_time >= previous[0]:
                latest_by_proposal[proposal_key] = (decision_time, decision, owner_origin)

        return {
            owner_origin
            for _, decision, owner_origin in latest_by_proposal.values()
            if decision == "declined" and owner_origin
        }

    def _get_active_offer_locked(
        self,
        topology_key: str,
        candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        candidate_by_key = {
            str(candidate.get("proposal_key", "")).strip(): candidate
            for candidate in candidates
            if str(candidate.get("proposal_key", "")).strip()
        }
        terminal_decisions = self._get_rebalance_decisions_locked(topology_key)

        decision_items: List[tuple[float, Dict[str, Any], Dict[str, Any]]] = []
        for item in self._service.list_rebalance_decisions():
            content = item.get("con")
            if not isinstance(content, dict):
                continue
            if str(content.get("type", "")).strip() != "rebalance_decision":
                continue
            if str(content.get("decision", "")).strip() != "offered":
                continue
            if str(content.get("topology_key", "")).strip() != topology_key:
                continue
            decision_items.append((self._decision_sort_time(item, content), item, content))

        for _, _, content in sorted(decision_items, key=lambda entry: entry[0], reverse=True):
            proposal_key = str(content.get("proposal_key", "")).strip()
            if terminal_decisions.get(proposal_key) in {"accepted", "declined"}:
                continue
            candidate = candidate_by_key.get(proposal_key)
            if not candidate:
                continue

            expires_at = _to_float(content.get("expires_at"))
            if expires_at <= time.time():
                continue

            proposal = dict(candidate)
            proposal.update(
                {
                    "id": str(content.get("proposal_id", "")).strip() or f"rb-0-{proposal.get('machine', {}).get('vmid', '')}",
                    "created_at": _to_float(content.get("created_at")),
                    "created_at_iso": str(content.get("created_at_iso", "")).strip() or _now_iso(),
                    "expires_at": expires_at,
                    "timeout_seconds": int(_to_float(content.get("timeout_seconds")) or REBALANCE_DECISION_SECONDS),
                    "proposal_slot_index": 0,
                }
            )
            return proposal

        return None

    def _decision_sort_time(self, item: Dict[str, Any], content: Dict[str, Any]) -> float:
        created_at = _to_float(content.get("created_at"))
        if created_at > 0:
            return created_at

        parsed_ct = self._parse_onem2m_timestamp(str(item.get("ct", "")).strip())
        if parsed_ct is not None:
            return parsed_ct

        return 0.0

    def _has_accepted_decision_locked(self, proposal: Dict[str, Any]) -> bool:
        topology_key = str(proposal.get("topology_key", "")).strip()
        proposal_key = str(proposal.get("proposal_key", "")).strip()
        return self._get_rebalance_decisions_locked(topology_key).get(proposal_key) == "accepted"

    def _publish_rebalance_decision(self, proposal: Dict[str, Any], decision: str) -> None:
        self._service.publish_rebalance_decision(
            {
                "type": "rebalance_decision",
                "decision": decision,
                "topology_key": str(proposal.get("topology_key", "")).strip(),
                "proposal_key": str(proposal.get("proposal_key", "")).strip(),
                "proposal_id": str(proposal.get("id", "")).strip(),
                "created_at": proposal.get("created_at"),
                "created_at_iso": str(proposal.get("created_at_iso", "")).strip(),
                "expires_at": proposal.get("expires_at"),
                "timeout_seconds": proposal.get("timeout_seconds"),
                "vmid": str(proposal.get("machine", {}).get("vmid", "")).strip(),
                "hostname": str(proposal.get("machine", {}).get("hostname", "")).strip(),
                "source_node": str(proposal.get("source_node", "")).strip(),
                "target_node": str(proposal.get("target_node", "")).strip(),
                "owner_origin": str(proposal.get("owner_origin", "")).strip(),
                "owner_reply_to": str(proposal.get("owner_reply_to", "")).strip(),
                "created_by": self._config.client_origin,
                "timestamp": _now_iso(),
            }
        )

    @staticmethod
    def _build_proposal_key(vmid: str, source_node: str, target_node: str) -> str:
        return f"{vmid}:{source_node}->{target_node}"

    @staticmethod
    def _is_proposal_expired(proposal: Dict[str, Any]) -> bool:
        expires_at = proposal.get("expires_at")
        if expires_at in ("", None):
            return False
        try:
            return time.time() >= float(expires_at)
        except (TypeError, ValueError):
            return False

    def _has_recent_cluster_migration_request_locked(self) -> bool:
        requests = self._service.list_provisioning_requests()

        now = time.time()
        for item in reversed(requests):
            content = item.get("con")
            if not isinstance(content, dict):
                continue
            if str(content.get("action", "")).strip() != "migrate_lxc":
                continue

            created_at = self._parse_onem2m_timestamp(str(item.get("ct", "")).strip())
            if created_at is None:
                continue
            if now - created_at <= CLUSTER_MIGRATION_QUIET_SECONDS:
                return True

        return False

    @staticmethod
    def _parse_onem2m_timestamp(value: str) -> Optional[float]:
        if not value:
            return None
        normalized = value.split(",", 1)[0]
        try:
            return datetime.strptime(normalized, "%Y%m%dT%H%M%S").timestamp()
        except ValueError:
            return None

    def _schedule_proposal_timer_locked(self, proposal: Dict[str, Any]) -> None:
        self._cancel_proposal_timer_locked()
        delay_seconds = max(1, int(float(proposal.get("expires_at", time.time())) - time.time()))
        timer = threading.Timer(delay_seconds, self._auto_accept_proposal, args=[str(proposal.get("id", ""))])
        timer.daemon = True
        timer.start()
        self._proposal_timer = timer

    def _cancel_proposal_timer_locked(self) -> None:
        if self._proposal_timer is not None:
            self._proposal_timer.cancel()
            self._proposal_timer = None

    def _auto_accept_proposal(self, proposal_id: str) -> None:
        with self._lock:
            if not self._pending_proposal or self._pending_proposal.get("id") != proposal_id:
                return

            proposal = dict(self._pending_proposal)
            self._pending_proposal = None
            self._cancel_proposal_timer_locked()
            self._start_migration_locked(proposal, trigger="timeout")

    def _start_migration_locked(self, proposal: Dict[str, Any], trigger: str) -> None:
        migration_id = f"mig-{uuid.uuid4().hex[:10]}"
        machine = dict(proposal.get("machine", {}))
        vmid = str(machine.get("vmid", "")).strip()
        if self._lease_manager and vmid:
            self._lease_manager.begin_machine_transfer(vmid)
        self._active_migration = {
            "id": migration_id,
            "proposal_id": proposal.get("id"),
            "vmid": vmid,
            "hostname": str(machine.get("hostname", "")).strip(),
            "source_node": str(proposal.get("source_node", "")).strip(),
            "source_node_label": str(proposal.get("source_node_label", "")).strip(),
            "target_node": str(proposal.get("target_node", "")).strip(),
            "target_node_label": str(proposal.get("target_node_label", "")).strip(),
            "status": "running",
            "message": "The migration was accepted and is now being prepared.",
            "started_at": _now_iso(),
            "trigger": trigger,
        }

        worker = threading.Thread(target=self._run_migration_job, args=(migration_id, proposal), daemon=True)
        worker.start()

    def _run_migration_job(self, migration_id: str, proposal: Dict[str, Any]) -> None:
        machine = dict(proposal.get("machine", {}))
        vmid = str(machine.get("vmid", "")).strip()
        hostname = str(machine.get("hostname", "")).strip()
        result_payload: Optional[Dict[str, Any]] = None
        completed_message = "The migration finished."
        completed_success = False

        try:
            target_node = str(proposal.get("target_node", "")).strip()
            fallback_source_node = str(proposal.get("source_node", "")).strip()
            current_location = self._service.resolve_migration_location(vmid, target_node, fallback_source_node)
            current_node = str((current_location or {}).get("node", "")).strip()
            if current_node and current_node == target_node:
                result_payload = {
                    "status": "completed",
                    "message": "The machine is already on the migration target.",
                    "action": "migrate_lxc",
                    "vmid": vmid,
                    "hostname": hostname,
                    "proxmox_node": current_node,
                    "target_proxmox_node": target_node,
                }
                completed_success = True
                completed_message = "The machine is already on the migration target."
                return
            if current_node and current_node != str(proposal.get("source_node", "")).strip():
                proposal = dict(proposal)
                proposal["source_node"] = current_node
                proposal["source_node_label"] = str((current_location or {}).get("node_hostname", "")).strip() or self._config.worker_node_labels.get(current_node, current_node)

            operation = self._run_migration_request(vmid, hostname, proposal, current_location)
            result_payload = operation.get("result") if isinstance(operation, dict) else None
            completed_success = (
                isinstance(result_payload, dict)
                and str(result_payload.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
            )
            completed_message = (
                str((result_payload or {}).get("message", "")).strip()
                or "The migration finished."
            )
            if not completed_success and "does not exist on node" in completed_message.lower():
                retry_location = self._service.resolve_migration_location(vmid, target_node, "")
                retry_node = str((retry_location or {}).get("node", "")).strip()
                if retry_node and retry_node == target_node:
                    result_payload = {
                        "status": "completed",
                        "message": "The machine is already on the migration target.",
                        "action": "migrate_lxc",
                        "vmid": vmid,
                        "hostname": hostname,
                        "proxmox_node": retry_node,
                        "target_proxmox_node": target_node,
                    }
                    completed_success = True
                    completed_message = "The machine is already on the migration target."
                elif retry_node:
                    proposal = dict(proposal)
                    proposal["source_node"] = retry_node
                    proposal["source_node_label"] = str((retry_location or {}).get("node_hostname", "")).strip() or self._config.worker_node_labels.get(retry_node, retry_node)
                    operation = self._run_migration_request(vmid, hostname, proposal, retry_location)
                    result_payload = operation.get("result") if isinstance(operation, dict) else None
                    completed_success = (
                        isinstance(result_payload, dict)
                        and str(result_payload.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
                    )
                    completed_message = (
                        str((result_payload or {}).get("message", "")).strip()
                        or "The migration finished."
                    )
        except Exception as exc:
            completed_message = str(exc)
            completed_success = False

        finally:
            with self._lock:
                if self._lease_manager and vmid:
                    if completed_success:
                        refreshed_machine = self._service.get_machine(vmid)
                        if refreshed_machine is not None:
                            self._lease_manager.complete_machine_transfer(refreshed_machine)
                        else:
                            self._lease_manager.cancel_machine_transfer(vmid)
                    else:
                        self._lease_manager.cancel_machine_transfer(vmid)

                active = self._active_migration or {}
                completed_payload = {
                    "id": migration_id,
                    "proposal_id": proposal.get("id"),
                    "vmid": vmid,
                    "hostname": hostname,
                    "source_node": str(proposal.get("source_node", "")).strip(),
                    "source_node_label": str(proposal.get("source_node_label", "")).strip(),
                    "target_node": str(proposal.get("target_node", "")).strip(),
                    "target_node_label": str(proposal.get("target_node_label", "")).strip(),
                    "status": "completed" if completed_success else "failed",
                    "message": completed_message,
                    "started_at": active.get("started_at"),
                    "finished_at": _now_iso(),
                    "trigger": active.get("trigger"),
                    "result": result_payload,
                }
                self._last_completed_migration = completed_payload
                self._active_migration = None
                self._pending_proposal = None
                self._cancel_proposal_timer_locked()

    def _run_migration_request(
        self,
        vmid: str,
        hostname: str,
        proposal: Dict[str, Any],
        current_location: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        source_node = str((current_location or {}).get("node", "")).strip() or str(proposal.get("source_node", "")).strip()
        source_node_hostname = (
            str((current_location or {}).get("node_hostname", "")).strip()
            or str(proposal.get("source_node_label", "")).strip()
            or self._config.worker_node_labels.get(source_node, source_node)
        )
        target_node = str(proposal.get("target_node", "")).strip()
        return self._service.run_machine_action(
            "migrate_lxc",
            vmid,
            hostname=hostname,
            extra_payload={
                "target_proxmox_node": source_node,
                "target_node_hostname": source_node_hostname,
                "migration_target_proxmox_node": target_node,
                "migration_target_node_hostname": str(proposal.get("target_node_label", "")).strip(),
                "migration_target_node_ip": str(
                    self._config.worker_node_ae_ips.get(target_node, "")
                ).strip(),
            },
        )

    def _serialize_proposal(self, proposal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not proposal:
            return None

        serialized = dict(proposal)
        expires_at = proposal.get("expires_at")
        serialized["remaining_seconds"] = (
            max(0, int(float(expires_at) - time.time())) if expires_at not in ("", None) else None
        )
        return serialized

    @staticmethod
    def _serialize_active_migration(active_migration: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return dict(active_migration) if active_migration else None

    @staticmethod
    def _serialize_completed_migration(completed_migration: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return dict(completed_migration) if completed_migration else None
