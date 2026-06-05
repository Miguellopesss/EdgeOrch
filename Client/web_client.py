"""Local web client for provisioning and managing LXC machines through oneM2M."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from pathlib import Path
from typing import Any, Dict

import paramiko
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app_runtime import get_bundle_dir, get_runtime_dir
from config import load_config
from lease_manager import LeaseManager
from provisioning_service import ProvisioningService, SUCCESS_RESULT_STATUSES, TEMPLATE_SUGGESTIONS
from rebalance_manager import RebalanceManager


BUNDLE_DIR = get_bundle_dir()
RUNTIME_DIR = get_runtime_dir()
TEMPLATES_DIR = BUNDLE_DIR / "templates"
STATIC_DIR = BUNDLE_DIR / "static"
ICON_FILE = BUNDLE_DIR / "Icon.png"
LEASE_STATE_FILE = RUNTIME_DIR / ".machine_leases.json"

config = load_config()
service = ProvisioningService(config)
lease_manager = LeaseManager(service, config, LEASE_STATE_FILE)
rebalance_manager = RebalanceManager(service, config, lease_manager)

app = FastAPI(title="EdgeOrch")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


DEFAULT_CREATE_FORM = {
    "hostname": "ct-demo-01",
    "template": "ubuntu-24.04-ssh-enabled",
    "cpu": 2,
    "memory_mb": 2048,
    "disk_gb": 20,
    "network": "vmbr0",
}

ACTION_MAP = {
    "start": "reboot_lxc",
    "reboot": "reboot_lxc",
    "shutdown": "shutdown_lxc",
    "delete": "delete_lxc",
}


@app.on_event("startup")
def on_startup() -> None:
    ready = service.ensure_ready()
    status = "OK" if ready["success"] else "ERROR"
    print(f"[{status}] {ready['message']}")
    registered_count = register_current_machine_leases()
    if registered_count:
        print(f"[OK] Registered leases for {registered_count} managed machine(s).")


def build_annotated_inventory() -> Dict[str, Any]:
    inventory = service.list_machines()
    ensure_inventory_leases(inventory)
    return lease_manager.annotate_inventory(inventory)


def ensure_inventory_leases(inventory: Dict[str, Any]) -> None:
    machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
    if not isinstance(machines, list):
        return

    for machine in machines:
        if not isinstance(machine, dict):
            continue
        if not str(machine.get("vmid", "")).strip():
            continue
        lease_manager.register_machine(machine)


def register_current_machine_leases() -> int:
    try:
        inventory = service.list_machines()
    except Exception as exc:
        print(f"[WARN] Could not register current machine leases: {exc}")
        return 0

    registered_count = 0
    for machine in inventory.get("machines", []) if isinstance(inventory, dict) else []:
        if not isinstance(machine, dict):
            continue
        if not str(machine.get("vmid", "")).strip():
            continue
        lease_manager.register_machine(machine)
        registered_count += 1
    return registered_count


def register_created_machine_lease(result: Dict[str, Any]) -> None:
    vmid = str(result.get("vmid", "")).strip()
    if not vmid:
        return

    try:
        raw_inventory = service.list_machines()
    except Exception:
        raw_inventory = {"machines": []}

    for machine in raw_inventory.get("machines", []) if isinstance(raw_inventory, dict) else []:
        if isinstance(machine, dict) and str(machine.get("vmid", "")).strip() == vmid:
            lease_manager.register_machine(machine)
            return

    lease_manager.register_machine(
        {
            "vmid": vmid,
            "hostname": str(result.get("hostname", "")).strip() or f"ct-{vmid}",
            "request_id": str(result.get("request_id", "")).strip(),
            "proxmox_node": str(result.get("proxmox_node", "")).strip(),
            "node_hostname": str(result.get("node_hostname", "")).strip(),
            "ip_address": str(result.get("ip_address", "")).strip(),
        }
    )


def wait_for_created_machine_inventory(result: Dict[str, Any]) -> Dict[str, Any]:
    vmid = str(result.get("vmid", "")).strip()
    request_id = str(result.get("request_id", "")).strip()
    inventory: Dict[str, Any] = {"success": True, "message": "Machine inventory is still updating.", "machines": []}
    last_confirmed_inventory_ct = ""
    stable_confirmations = 0

    while True:
        inventory = build_annotated_inventory()
        machines = inventory.get("machines", []) if isinstance(inventory, dict) else []
        for machine in machines:
            if not isinstance(machine, dict):
                continue
            if vmid and str(machine.get("vmid", "")).strip() != vmid:
                continue

            description = str(machine.get("description", "")).strip()
            request_matches = not request_id or request_id in description
            machine_is_running = str(machine.get("machine_status", "")).strip().lower() == "running"
            has_live_inventory_data = bool(description and str(machine.get("rootfs", "")).strip())
            ip_address = str(machine.get("ip_address", "")).strip()
            ssh_port_ready = _is_tcp_port_open(ip_address, 22) if ip_address else False
            if request_matches and machine_is_running and has_live_inventory_data and ssh_port_ready:
                inventory_ct = str(machine.get("_inventory_content_instance_ct", "")).strip()
                if inventory_ct and inventory_ct != last_confirmed_inventory_ct:
                    last_confirmed_inventory_ct = inventory_ct
                    stable_confirmations += 1
                elif not inventory_ct:
                    stable_confirmations += 1

                if stable_confirmations >= 2:
                    return inventory
            else:
                stable_confirmations = 0

        if not vmid:
            return inventory

        time.sleep(1.5)


def _is_tcp_port_open(host: str, port: int, timeout_seconds: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page_title": "EdgeOrch",
            "default_form": DEFAULT_CREATE_FORM,
            "template_suggestions": TEMPLATE_SUGGESTIONS,
            "ct_login_user_hint": config.ct_login_user_hint,
            "ct_login_password_hint": config.ct_login_password_hint,
            "websocket_path": "/ws/terminal",
            "machine_lease_seconds": config.machine_lease_seconds,
            "machine_renewal_prompt_seconds": config.machine_renewal_prompt_seconds,
        },
    )


@app.get("/assets/edgeorch-icon")
async def brand_logo() -> FileResponse:
    return FileResponse(str(ICON_FILE), media_type="image/png")


@app.get("/api/machines")
async def api_list_machines() -> JSONResponse:
    try:
        inventory = await asyncio.to_thread(build_annotated_inventory)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc), "machines": []}, status_code=500)

    return JSONResponse(inventory)


@app.get("/api/leases/status")
async def api_lease_status() -> JSONResponse:
    try:
        status = await asyncio.to_thread(lease_manager.get_status)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(status)


@app.get("/api/rebalance/status")
async def api_rebalance_status() -> JSONResponse:
    try:
        status = await asyncio.to_thread(rebalance_manager.get_status)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(status)


@app.post("/api/machines")
async def api_create_machine(request: Request) -> JSONResponse:
    payload = await request.json()
    try:
        operation = await asyncio.to_thread(service.create_machine, payload if isinstance(payload, dict) else {})
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    result = operation["result"]
    operation_success = str(result.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
    if operation_success:
        inventory = await asyncio.to_thread(wait_for_created_machine_inventory, result)
        await asyncio.to_thread(register_created_machine_lease, result)
        inventory = await asyncio.to_thread(build_annotated_inventory)
    else:
        inventory = await asyncio.to_thread(build_annotated_inventory)
    return JSONResponse(
        {
            "success": operation_success,
            "message": str(result.get("message", "")).strip() or operation["result_message"],
            "operation": operation,
            "inventory": inventory,
            "selected_vmid": str(result.get("vmid", "")).strip(),
        }
    )


@app.post("/api/leases/prompts/{prompt_id}/renew")
async def api_renew_machine_lease(prompt_id: str) -> JSONResponse:
    try:
        status = await asyncio.to_thread(lease_manager.decide, prompt_id, True)
        inventory = await asyncio.to_thread(build_annotated_inventory)
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=409)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "success": True,
            "message": "Machine lease renewed successfully.",
            "inventory": inventory,
            **status,
        }
    )


@app.post("/api/leases/prompts/{prompt_id}/decline")
async def api_decline_machine_lease(prompt_id: str) -> JSONResponse:
    try:
        status = await asyncio.to_thread(lease_manager.decide, prompt_id, False)
        inventory = await asyncio.to_thread(build_annotated_inventory)
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=409)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "success": True,
            "message": "Renewal was declined. EdgeOrch will remove the machine automatically.",
            "inventory": inventory,
            **status,
        }
    )


@app.post("/api/rebalance/proposals/{proposal_id}/accept")
async def api_accept_rebalance_proposal(proposal_id: str) -> JSONResponse:
    try:
        status = await asyncio.to_thread(rebalance_manager.decide, proposal_id, True)
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=409)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "success": True,
            "message": "Migration accepted. EdgeOrch will shut down, move, and validate the machine automatically.",
            **status,
        }
    )


@app.post("/api/rebalance/proposals/{proposal_id}/decline")
async def api_decline_rebalance_proposal(proposal_id: str) -> JSONResponse:
    try:
        status = await asyncio.to_thread(rebalance_manager.decide, proposal_id, False)
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=409)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "success": True,
            "message": "Proposal declined. EdgeOrch will look for another eligible machine if one exists.",
            **status,
        }
    )


@app.post("/api/machines/{vmid}/actions/{action_name}")
async def api_machine_action(vmid: str, action_name: str, request: Request) -> JSONResponse:
    payload = await request.json()
    if action_name not in ACTION_MAP:
        return JSONResponse({"success": False, "message": f"Invalid action: {action_name}"}, status_code=400)

    if rebalance_manager.is_machine_locked(vmid):
        return JSONResponse(
            {
                "success": False,
                "message": "This machine is being migrated right now. Wait for completion before acting on it again.",
            },
            status_code=409,
        )

    if lease_manager.is_machine_locked(vmid):
        return JSONResponse(
            {
                "success": False,
                "message": "This machine lease ended and EdgeOrch is removing it automatically.",
            },
            status_code=409,
        )

    payload_dict = payload if isinstance(payload, dict) else {}
    hostname = str(payload_dict.get("hostname", "")).strip()

    try:
        operation = await asyncio.to_thread(service.run_machine_action, ACTION_MAP[action_name], vmid, hostname)
        if action_name == "delete":
            result = operation.get("result", {}) if isinstance(operation, dict) else {}
            operation_success = str(result.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
            if operation_success:
                await asyncio.to_thread(lease_manager.remove_machine, vmid)
        inventory = await asyncio.to_thread(build_annotated_inventory)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)

    result = operation["result"]
    operation_success = str(result.get("status", "")).strip() in SUCCESS_RESULT_STATUSES
    return JSONResponse(
        {
            "success": operation_success,
            "message": str(result.get("message", "")).strip() or operation["result_message"],
            "operation": operation,
            "inventory": inventory,
            "selected_vmid": "" if action_name == "delete" and operation_success else vmid,
        }
    )


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket) -> None:
    await websocket.accept()

    ssh_client: paramiko.SSHClient | None = None
    channel: paramiko.Channel | None = None
    output_task: asyncio.Task[None] | None = None

    try:
        connect_message = await websocket.receive_json()
        if not isinstance(connect_message, dict) or connect_message.get("type") != "connect":
            await websocket.send_json({"type": "status", "status": "error", "message": "Invalid connection request."})
            return

        vmid = str(connect_message.get("vmid", "")).strip()
        password = str(connect_message.get("password", "")).strip()
        cols = int(connect_message.get("cols", 120) or 120)
        rows = int(connect_message.get("rows", 32) or 32)

        if not vmid:
            await websocket.send_json({"type": "status", "status": "error", "message": "Missing VMID for shell connection."})
            return

        if not password:
            await websocket.send_json({"type": "status", "status": "error", "message": "Provide the root password to open the shell."})
            return

        machine = await asyncio.to_thread(service.get_machine, vmid)
        if machine is None:
            await websocket.send_json({"type": "status", "status": "error", "message": f"Machine {vmid} is no longer available."})
            return

        if rebalance_manager.is_machine_locked(vmid):
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "error",
                    "message": "This machine is being migrated right now. Wait for completion before opening the shell again.",
                }
            )
            return

        if lease_manager.is_machine_locked(vmid):
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "error",
                    "message": "This machine lease ended and EdgeOrch is removing it automatically. Shell access is no longer available.",
                }
            )
            return

        host = str(machine.get("ip_address", "")).strip()
        if not host:
            await websocket.send_json({"type": "status", "status": "error", "message": "This machine does not have an SSH-ready IP yet."})
            return

        last_action = str(machine.get("last_action", "")).strip()
        auth_retry_seconds = 30 if last_action == "migrate_lxc" else 6
        ssh_client = await _connect_ssh_when_ready(host, password, auth_retry_seconds=auth_retry_seconds)

        channel = ssh_client.invoke_shell(term="xterm", width=cols, height=rows)
        channel.settimeout(0.0)

        await websocket.send_json(
            {
                "type": "status",
                "status": "connected",
                "message": f"SSH shell connected to {machine.get('hostname', vmid)} ({host}).",
            }
        )

        output_task = asyncio.create_task(_pump_shell_output(websocket, channel))

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue

            message_type = str(message.get("type", "")).strip()
            if message_type == "input":
                data = str(message.get("data", ""))
                if data and channel is not None:
                    await asyncio.to_thread(channel.send, data)
            elif message_type == "resize" and channel is not None:
                resize_cols = int(message.get("cols", cols) or cols)
                resize_rows = int(message.get("rows", rows) or rows)
                await asyncio.to_thread(channel.resize_pty, resize_cols, resize_rows)
            elif message_type == "close":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - runtime feedback path
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "status", "status": "error", "message": str(exc)})
    finally:
        if output_task is not None:
            output_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await output_task
        if channel is not None:
            with contextlib.suppress(Exception):
                channel.close()
        if ssh_client is not None:
            with contextlib.suppress(Exception):
                ssh_client.close()
        with contextlib.suppress(Exception):
            await websocket.close()


async def _pump_shell_output(websocket: WebSocket, channel: paramiko.Channel) -> None:
    while True:
        if channel.recv_ready():
            data = await asyncio.to_thread(channel.recv, 4096)
            if not data:
                break
            await websocket.send_json({"type": "output", "data": data.decode("utf-8", "replace")})
            continue

        if channel.closed or channel.exit_status_ready():
            break

        await asyncio.sleep(0.03)

    await websocket.send_json({"type": "status", "status": "disconnected", "message": "The SSH session ended."})


async def _connect_ssh_when_ready(
    host: str,
    password: str,
    auth_retry_seconds: int = 6,
) -> paramiko.SSHClient:
    deadline = time.monotonic() + 45
    auth_deadline = time.monotonic() + max(0, auth_retry_seconds)
    last_error: Exception | None = None
    auth_rejected = False

    while True:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            await asyncio.to_thread(
                ssh_client.connect,
                hostname=host,
                port=22,
                username=config.ct_login_user_hint,
                password=password,
                look_for_keys=False,
                allow_agent=False,
                timeout=8,
                banner_timeout=8,
                auth_timeout=8,
            )
            return ssh_client
        except paramiko.AuthenticationException as exc:
            with contextlib.suppress(Exception):
                ssh_client.close()
            last_error = exc
            auth_rejected = True
            if time.monotonic() >= auth_deadline:
                break
            await asyncio.sleep(2)
        except Exception as exc:
            with contextlib.suppress(Exception):
                ssh_client.close()
            last_error = exc
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(3)

    if auth_rejected:
        raise RuntimeError("The SSH server rejected the root password.")

    detail = str(last_error).strip() if last_error else "unknown error"
    raise RuntimeError(f"The machine is running, but SSH did not become ready in time: {detail}")


if __name__ == "__main__":
    uvicorn.run(app, host=config.web_host, port=config.web_port)
