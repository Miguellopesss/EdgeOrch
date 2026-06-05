"""Shared provisioning workflows for the CLI and the web client."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import Config
from notification_channel import NotificationChannel
from onem2m_client import OneM2MClient


REDACTED_SECRET = "<hidden>"
SUCCESS_RESULT_STATUSES = {"completed", "created_no_ip"}
TEMPLATE_SUGGESTIONS = [
    "ubuntu-24.04-ssh-enabled",
    "ubuntu-22.04-standard",
]
SIZE_PATTERN = re.compile(r"size=(?P<value>\d+(?:\.\d+)?)(?P<unit>[KMGTP])", re.IGNORECASE)
CAPACITY_DISK_MARGIN_GB = 1.0
PLACEMENT_RESERVATION_SECONDS = 180


def _to_int(value: Any) -> Optional[int]:
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float:
    try:
        text = str(value).strip()
        if not text:
            return 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _parse_description_metadata(description: str) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for part in str(description or "").split(";"):
        normalized_part = part.strip()
        if "=" not in normalized_part:
            continue
        key, value = normalized_part.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key.lower().endswith("request_id"):
            normalized_key = "request_id"
        if normalized_key:
            metadata[normalized_key] = normalized_value
    return metadata


def _parse_disk_size_gb(rootfs_value: str, fallback_bytes: Any = None) -> Optional[float]:
    match = SIZE_PATTERN.search(str(rootfs_value or ""))
    if match:
        size_value = float(match.group("value"))
        unit = match.group("unit").upper()
        multipliers = {
            "K": 1 / (1024 * 1024),
            "M": 1 / 1024,
            "G": 1,
            "T": 1024,
            "P": 1024 * 1024,
        }
        return round(size_value * multipliers[unit], 1)

    bytes_value = _to_int(fallback_bytes)
    if bytes_value and bytes_value > 0:
        return round(bytes_value / (1024 ** 3), 1)

    return None


def sanitize_secret_fields(value: Any) -> Any:
    """Hide sensitive values before they are echoed back to the browser."""

    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "root_password":
                sanitized[key] = REDACTED_SECRET
            else:
                sanitized[key] = sanitize_secret_fields(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_secret_fields(item) for item in value]

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            return json.dumps(sanitize_secret_fields(parsed), ensure_ascii=False)
        return value

    return value


class ProvisioningService:
    """High-level workflows used by the web application."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def ensure_ready(self) -> Dict[str, Any]:
        client = self._client()
        success, message = client.ensure_private_result_channel()
        return {"success": success, "message": message}

    def create_machine(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        placement = self.plan_create_placement(payload)
        requested_target = str(payload.get("target_proxmox_node", "")).strip()

        if requested_target:
            requested_target_candidate = next(
                (candidate for candidate in placement["candidates"] if candidate["node"] == requested_target),
                None,
            )
            if not requested_target_candidate or not requested_target_candidate["can_host"]:
                if requested_target_candidate:
                    reason_text = "; ".join(requested_target_candidate["reasons"]) or "insufficient free resources"
                    raise RuntimeError(f"Node {requested_target} cannot host this machine right now: {reason_text}.")
                raise RuntimeError(f"Node {requested_target} is not available for provisioning.")
        else:
            preferred_node = str(placement.get("preferred_node", "")).strip()
            if not preferred_node:
                summary_lines = [
                    f"{candidate['node']}: {'; '.join(candidate['reasons']) or 'not eligible'}"
                    for candidate in placement["candidates"]
                ]
                summary_text = " | ".join(summary_lines) or "No worker node reported enough free resources."
                raise RuntimeError(
                    "No worker node currently has enough free CPU, memory, and disk for this machine. "
                    f"{summary_text}"
                )
            payload = dict(payload)
            payload["target_proxmox_node"] = preferred_node
        self.publish_placement_reservation(payload)
        return self.submit_request("create_lxc", payload)

    def run_machine_action(
        self,
        action: str,
        vmid: str,
        hostname: str = "",
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"vmid": vmid}
        machine = self.get_machine(vmid)

        resolved_hostname = hostname.strip() if hostname else ""
        if not resolved_hostname and machine is not None:
            resolved_hostname = str(machine.get("hostname", "")).strip()
        if resolved_hostname:
            payload["hostname"] = resolved_hostname

        if machine is not None:
            target_proxmox_node = str(machine.get("proxmox_node", "")).strip()
            target_node_hostname = str(machine.get("node_hostname", "")).strip()
            if target_proxmox_node:
                payload["target_proxmox_node"] = target_proxmox_node
            if target_node_hostname:
                payload["target_node_hostname"] = target_node_hostname

        if action != "create_lxc" and not str(payload.get("target_proxmox_node", "")).strip():
            raise RuntimeError(
                "Could not determine which node currently owns this machine. Refresh the inventory and try again."
            )

        if extra_payload:
            payload.update(extra_payload)

        return self.submit_request(action, payload)

    def submit_request(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = self._client()
        ready = self.ensure_ready()
        if not ready["success"]:
            raise RuntimeError(str(ready["message"]))

        request_payload = self._build_request_payload(action, payload)
        notification_channel = NotificationChannel(self.config)
        subscription_path = ""
        use_notifications = False

        try:
            notify_success, notify_message = notification_channel.start()
            if notify_success:
                subscription_name = f"sub_{str(request_payload['request_id']).replace('-', '_')}"
                sub_success, sub_message, subscription_path = client.create_results_subscription(
                    notification_channel.notification_url,
                    subscription_name,
                )
                if not sub_success:
                    raise RuntimeError(sub_message)
                use_notifications = True
            else:
                notify_message = f"Notification channel unavailable. Falling back to polling. Reason: {notify_message}"

            publish_success, publish_message, publish_response = client.publish_request(request_payload)
            if not publish_success:
                raise RuntimeError(publish_message)

            if use_notifications:
                notified, notified_message, _ = notification_channel.wait_for_request(str(request_payload["request_id"]))
                if notified:
                    result_success, result_message, result = client.get_result_for_request(str(request_payload["request_id"]))
                else:
                    result_success, result_message, result = client.wait_for_result(
                        str(request_payload["request_id"]),
                        timeout_seconds=None if action in {"create_lxc", "migrate_lxc"} else 15,
                        poll_interval_seconds=2,
                    )
            else:
                notified_message = notify_message
                result_success, result_message, result = client.wait_for_result(
                    str(request_payload["request_id"]),
                    timeout_seconds=None if action in {"create_lxc", "migrate_lxc"} else 180,
                )

            if not result_success or not result:
                raise RuntimeError(result_message)

            result_content = ((result.get("m2m:cin") or {}).get("con")) if isinstance(result, dict) else None
            if not isinstance(result_content, dict):
                raise RuntimeError("The CSE replied without a parseable result for this request.")

            return {
                "request": sanitize_secret_fields(request_payload),
                "publish_message": publish_message,
                "notification_message": notified_message,
                "result_message": result_message,
                "result": sanitize_secret_fields(result_content),
                "raw_result": sanitize_secret_fields(result),
            }
        finally:
            if subscription_path:
                client.delete_resource(subscription_path)
            notification_channel.close()

    def list_results(self) -> Dict[str, Any]:
        client = self._client()
        ready = self.ensure_ready()
        if not ready["success"]:
            raise RuntimeError(str(ready["message"]))

        success, message, results = client.list_private_results()
        if not success:
            raise RuntimeError(message)

        return {
            "success": True,
            "message": message,
            "results": sanitize_secret_fields(results),
        }

    def list_provisioning_requests(self) -> List[Dict[str, Any]]:
        client = self._client()
        success, _, requests = client.list_provisioning_requests()
        if not success:
            return []
        return sanitize_secret_fields(requests)

    def list_rebalance_decisions(self) -> List[Dict[str, Any]]:
        client = self._client()
        success, _, decisions = client.list_rebalance_decisions()
        if not success:
            return []
        return sanitize_secret_fields(decisions)

    def publish_rebalance_decision(self, payload: Dict[str, Any]) -> None:
        client = self._client()
        success, message, _ = client.publish_rebalance_decision(payload)
        if not success:
            raise RuntimeError(message)

    def list_placement_reservations(self) -> List[Dict[str, Any]]:
        client = self._client()
        success, _, reservations = client.list_placement_reservations()
        if not success:
            return []
        return sanitize_secret_fields(reservations)

    def publish_placement_reservation(self, payload: Dict[str, Any]) -> None:
        target_node = str(payload.get("target_proxmox_node", "")).strip()
        if not target_node:
            return

        reservation_payload = {
            "type": "placement_reservation",
            "reservation_id": f"placement-{uuid.uuid4().hex[:12]}",
            "action": "create_lxc",
            "hostname": str(payload.get("hostname", "")).strip(),
            "target_proxmox_node": target_node,
            "cpu": max(1, _to_int(payload.get("cpu")) or 1),
            "memory_mb": max(1, _to_int(payload.get("memory_mb")) or 1),
            "disk_gb": max(1.0, _to_float(payload.get("disk_gb")) or 1.0),
            "created_by": self.config.client_origin,
            "created_at": time.time(),
            "ttl_seconds": PLACEMENT_RESERVATION_SECONDS,
        }
        client = self._client()
        success, message, _ = client.publish_placement_reservation(reservation_payload)
        if not success:
            raise RuntimeError(message)

    def list_machines(self) -> Dict[str, Any]:
        results_payload = self.list_results()
        raw_results = results_payload["results"]

        machines_by_vmid: Dict[str, Dict[str, Any]] = {}
        deleted_vmids: set[str] = set()

        for result in raw_results:
            if not isinstance(result, dict):
                continue

            content = result.get("con")
            if not isinstance(content, dict):
                continue

            vmid = str(content.get("vmid", "")).strip()
            if not vmid:
                continue

            action = str(content.get("action", "")).strip()
            result_status = str(content.get("status", "")).strip()
            action_succeeded = result_status in SUCCESS_RESULT_STATUSES

            existing_machine = machines_by_vmid.get(vmid)
            if action == "delete_lxc" and action_succeeded:
                machines_by_vmid.pop(vmid, None)
                deleted_vmids.add(vmid)
                continue

            if vmid in deleted_vmids:
                deleted_vmids.remove(vmid)

            if existing_machine is None:
                if not (action == "create_lxc" and action_succeeded):
                    continue
                existing_machine = {
                    "vmid": vmid,
                    "hostname": str(content.get("hostname", f"ct-{vmid}")).strip() or f"ct-{vmid}",
                    "ip_address": "",
                    "machine_status": "unknown",
                    "cpu": _to_int(content.get("cpu")),
                    "memory_mb": _to_int(content.get("memory_mb")),
                    "disk_gb": _to_int(content.get("disk_gb")),
                    "network": str(content.get("network", "")).strip(),
                    "last_action": "",
                    "last_result_status": "",
                    "last_message": "",
                    "last_updated": "",
                    "request_id": "",
                    "proxmox_node": str(content.get("proxmox_node", "")).strip(),
                    "node_hostname": str(content.get("node_hostname", "")).strip(),
                }

            hostname = str(content.get("hostname", "")).strip()
            if hostname:
                existing_machine["hostname"] = hostname

            ip_address = str(content.get("ip_address", "")).strip()
            if ip_address:
                existing_machine["ip_address"] = ip_address

            if existing_machine.get("cpu") is None:
                existing_machine["cpu"] = _to_int(content.get("cpu"))
            if existing_machine.get("memory_mb") is None:
                existing_machine["memory_mb"] = _to_int(content.get("memory_mb"))
            if existing_machine.get("disk_gb") is None:
                existing_machine["disk_gb"] = _to_int(content.get("disk_gb"))
            if not str(existing_machine.get("network", "")).strip():
                existing_machine["network"] = str(content.get("network", "")).strip()

            if action_succeeded:
                explicit_machine_status = str(content.get("machine_status", "")).strip().lower()
                if explicit_machine_status:
                    existing_machine["machine_status"] = explicit_machine_status
                elif action in {"create_lxc", "reboot_lxc"}:
                    existing_machine["machine_status"] = "running"
                elif action == "shutdown_lxc":
                    existing_machine["machine_status"] = "stopped"

            if action == "migrate_lxc" and action_succeeded:
                migrated_target_node = str(
                    content.get("target_proxmox_node", "")
                    or content.get("migration_target_proxmox_node", "")
                    or content.get("proxmox_node", "")
                ).strip()
                migrated_target_hostname = str(
                    content.get("target_node_hostname", "")
                    or content.get("migration_target_node_hostname", "")
                    or content.get("node_hostname", "")
                ).strip()
                if migrated_target_node:
                    existing_machine["proxmox_node"] = migrated_target_node
                if migrated_target_hostname:
                    existing_machine["node_hostname"] = migrated_target_hostname

            existing_machine["last_action"] = action
            existing_machine["last_result_status"] = result_status
            existing_machine["last_message"] = str(content.get("message", "")).strip()
            existing_machine["last_updated"] = str(content.get("timestamp", result.get("ct", ""))).strip()
            existing_machine["request_id"] = str(content.get("request_id", "")).strip()
            if action != "migrate_lxc" or not action_succeeded:
                existing_machine["proxmox_node"] = str(content.get("proxmox_node", existing_machine.get("proxmox_node", ""))).strip()
                existing_machine["node_hostname"] = str(content.get("node_hostname", existing_machine.get("node_hostname", ""))).strip()
            existing_machine["shell_ready"] = (
                existing_machine["machine_status"] == "running"
                and bool(existing_machine.get("ip_address"))
            )

            machines_by_vmid[vmid] = existing_machine

        machines = list(machines_by_vmid.values())
        machines = self._reconcile_machines_with_ae_inventory(machines)
        machines.sort(key=lambda item: (str(item.get("hostname", "")).lower(), str(item.get("vmid", ""))))

        return {
            "success": True,
            "message": "Machine inventory refreshed successfully.",
            "machines": machines,
            "templates": TEMPLATE_SUGGESTIONS,
        }

    def get_machine(self, vmid: str) -> Optional[Dict[str, Any]]:
        inventory = self.list_machines()
        for machine in inventory["machines"]:
            if str(machine.get("vmid", "")).strip() == str(vmid).strip():
                return machine
        return None

    def _client(self) -> OneM2MClient:
        return OneM2MClient(self.config)

    def get_worker_node_labels(self) -> Dict[str, str]:
        return dict(self.config.worker_node_labels)

    def get_worker_proxmox_nodes(self) -> tuple[str, ...]:
        return tuple(self.config.worker_proxmox_nodes)

    def get_worker_node_ae_names(self) -> Dict[str, str]:
        return dict(self.config.worker_node_ae_names)

    def get_allowed_migration_targets(self, source_node: str, vmid: str) -> List[str]:
        if not source_node or not vmid:
            return []

        inventory = self._fetch_worker_inventory_by_node().get(source_node, {})
        machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
        if not isinstance(machines, list):
            return []

        for machine in machines:
            if not isinstance(machine, dict):
                continue
            if str(machine.get("vmid", "")).strip() != str(vmid).strip():
                continue
            allowed_nodes = machine.get("allowed_migration_targets", [])
            if not isinstance(allowed_nodes, list):
                return []
            return [str(node).strip() for node in allowed_nodes if str(node).strip()]

        return []

    def resolve_migration_location(
        self,
        vmid: str,
        target_node: str = "",
        fallback_source_node: str = "",
    ) -> Optional[Dict[str, Any]]:
        vmid = str(vmid).strip()
        if not vmid:
            return None

        target_node = str(target_node).strip()
        fallback_source_node = str(fallback_source_node).strip()
        matches: List[Dict[str, Any]] = []
        inventory_by_node = self._fetch_worker_inventory_by_node()

        for node_name, inventory in inventory_by_node.items():
            machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
            if not isinstance(machines, list):
                continue

            for machine in machines:
                if not isinstance(machine, dict):
                    continue
                if str(machine.get("vmid", "")).strip() != vmid:
                    continue

                allowed_targets = machine.get("allowed_migration_targets", [])
                if not isinstance(allowed_targets, list):
                    allowed_targets = []

                matches.append(
                    {
                        "node": node_name,
                        "node_hostname": str(machine.get("node_hostname", "")).strip()
                        or self.config.worker_node_labels.get(node_name, node_name),
                        "machine": dict(machine),
                        "inventory_ct": str(inventory.get("_content_instance_ct", "")).strip(),
                        "allowed_migration_targets": [
                            str(node).strip() for node in allowed_targets if str(node).strip()
                        ],
                    }
                )

        if not matches:
            return None

        for match in matches:
            if target_node and match["node"] == target_node:
                return match

        for match in matches:
            if target_node and target_node in match.get("allowed_migration_targets", []):
                return match

        for match in matches:
            if fallback_source_node and match["node"] == fallback_source_node:
                return match

        matches.sort(key=lambda item: str(item.get("inventory_ct", "")), reverse=True)
        return matches[0]

    def _fetch_worker_inventory_by_node(self) -> Dict[str, Dict[str, Any]]:
        client = self._client()
        inventories: Dict[str, Dict[str, Any]] = {}

        for node_name in self.get_worker_proxmox_nodes():
            ae_name = str(self.config.worker_node_ae_names.get(node_name, "")).strip()
            if not ae_name:
                continue

            container_path = f"/{self.config.cse_base}/{ae_name}/inventory"
            success, _, content_instance = client.get_latest_content(container_path)
            if not success or not isinstance(content_instance, dict):
                continue

            content = content_instance.get("con")
            if not isinstance(content, dict):
                continue

            reported_node = str(content.get("proxmox_node", "")).strip()
            if reported_node and reported_node != node_name:
                continue

            inventories[node_name] = {
                **content,
                "_content_instance_ct": str(content_instance.get("ct", "")).strip(),
                "_ae_name": ae_name,
            }

        return inventories

    def _fetch_inventory_machines_by_node(self, nodes: set[str]) -> Dict[str, Optional[Dict[str, Dict[str, Any]]]]:
        inventory_by_node = self._fetch_worker_inventory_by_node()
        selected_nodes = nodes or set(inventory_by_node)
        live_by_node: Dict[str, Optional[Dict[str, Dict[str, Any]]]] = {}

        for node_name in sorted(selected_nodes):
            inventory = inventory_by_node.get(node_name)
            if not isinstance(inventory, dict):
                live_by_node[node_name] = None
                continue

            machines = inventory.get("machines", [])
            if not isinstance(machines, list):
                live_by_node[node_name] = {}
                continue

            content_instance_ct = str(inventory.get("_content_instance_ct", "")).strip()
            live_machines: Dict[str, Dict[str, Any]] = {}
            for machine in machines:
                if not isinstance(machine, dict):
                    continue
                vmid = str(machine.get("vmid", "")).strip()
                if not vmid:
                    continue
                live_machine = dict(machine)
                live_machine["_inventory_content_instance_ct"] = content_instance_ct
                live_machines[vmid] = live_machine
            live_by_node[node_name] = live_machines

        return live_by_node

    def list_cluster_managed_machines(self) -> List[Dict[str, Any]]:
        owned_inventory = self.list_machines()
        owned_machines = owned_inventory.get("machines", []) if isinstance(owned_inventory, dict) else []
        if not isinstance(owned_machines, list):
            owned_machines = []

        owned_by_vmid = {
            str(machine.get("vmid", "")).strip(): machine
            for machine in owned_machines
            if str(machine.get("vmid", "")).strip()
        }

        cluster_machines: List[Dict[str, Any]] = []
        inventory_by_node = self._fetch_worker_inventory_by_node()
        for node_name in self.get_worker_proxmox_nodes():
            inventory = inventory_by_node.get(node_name)
            machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
            if not isinstance(machines, list):
                continue
            content_instance_ct = str(inventory.get("_content_instance_ct", "")).strip()

            for live_machine in machines:
                if not isinstance(live_machine, dict):
                    continue

                vmid = str(live_machine.get("vmid", "")).strip()
                if not vmid:
                    continue

                description = str(live_machine.get("description", "")).strip()
                if "oneM2M request_id=" not in description:
                    continue

                metadata = _parse_description_metadata(description)
                owned_machine = owned_by_vmid.get(vmid, {})
                owner_origin = str(live_machine.get("owner_origin", "") or metadata.get("request_owner", "")).strip()
                owner_reply_to = str(live_machine.get("owner_reply_to", "") or metadata.get("reply_to", "")).strip()
                current_client_owns_machine = bool(owned_machine) or owner_origin == self.config.client_origin

                hostname = (
                    str(live_machine.get("hostname", "")).strip()
                    or str(live_machine.get("name", "")).strip()
                    or str(owned_machine.get("hostname", "")).strip()
                    or f"ct-{vmid}"
                )
                memory_mb = (
                    _to_int(live_machine.get("memory_mb"))
                    or _to_int(owned_machine.get("memory_mb"))
                    or int(round((float(live_machine.get("maxmem", 0) or 0) / (1024 ** 2))))
                    or None
                )
                disk_gb = (
                    _to_int(live_machine.get("disk_gb"))
                    or _parse_disk_size_gb(str(live_machine.get("rootfs", "")))
                    or _to_int(owned_machine.get("disk_gb"))
                    or _parse_disk_size_gb("", live_machine.get("maxdisk"))
                )
                cluster_machines.append(
                    {
                        "vmid": vmid,
                        "hostname": hostname,
                        "ip_address": str(owned_machine.get("ip_address", "")).strip(),
                        "machine_status": str(live_machine.get("machine_status", live_machine.get("status", ""))).strip().lower(),
                        "cpu": _to_int(live_machine.get("cpu")) or _to_int(owned_machine.get("cpu")) or _to_int(live_machine.get("cpus")),
                        "memory_mb": memory_mb,
                        "disk_gb": disk_gb,
                        "network": str(live_machine.get("network", "")).strip() or str(owned_machine.get("network", "")).strip(),
                        "proxmox_node": node_name,
                        "node_hostname": str(live_machine.get("node_hostname", "")).strip() or self.config.worker_node_labels.get(node_name, node_name),
                        "description": description,
                        "request_id": str(live_machine.get("request_id", "") or metadata.get("request_id", "")).strip() or str(owned_machine.get("request_id", "")).strip(),
                        "owner_origin": owner_origin,
                        "owner_reply_to": owner_reply_to,
                        "owned_by_current_client": current_client_owns_machine,
                        "allowed_migration_targets": live_machine.get("allowed_migration_targets", []),
                        "_inventory_content_instance_ct": content_instance_ct,
                    }
                )

        deduped_by_vmid: Dict[str, Dict[str, Any]] = {}
        for machine in cluster_machines:
            vmid = str(machine.get("vmid", "")).strip()
            if not vmid:
                continue
            previous = deduped_by_vmid.get(vmid)
            if previous is None:
                deduped_by_vmid[vmid] = machine
                continue
            if str(machine.get("_inventory_content_instance_ct", "")) >= str(previous.get("_inventory_content_instance_ct", "")):
                deduped_by_vmid[vmid] = machine

        cluster_machines = list(deduped_by_vmid.values())
        cluster_machines.sort(
            key=lambda item: (
                str(item.get("proxmox_node", "")),
                str(item.get("hostname", "")).lower(),
                str(item.get("vmid", "")),
            )
        )
        return cluster_machines

    def plan_create_placement(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        requested_cpu = max(1, _to_int(payload.get("cpu")) or 1)
        requested_memory_mb = max(1, _to_int(payload.get("memory_mb")) or 1)
        requested_disk_gb = max(1.0, float(_to_int(payload.get("disk_gb")) or 1))

        inventory_by_node = self._fetch_worker_inventory_by_node()
        reservations_by_node = self._get_recent_placement_reservations_by_node()
        candidates: List[Dict[str, Any]] = []

        for node_name in self.get_worker_proxmox_nodes():
            reasons: List[str] = []

            inventory = inventory_by_node.get(node_name)
            if not isinstance(inventory, dict):
                candidates.append(
                    {
                        "node": node_name,
                        "can_host": False,
                        "reasons": ["AE inventory is unavailable"],
                    }
                )
                continue

            live_containers = inventory.get("machines", [])
            if not isinstance(live_containers, list):
                candidates.append(
                    {
                        "node": node_name,
                        "can_host": False,
                        "reasons": ["AE inventory is malformed"],
                    }
                )
                continue

            total_running_cpu = 0
            total_running_memory_mb = 0
            running_machine_count = 0
            total_machine_count = 0

            for container in live_containers:
                if not isinstance(container, dict):
                    continue
                total_machine_count += 1
                if str(container.get("machine_status", container.get("status", ""))).strip().lower() != "running":
                    continue

                running_machine_count += 1
                total_running_cpu += max(0, _to_int(container.get("cpu")) or _to_int(container.get("cpus")) or 0)
                total_running_memory_mb += max(
                    0,
                    _to_int(container.get("memory_mb"))
                    or int(round((float(container.get("maxmem", 0) or 0) / (1024 ** 2))))
                )

            reservation = reservations_by_node.get(node_name, {})
            reserved_cpu = int(reservation.get("cpu", 0) or 0)
            reserved_memory_mb = int(reservation.get("memory_mb", 0) or 0)
            reserved_disk_gb = float(reservation.get("disk_gb", 0.0) or 0.0)
            reserved_count = int(reservation.get("count", 0) or 0)
            total_running_cpu += reserved_cpu
            total_running_memory_mb += reserved_memory_mb
            running_machine_count += reserved_count
            total_machine_count += reserved_count

            configured_max_cpu = int(self.config.worker_node_max_cpu.get(node_name, 0) or 0)
            configured_max_memory_mb = int(self.config.worker_node_max_memory_mb.get(node_name, 0) or 0)
            node_status = inventory.get("node_status", {}) if isinstance(inventory.get("node_status"), dict) else {}
            cpuinfo = node_status.get("cpuinfo", {}) if isinstance(node_status.get("cpuinfo"), dict) else {}
            memory_status = node_status.get("memory", {}) if isinstance(node_status.get("memory"), dict) else {}

            max_cpu = max(configured_max_cpu, _to_int(cpuinfo.get("cpus")) or 0)
            max_memory_mb = max(
                configured_max_memory_mb,
                int(round((float(memory_status.get("total", 0) or 0) / (1024 ** 2)))) if memory_status.get("total") else 0,
            )

            free_cpu = max_cpu - total_running_cpu if max_cpu > 0 else 0
            free_memory_mb = max_memory_mb - total_running_memory_mb if max_memory_mb > 0 else 0

            if max_cpu <= 0:
                reasons.append("CPU capacity is unknown")
            elif free_cpu < requested_cpu:
                reasons.append(f"needs {requested_cpu} vCPU but only {max(0, free_cpu)} are free")

            if max_memory_mb <= 0:
                reasons.append("memory capacity is unknown")
            elif free_memory_mb < requested_memory_mb:
                reasons.append(f"needs {requested_memory_mb} MB RAM but only {max(0, free_memory_mb)} MB are free")

            available_disk_gb = None
            storage_status = inventory.get("storage", {}) if isinstance(inventory.get("storage"), dict) else {}
            if storage_status:
                available_disk_gb = max(
                    0.0,
                    (float(storage_status.get("avail", 0) or 0) / (1024 ** 3)) - reserved_disk_gb,
                )

            required_disk_gb = requested_disk_gb + CAPACITY_DISK_MARGIN_GB
            if available_disk_gb is None:
                reasons.append("storage status is unavailable")
            elif available_disk_gb < required_disk_gb:
                reasons.append(
                    f"needs {round(required_disk_gb, 1)} GB disk but only {round(max(0.0, available_disk_gb), 1)} GB are free"
                )

            candidates.append(
                {
                    "node": node_name,
                    "can_host": not reasons,
                    "reasons": reasons,
                    "free_cpu": free_cpu,
                    "free_memory_mb": free_memory_mb,
                    "free_disk_gb": available_disk_gb if available_disk_gb is not None else -1.0,
                    "running_machine_count": running_machine_count,
                    "total_machine_count": total_machine_count,
                    "reserved_machine_count": reserved_count,
                }
            )

        feasible_candidates = [candidate for candidate in candidates if candidate["can_host"]]
        feasible_candidates.sort(
            key=lambda candidate: (
                candidate["running_machine_count"],
                candidate["total_machine_count"],
                -candidate["free_memory_mb"],
                -candidate["free_cpu"],
                -candidate["free_disk_gb"],
                candidate["node"],
            )
        )

        return {
            "preferred_node": feasible_candidates[0]["node"] if feasible_candidates else "",
            "candidates": candidates,
        }

    def _get_recent_placement_reservations_by_node(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        reservations_by_node: Dict[str, Dict[str, Any]] = {}

        for item in self.list_placement_reservations():
            content = item.get("con")
            if not isinstance(content, dict):
                continue
            if str(content.get("type", "")).strip() != "placement_reservation":
                continue
            if str(content.get("action", "")).strip() != "create_lxc":
                continue

            created_at = _to_float(content.get("created_at"))
            ttl_seconds = _to_float(content.get("ttl_seconds")) or PLACEMENT_RESERVATION_SECONDS
            if created_at <= 0 or now - created_at > ttl_seconds:
                continue

            node_name = str(content.get("target_proxmox_node", "")).strip()
            if not node_name:
                continue

            bucket = reservations_by_node.setdefault(
                node_name,
                {"count": 0, "cpu": 0, "memory_mb": 0, "disk_gb": 0.0},
            )
            bucket["count"] = int(bucket.get("count", 0) or 0) + 1
            bucket["cpu"] = int(bucket.get("cpu", 0) or 0) + max(0, _to_int(content.get("cpu")) or 0)
            bucket["memory_mb"] = int(bucket.get("memory_mb", 0) or 0) + max(
                0,
                _to_int(content.get("memory_mb")) or 0,
            )
            bucket["disk_gb"] = float(bucket.get("disk_gb", 0.0) or 0.0) + max(
                0.0,
                _to_float(content.get("disk_gb")),
            )

        return reservations_by_node

    def select_preferred_create_node(self) -> str:
        return str(self.plan_create_placement({}).get("preferred_node", "")).strip()

    def _reconcile_machines_with_ae_inventory(self, machines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not machines:
            return machines

        live_by_node = self._fetch_inventory_machines_by_node(set())

        reconciled: List[Dict[str, Any]] = []
        for machine in machines:
            node_name = str(machine.get("proxmox_node", "")).strip()
            vmid = str(machine.get("vmid", "")).strip()
            if not node_name or not vmid:
                reconciled.append(machine)
                continue

            node_inventory = live_by_node.get(node_name)
            if node_inventory is None:
                reconciled.append(machine)
                continue

            live_machine = node_inventory.get(vmid)
            live_node_name = node_name
            if not live_machine:
                for candidate_node, candidate_inventory in live_by_node.items():
                    if not isinstance(candidate_inventory, dict) or candidate_node == node_name:
                        continue
                    candidate_machine = candidate_inventory.get(vmid)
                    if candidate_machine:
                        live_machine = candidate_machine
                        live_node_name = candidate_node
                        break
            if not live_machine:
                continue

            if live_node_name and live_node_name != node_name:
                machine["proxmox_node"] = live_node_name
                machine["node_hostname"] = self.config.worker_node_labels.get(live_node_name, live_node_name)

            last_action = str(machine.get("last_action", "")).strip()
            last_status = str(machine.get("last_result_status", "")).strip()
            last_message = str(machine.get("last_message", "")).strip()
            if (
                last_action == "migrate_lxc"
                and last_status not in SUCCESS_RESULT_STATUSES
                and "does not exist on node" in last_message.lower()
            ):
                machine["last_result_status"] = "completed"
                machine["last_message"] = f"Machine found on {machine.get('proxmox_node', live_node_name)} after inventory refresh."

            live_name = str(live_machine.get("name", "")).strip()
            live_hostname = str(live_machine.get("hostname", "")).strip() or live_name
            if live_hostname:
                machine["hostname"] = live_hostname

            live_ip_address = str(live_machine.get("ip_address", live_machine.get("ip", ""))).strip()
            if live_ip_address:
                machine["ip_address"] = live_ip_address

            live_status = str(live_machine.get("machine_status", live_machine.get("status", ""))).strip().lower()
            if live_status:
                machine["machine_status"] = live_status

            if machine.get("cpu") is None:
                machine["cpu"] = _to_int(live_machine.get("cpu")) or _to_int(live_machine.get("cpus"))
            if machine.get("memory_mb") is None:
                machine["memory_mb"] = _to_int(live_machine.get("memory_mb")) or round((float(live_machine.get("maxmem", 0) or 0) / (1024 ** 2))) or None
            if machine.get("disk_gb") is None:
                machine["disk_gb"] = _to_int(live_machine.get("disk_gb")) or _parse_disk_size_gb("", live_machine.get("maxdisk"))

            for key in ("network", "description", "rootfs", "node_hostname"):
                value = live_machine.get(key)
                if value not in ("", None):
                    machine[key] = value

            machine["_inventory_content_instance_ct"] = str(live_machine.get("_inventory_content_instance_ct", "")).strip()

            machine["shell_ready"] = (
                machine["machine_status"] == "running"
                and bool(str(machine.get("ip_address", "")).strip())
            )
            reconciled.append(machine)

        return reconciled

    def _build_request_payload(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now()
        request_payload: Dict[str, Any] = {
            "request_id": now.strftime(f"req-%Y%m%d-%H%M%S-{uuid.uuid4().hex[:8]}"),
            "action": action,
            "reply_to": self.config.client_results_path,
            "status": "requested",
            "created_by": self.config.client_origin,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        request_payload.update(payload)
        return request_payload
