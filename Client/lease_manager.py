"""Lease lifecycle manager for time-limited EdgeOrch machines."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import Config
from provisioning_service import ProvisioningService, SUCCESS_RESULT_STATUSES


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class LeaseManager:
    """Track machine validity, renewal prompts, and automatic expiry actions."""

    def __init__(self, service: ProvisioningService, config: Config, state_file: Path) -> None:
        self._service = service
        self._config = config
        self._state_file = Path(state_file)
        self._lock = threading.RLock()
        self._leases: Dict[str, Dict[str, Any]] = {}
        self._protected_vmids: set[str] = set()
        self._pending_prompt: Optional[Dict[str, Any]] = None
        self._active_enforcement: Optional[Dict[str, Any]] = None
        self._last_event: Optional[Dict[str, Any]] = None
        self._stop_event = threading.Event()
        self._load_state_locked()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name="EdgeOrchLeaseMonitor", daemon=True)
        self._monitor_thread.start()

    def register_machine(self, machine: Dict[str, Any]) -> None:
        vmid = str(machine.get("vmid", "")).strip()
        if not vmid:
            return

        with self._lock:
            now = time.time()
            existing = self._leases.get(vmid)
            incoming_request_id = str(machine.get("request_id", "")).strip()
            existing_request_id = str((existing or {}).get("request_id", "")).strip()
            if existing is None or (incoming_request_id and incoming_request_id != existing_request_id):
                existing = {
                    "vmid": vmid,
                    "hostname": str(machine.get("hostname", "")).strip() or f"ct-{vmid}",
                    "request_id": incoming_request_id,
                    "created_at": _now_iso(),
                    "created_at_ts": now,
                    "expires_at": now + self._config.machine_lease_seconds,
                    "lease_seconds": self._config.machine_lease_seconds,
                    "renewal_count": 0,
                    "proxmox_node": str(machine.get("proxmox_node", "")).strip(),
                    "node_hostname": str(machine.get("node_hostname", "")).strip(),
                    "ip_address": str(machine.get("ip_address", "")).strip(),
                    "enforcement_disabled": False,
                }
            else:
                existing["hostname"] = str(machine.get("hostname", existing.get("hostname", ""))).strip() or existing.get("hostname", "")
                existing["request_id"] = str(machine.get("request_id", existing.get("request_id", ""))).strip()
                existing["proxmox_node"] = str(machine.get("proxmox_node", existing.get("proxmox_node", ""))).strip()
                existing["node_hostname"] = str(machine.get("node_hostname", existing.get("node_hostname", ""))).strip()
                existing["ip_address"] = str(machine.get("ip_address", existing.get("ip_address", ""))).strip()

            self._leases[vmid] = existing
            self._persist_locked()

    def begin_machine_transfer(self, vmid: str) -> None:
        normalized_vmid = str(vmid).strip()
        if not normalized_vmid:
            return

        with self._lock:
            self._protected_vmids.add(normalized_vmid)
            self._persist_locked()

    def complete_machine_transfer(self, machine: Dict[str, Any]) -> None:
        vmid = str(machine.get("vmid", "")).strip()
        if not vmid:
            return

        with self._lock:
            lease = self._leases.get(vmid)
            if lease:
                lease["hostname"] = str(machine.get("hostname", lease.get("hostname", ""))).strip() or lease.get("hostname", "")
                lease["request_id"] = str(machine.get("request_id", lease.get("request_id", ""))).strip()
                lease["proxmox_node"] = str(machine.get("proxmox_node", lease.get("proxmox_node", ""))).strip()
                lease["node_hostname"] = str(machine.get("node_hostname", lease.get("node_hostname", ""))).strip()
                lease["ip_address"] = str(machine.get("ip_address", lease.get("ip_address", ""))).strip()

            self._protected_vmids.discard(vmid)
            self._persist_locked()

    def cancel_machine_transfer(self, vmid: str) -> None:
        normalized_vmid = str(vmid).strip()
        if not normalized_vmid:
            return

        with self._lock:
            self._protected_vmids.discard(normalized_vmid)
            self._persist_locked()

    def remove_machine(self, vmid: str) -> None:
        normalized_vmid = str(vmid).strip()
        if not normalized_vmid:
            return

        with self._lock:
            self._protected_vmids.discard(normalized_vmid)
            self._leases.pop(normalized_vmid, None)
            if self._pending_prompt and str(self._pending_prompt.get("vmid", "")).strip() == normalized_vmid:
                self._pending_prompt = None
            if self._active_enforcement and str(self._active_enforcement.get("vmid", "")).strip() == normalized_vmid:
                self._active_enforcement = None
            self._persist_locked()

    def annotate_inventory(self, inventory: Dict[str, Any]) -> Dict[str, Any]:
        machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
        if not isinstance(machines, list):
            machines = []

        with self._lock:
            self._reconcile_with_inventory_locked(machines)
            now = time.time()
            for machine in machines:
                vmid = str(machine.get("vmid", "")).strip()
                lease = self._leases.get(vmid)
                if not lease:
                    continue

                machine["lease"] = self._serialize_lease_locked(lease, now)

            inventory["lease_policy"] = {
                "lease_seconds": self._config.machine_lease_seconds,
                "renewal_prompt_seconds": self._config.machine_renewal_prompt_seconds,
            }
            return inventory

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            return {
                "success": True,
                "prompt": self._serialize_prompt_locked(self._pending_prompt),
                "active_enforcement": dict(self._active_enforcement) if self._active_enforcement else None,
                "last_event": dict(self._last_event) if self._last_event else None,
            }

    def decide(self, prompt_id: str, renew: bool) -> Dict[str, Any]:
        with self._lock:
            if not self._pending_prompt or str(self._pending_prompt.get("id", "")).strip() != str(prompt_id).strip():
                raise ValueError("This renewal prompt is no longer available.")

            prompt = dict(self._pending_prompt)
            self._pending_prompt = None

            if renew:
                vmid = str(prompt.get("vmid", "")).strip()
                lease = self._leases.get(vmid)
                if not lease:
                    raise ValueError("The machine linked to this renewal no longer exists.")

                now = time.time()
                lease["expires_at"] = now + self._config.machine_lease_seconds
                lease["renewal_count"] = int(lease.get("renewal_count", 0) or 0) + 1
                lease["last_renewed_at"] = _now_iso()
                lease["enforcement_disabled"] = False
                self._last_event = {
                    "id": f"lease-{uuid.uuid4().hex[:10]}",
                    "vmid": vmid,
                    "hostname": str(lease.get("hostname", "")).strip(),
                    "status": "renewed",
                    "message": f"Lease for {str(lease.get('hostname', '')).strip() or vmid} renewed for another {self._config.machine_lease_seconds // 60} min.",
                    "trigger": "user",
                    "created_at": _now_iso(),
                }
                self._persist_locked()
                return self.get_status()

            self._start_delete_locked(prompt, trigger="decline")
            return self.get_status()

    def is_machine_locked(self, vmid: str) -> bool:
        with self._lock:
            return bool(
                self._active_enforcement
                and str(self._active_enforcement.get("status", "")).strip() == "running"
                and str(self._active_enforcement.get("vmid", "")).strip() == str(vmid).strip()
            )

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            with self._lock:
                self._refresh_locked()

    def _refresh_locked(self) -> None:
        now = time.time()

        if self._active_enforcement:
            return

        if self._pending_prompt:
            prompt_vmid = str(self._pending_prompt.get("vmid", "")).strip()
            lease = self._leases.get(prompt_vmid)
            if not lease:
                self._pending_prompt = None
                self._persist_locked()
                return

            lease_expires_at = float(lease.get("expires_at", 0) or 0)
            self._pending_prompt["expires_at"] = lease_expires_at
            if lease_expires_at <= now:
                prompt = dict(self._pending_prompt)
                self._pending_prompt = None
                self._start_delete_locked(prompt, trigger="timeout")
            return

        for lease in sorted(
            self._leases.values(),
            key=lambda item: (float(item.get("expires_at", 0) or 0), str(item.get("hostname", "")), str(item.get("vmid", ""))),
        ):
            if lease.get("enforcement_disabled"):
                continue

            remaining = float(lease.get("expires_at", 0) or 0) - now
            if remaining <= self._config.machine_renewal_prompt_seconds:
                if remaining <= 0:
                    lease["expires_at"] = now + self._config.machine_renewal_prompt_seconds
                self._pending_prompt = self._build_prompt_locked(lease)
                self._persist_locked()
                return

    def _build_prompt_locked(self, lease: Dict[str, Any]) -> Dict[str, Any]:
        vmid = str(lease.get("vmid", "")).strip()
        hostname = str(lease.get("hostname", "")).strip() or f"ct-{vmid}"
        expires_at = float(lease.get("expires_at", time.time()) or time.time())
        return {
            "id": f"lease-prompt-{uuid.uuid4().hex[:10]}",
            "vmid": vmid,
            "hostname": hostname,
            "proxmox_node": str(lease.get("proxmox_node", "")).strip(),
            "node_hostname": str(lease.get("node_hostname", "")).strip(),
            "expires_at": expires_at,
            "created_at": _now_iso(),
            "timeout_seconds": self._config.machine_renewal_prompt_seconds,
            "renew_seconds": self._config.machine_lease_seconds,
            "message": f"The lease for {hostname} is about to expire. Do you want to renew it for another {self._config.machine_lease_seconds // 60} min?",
        }

    def _serialize_prompt_locked(self, prompt: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not prompt:
            return None

        serialized = dict(prompt)
        serialized["remaining_seconds"] = max(0, int(float(prompt.get("expires_at", time.time())) - time.time()))
        return serialized

    def _serialize_lease_locked(self, lease: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        now_value = time.time() if now is None else now
        vmid = str(lease.get("vmid", "")).strip()
        prompt_active = bool(self._pending_prompt and str(self._pending_prompt.get("vmid", "")).strip() == vmid)
        deleting = bool(self._active_enforcement and str(self._active_enforcement.get("vmid", "")).strip() == vmid)
        expires_at = float(lease.get("expires_at", 0) or 0)
        return {
            "vmid": vmid,
            "expires_at": expires_at,
            "expires_at_iso": datetime.fromtimestamp(expires_at).isoformat(timespec="seconds") if expires_at else "",
            "remaining_seconds": max(0, int(expires_at - now_value)),
            "lease_seconds": int(lease.get("lease_seconds", self._config.machine_lease_seconds) or self._config.machine_lease_seconds),
            "renewal_prompt_seconds": self._config.machine_renewal_prompt_seconds,
            "renewal_count": int(lease.get("renewal_count", 0) or 0),
            "prompt_active": prompt_active,
            "deleting": deleting,
            "enforcement_disabled": bool(lease.get("enforcement_disabled")),
        }

    def _start_delete_locked(self, source: Dict[str, Any], trigger: str) -> None:
        vmid = str(source.get("vmid", "")).strip()
        hostname = str(source.get("hostname", "")).strip() or f"ct-{vmid}"
        self._active_enforcement = {
            "id": f"lease-action-{uuid.uuid4().hex[:10]}",
            "vmid": vmid,
            "hostname": hostname,
            "status": "running",
            "action": "delete",
            "trigger": trigger,
            "message": f"The lease for {hostname} has ended. EdgeOrch is removing the machine automatically.",
            "started_at": _now_iso(),
        }
        worker = threading.Thread(
            target=self._run_delete_job,
            args=(dict(self._active_enforcement),),
            name=f"EdgeOrchLeaseDelete-{vmid}",
            daemon=True,
        )
        worker.start()
        self._persist_locked()

    def _run_delete_job(self, action: Dict[str, Any]) -> None:
        vmid = str(action.get("vmid", "")).strip()
        hostname = str(action.get("hostname", "")).strip()
        trigger = str(action.get("trigger", "")).strip() or "timeout"
        result_payload: Optional[Dict[str, Any]] = None
        completed_status = "deleted"
        completed_message = f"{hostname or vmid} was removed automatically after lease expiry."
        keep_disabled_lease = False

        try:
            operation = self._service.run_machine_action("delete_lxc", vmid, hostname=hostname)
            result_payload = operation.get("result") if isinstance(operation, dict) else None
            success = isinstance(result_payload, dict) and str(result_payload.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
            if success:
                completed_message = str((result_payload or {}).get("message", "")).strip() or completed_message
            else:
                completed_status = "failed"
                completed_message = str((result_payload or {}).get("message", "")).strip() or "Automatic removal of the expired machine failed."
                keep_disabled_lease = True
        except Exception as exc:
            machine = self._service.get_machine(vmid)
            if machine is None:
                completed_message = f"{hostname or vmid} was already gone. The lease was closed."
            else:
                completed_status = "failed"
                completed_message = str(exc)
                keep_disabled_lease = True

        with self._lock:
            if completed_status == "deleted":
                self._leases.pop(vmid, None)
            else:
                lease = self._leases.get(vmid)
                if lease and keep_disabled_lease:
                    lease["enforcement_disabled"] = True

            self._active_enforcement = None
            self._pending_prompt = None
            self._last_event = {
                "id": action.get("id"),
                "vmid": vmid,
                "hostname": hostname,
                "status": completed_status,
                "action": "delete",
                "trigger": trigger,
                "message": completed_message,
                "created_at": _now_iso(),
                "result": result_payload,
            }
            self._persist_locked()

    def _reconcile_with_inventory_locked(self, machines: list[Dict[str, Any]]) -> None:
        live_vmids = {str(machine.get("vmid", "")).strip() for machine in machines if str(machine.get("vmid", "")).strip()}
        stale_vmids = [
            vmid for vmid in self._leases
            if vmid not in live_vmids and vmid not in self._protected_vmids
        ]
        for vmid in stale_vmids:
            self._leases.pop(vmid, None)

        for machine in machines:
            vmid = str(machine.get("vmid", "")).strip()
            lease = self._leases.get(vmid)
            if not lease:
                continue
            lease["hostname"] = str(machine.get("hostname", lease.get("hostname", ""))).strip() or lease.get("hostname", "")
            lease["request_id"] = str(machine.get("request_id", lease.get("request_id", ""))).strip()
            lease["proxmox_node"] = str(machine.get("proxmox_node", lease.get("proxmox_node", ""))).strip()
            lease["node_hostname"] = str(machine.get("node_hostname", lease.get("node_hostname", ""))).strip()
            lease["ip_address"] = str(machine.get("ip_address", lease.get("ip_address", ""))).strip()

        if stale_vmids:
            if self._pending_prompt and str(self._pending_prompt.get("vmid", "")).strip() in stale_vmids:
                self._pending_prompt = None
            if self._active_enforcement and str(self._active_enforcement.get("vmid", "")).strip() in stale_vmids:
                self._active_enforcement = None
            self._persist_locked()

    def _load_state_locked(self) -> None:
        if not self._state_file.exists():
            return

        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return

        leases = payload.get("leases", {})
        if not isinstance(leases, dict):
            return

        parsed: Dict[str, Dict[str, Any]] = {}
        for vmid, lease in leases.items():
            if not isinstance(lease, dict):
                continue
            normalized_vmid = str(vmid).strip()
            if not normalized_vmid:
                continue
            parsed[normalized_vmid] = dict(lease)

        self._leases = parsed

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "leases": self._leases,
        }
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self._state_file.with_name(
            f"{self._state_file.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            last_error: Optional[Exception] = None
            for attempt in range(6):
                try:
                    temp_file.replace(self._state_file)
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
            if last_error:
                raise last_error
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
