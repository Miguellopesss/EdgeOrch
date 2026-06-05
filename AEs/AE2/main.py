import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


CSE_URL = os.getenv("CSE_URL", "").rstrip("/")
CSE_BASE = os.getenv("CSE_BASE", "cse-in").strip("/")
PROVISIONING_AE = os.getenv("PROVISIONING_AE", "AE_Provisioning").strip("/")
AE_NAME = os.getenv("AE_NAME", "AE_Proxmox_Monitor").strip("/")
AE_ORIGIN = os.getenv("AE_ORIGIN", "Cae-proxmox-monitor").strip()
ONEM2M_RELEASE = os.getenv("ONEM2M_RELEASE", "4").strip()

PROXMOX_HOST = os.getenv("PROXMOX_HOST", "").rstrip("/")
PROXMOX_NODE = os.getenv("PROXMOX_NODE", "").strip()
PROXMOX_TOKEN_ID = os.getenv("PROXMOX_TOKEN_ID", "").strip()
PROXMOX_TOKEN_SECRET = os.getenv("PROXMOX_TOKEN_SECRET", "").strip()
PROXMOX_VERIFY_SSL = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"
PROXMOX_TEMPLATE_STORAGE = os.getenv("PROXMOX_TEMPLATE_STORAGE", "local").strip()
PROXMOX_ROOTFS_STORAGE = os.getenv("PROXMOX_ROOTFS_STORAGE", "local-lvm").strip()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
CLAIM_SETTLE_SECONDS = int(os.getenv("CLAIM_SETTLE_SECONDS", "2"))
CREATE_CLAIM_DISCOVERY_SECONDS = float(
    os.getenv("CREATE_CLAIM_DISCOVERY_SECONDS", str(max(POLL_INTERVAL_SECONDS, 1)))
)
CREATE_CLAIM_LOAD_STEP_SECONDS = float(os.getenv("CREATE_CLAIM_LOAD_STEP_SECONDS", "0.75"))
CREATE_CLAIM_MAX_LOAD_DELAY_SECONDS = float(os.getenv("CREATE_CLAIM_MAX_LOAD_DELAY_SECONDS", "6"))
RESULT_IP_TIMEOUT_SECONDS = int(os.getenv("RESULT_IP_TIMEOUT_SECONDS", "120"))
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "300"))
MIGRATION_TIMEOUT_SECONDS = int(os.getenv("MIGRATION_TIMEOUT_SECONDS", "900"))
CAPACITY_DISK_MARGIN_GB = float(os.getenv("CAPACITY_DISK_MARGIN_GB", "1"))
STATE_FILE = os.getenv("STATE_FILE", "/root/ae-python/state.json").strip()

CT_SWAP_MB = int(os.getenv("CT_SWAP_MB", "512"))
CT_UNPRIVILEGED = os.getenv("CT_UNPRIVILEGED", "true").lower() == "true"
CT_ONBOOT = os.getenv("CT_ONBOOT", "true").lower() == "true"
CT_START_AFTER_CREATE = os.getenv("CT_START_AFTER_CREATE", "true").lower() == "true"
CT_FEATURES = os.getenv("CT_FEATURES", "nesting=1").strip()
CT_DEFAULT_PASSWORD = os.getenv("CT_DEFAULT_PASSWORD", "").strip()

LOCAL_HOSTNAME = socket.gethostname()
LOCAL_IP = "127.0.0.1"

REQUESTS_CONTAINER_PATH = f"/{CSE_BASE}/{PROVISIONING_AE}/requests"
CLAIMS_CONTAINER_PATH = f"/{CSE_BASE}/{PROVISIONING_AE}/claims"
RESULTS_CONTAINER_PATH = f"/{CSE_BASE}/{PROVISIONING_AE}/results"
INVENTORY_CONTAINER_NAME = os.getenv("INVENTORY_CONTAINER_NAME", "inventory").strip() or "inventory"
INVENTORY_CONTAINER_PATH = f"/{CSE_BASE}/{AE_NAME}/{INVENTORY_CONTAINER_NAME}"
INVENTORY_ACP_NAME = os.getenv("INVENTORY_ACP_NAME", "acp_inventory_read").strip() or "acp_inventory_read"
INVENTORY_PUBLISH_INTERVAL_SECONDS = max(5, int(os.getenv("INVENTORY_PUBLISH_INTERVAL_SECONDS", "15")))

ONE_M2M_SESSION = requests.Session()
PROXMOX_SESSION = requests.Session()

if not PROXMOX_VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def log_ok(message: str) -> None:
    print(f"[OK] {message}")


def log_error(message: str) -> None:
    print(f"[ERROR] {message}")


def resolve_probe_target() -> tuple[str, int]:
    for candidate in (CSE_URL, PROXMOX_HOST):
        if not candidate:
            continue

        parsed = urllib.parse.urlparse(candidate)
        if not parsed.hostname:
            continue

        if parsed.port:
            return parsed.hostname, parsed.port

        return parsed.hostname, 443 if parsed.scheme == "https" else 80

    return "8.8.8.8", 53


def resolve_local_ip() -> str:
    try:
        host_ips = subprocess.check_output(["hostname", "-I"], text=True).strip().split()
        for host_ip in host_ips:
            if host_ip and not host_ip.startswith("127."):
                return host_ip
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        resolved_ip = socket.gethostbyname(socket.gethostname())
        if resolved_ip and not resolved_ip.startswith("127."):
            return resolved_ip
    except OSError:
        pass

    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe_host, probe_port = resolve_probe_target()
        probe_socket.connect((probe_host, probe_port))
        return str(probe_socket.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe_socket.close()


def ensure_required_config() -> None:
    required = {
        "CSE_URL": CSE_URL,
        "CSE_BASE": CSE_BASE,
        "PROVISIONING_AE": PROVISIONING_AE,
        "AE_NAME": AE_NAME,
        "AE_ORIGIN": AE_ORIGIN,
        "ONEM2M_RELEASE": ONEM2M_RELEASE,
        "PROXMOX_HOST": PROXMOX_HOST,
        "PROXMOX_NODE": PROXMOX_NODE,
        "PROXMOX_TOKEN_ID": PROXMOX_TOKEN_ID,
        "PROXMOX_TOKEN_SECRET": PROXMOX_TOKEN_SECRET,
    }

    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def build_onem2m_headers(resource_type: Optional[int] = None) -> Dict[str, str]:
    headers = {
        "X-M2M-Origin": AE_ORIGIN,
        "X-M2M-RVI": ONEM2M_RELEASE,
        "X-M2M-RI": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    if resource_type is not None:
        headers["Content-Type"] = f"application/json;ty={resource_type}"
    return headers


def onem2m_url(path: str) -> str:
    return f"{CSE_URL}/{path.lstrip('/')}"


def one_m2m_request(method: str, path: str, resource_type: Optional[int] = None, **kwargs: Any) -> Optional[requests.Response]:
    try:
        return ONE_M2M_SESSION.request(
            method,
            onem2m_url(path),
            headers=build_onem2m_headers(resource_type),
            timeout=20,
            **kwargs,
        )
    except requests.RequestException as exc:
        log_error(f"oneM2M request failed for {path}: {exc}")
        return None


def proxmox_headers() -> Dict[str, str]:
    return {
        "Authorization": f"PVEAPIToken={PROXMOX_TOKEN_ID}={PROXMOX_TOKEN_SECRET}",
    }


def proxmox_request(method: str, path: str, **kwargs: Any) -> Optional[requests.Response]:
    try:
        return PROXMOX_SESSION.request(
            method,
            f"{PROXMOX_HOST}/api2/json{path}",
            headers=proxmox_headers(),
            timeout=30,
            verify=PROXMOX_VERIFY_SSL,
            **kwargs,
        )
    except requests.RequestException as exc:
        log_error(f"Proxmox request failed for {path}: {exc}")
        return None


def parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text}


def create_ae() -> bool:
    payload = {
        "m2m:ae": {
            "rn": AE_NAME,
            "api": "N.org.demo.proxmox.node.provisioning",
            "rr": True,
            "srv": [ONEM2M_RELEASE],
        }
    }

    response = one_m2m_request("POST", f"/{CSE_BASE}", resource_type=2, json=payload)
    if response is None:
        return False

    if response.status_code in (200, 201, 409):
        log_ok(f"Node AE ready: {AE_NAME}")
        return True

    response_data = parse_json_response(response)
    debug_message = str(response_data.get("m2m:dbg", ""))
    if response.status_code == 403 and "Originator has already registered" in debug_message:
        log_ok(f"Node AE ready: {AE_NAME}")
        return True

    log_error(f"Failed to create node AE {AE_NAME}: HTTP {response.status_code}")
    log_error(json.dumps(response_data, ensure_ascii=False))
    return False



def get_resource_ri(path: str, wrapper_name: str) -> str:
    resource = retrieve_resource(path)
    if not isinstance(resource, dict):
        return ""
    wrapped = resource.get(wrapper_name)
    if not isinstance(wrapped, dict):
        return ""
    return str(wrapped.get("ri", "")).strip()


def ensure_inventory_read_acp() -> Optional[str]:
    acp_path = f"/{CSE_BASE}/{AE_NAME}/{INVENTORY_ACP_NAME}"
    existing_ri = get_resource_ri(acp_path, "m2m:acp")
    acp_body = {
        "pv": {
            "acr": [
                {"acor": ["all"], "acop": 34},
                {"acor": [AE_ORIGIN], "acop": 63},
            ]
        },
        "pvs": {
            "acr": [
                {"acor": [AE_ORIGIN], "acop": 63},
            ]
        },
    }

    if existing_ri:
        response = one_m2m_request("PUT", acp_path, json={"m2m:acp": acp_body})
        if response is not None and response.status_code not in (200, 201):
            log_error(f"Failed to update inventory ACP: HTTP {response.status_code}")
            log_error(response.text)
        return existing_ri

    response = one_m2m_request(
        "POST",
        f"/{CSE_BASE}/{AE_NAME}",
        resource_type=1,
        json={"m2m:acp": {"rn": INVENTORY_ACP_NAME, **acp_body}},
    )
    if response is None:
        return None
    if response.status_code in (200, 201):
        payload = parse_json_response(response)
        acp = payload.get("m2m:acp", {})
        return str(acp.get("ri", "")).strip() or get_resource_ri(acp_path, "m2m:acp") or None
    if response.status_code == 409:
        return get_resource_ri(acp_path, "m2m:acp") or None

    log_error(f"Failed to create inventory ACP: HTTP {response.status_code}")
    log_error(response.text)
    return None


def ensure_inventory_container() -> bool:
    acp_ri = ensure_inventory_read_acp()
    if not acp_ri:
        return False

    existing_ri = get_resource_ri(INVENTORY_CONTAINER_PATH, "m2m:cnt")
    cnt_create_body = {"acpi": [acp_ri], "mni": 10}
    cnt_update_body = {"acpi": [acp_ri]}
    if existing_ri:
        response = one_m2m_request("PUT", INVENTORY_CONTAINER_PATH, json={"m2m:cnt": cnt_update_body})
        if response is None:
            return False
        if response.status_code in (200, 201):
            log_ok(f"Inventory container ready: {INVENTORY_CONTAINER_PATH}")
            return True
        log_error(f"Failed to update inventory container: HTTP {response.status_code}")
        log_error(response.text)
        return False

    response = one_m2m_request(
        "POST",
        f"/{CSE_BASE}/{AE_NAME}",
        resource_type=3,
        json={"m2m:cnt": {"rn": INVENTORY_CONTAINER_NAME, **cnt_create_body}},
    )
    if response is None:
        return False
    if response.status_code in (200, 201, 409):
        log_ok(f"Inventory container ready: {INVENTORY_CONTAINER_PATH}")
        return True

    log_error(f"Failed to create inventory container: HTTP {response.status_code}")
    log_error(response.text)
    return False


def create_content_instance(container_path: str, content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {
        "m2m:cin": {
            "con": json.dumps(content, ensure_ascii=False)
        }
    }
    response = one_m2m_request("POST", container_path, resource_type=4, json=payload)
    if response is None:
        return None

    if response.status_code not in (200, 201):
        log_error(f"Failed to publish content instance to {container_path}: HTTP {response.status_code}")
        log_error(response.text)
        return None

    return parse_json_response(response)


def discover_content_instance_paths(container_path: str) -> List[str]:
    response = one_m2m_request("GET", f"{container_path}?fu=1&ty=4")
    if response is None or response.status_code != 200:
        if response is not None:
            log_error(f"Discovery failed for {container_path}: HTTP {response.status_code}")
            log_error(response.text)
        return []

    response_data = parse_json_response(response)
    uris = response_data.get("m2m:uril", [])
    return [uri for uri in uris if isinstance(uri, str)]


def retrieve_resource(path: str) -> Optional[Dict[str, Any]]:
    response = one_m2m_request("GET", path)
    if response is None or response.status_code != 200:
        return None
    return parse_json_response(response)


def parse_content_instance_resource(resource_path: str) -> Optional[Dict[str, Any]]:
    resource = retrieve_resource(resource_path)
    if not resource:
        return None

    cin = resource.get("m2m:cin")
    if not isinstance(cin, dict):
        return None

    raw_content = cin.get("con")
    if not isinstance(raw_content, str):
        return None

    try:
        parsed_content = json.loads(raw_content)
    except json.JSONDecodeError:
        return None

    return {
        "path": resource_path,
        "ct": cin.get("ct", ""),
        "ri": cin.get("ri", ""),
        "rn": cin.get("rn", ""),
        "content": parsed_content,
    }


def list_content_instances(container_path: str) -> List[Dict[str, Any]]:
    instances: List[Dict[str, Any]] = []
    for resource_path in discover_content_instance_paths(container_path):
        parsed_instance = parse_content_instance_resource(resource_path)
        if parsed_instance:
            instances.append(parsed_instance)

    instances.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
    return instances


def load_state() -> Dict[str, List[str]]:
    if not os.path.exists(STATE_FILE):
        return {"processed_request_ids": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {"processed_request_ids": []}

    processed_request_ids = state.get("processed_request_ids")
    if not isinstance(processed_request_ids, list):
        processed_request_ids = []

    return {"processed_request_ids": [str(item) for item in processed_request_ids]}


def save_state(state: Dict[str, List[str]]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def mark_request_processed(state: Dict[str, List[str]], request_id: str) -> None:
    processed_request_ids = state.setdefault("processed_request_ids", [])
    if request_id not in processed_request_ids:
        processed_request_ids.append(request_id)
        save_state(state)


def resolve_result_container_path(request_data: Dict[str, Any]) -> str:
    reply_to = str(request_data.get("reply_to", "")).strip()
    if reply_to.startswith(f"/{CSE_BASE}/"):
        return reply_to
    return RESULTS_CONTAINER_PATH


def result_exists_for_request(request_id: str, results_container_path: str) -> bool:
    for result in list_content_instances(results_container_path):
        content = result.get("content", {})
        if isinstance(content, dict) and str(content.get("request_id", "")) == request_id:
            return True
    return False


def claims_for_request(request_id: str) -> List[Dict[str, Any]]:
    matching_claims = []
    for claim in list_content_instances(CLAIMS_CONTAINER_PATH):
        content = claim.get("content", {})
        if isinstance(content, dict) and str(content.get("request_id", "")) == request_id:
            matching_claims.append(claim)
    matching_claims.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
    return matching_claims


def parse_onem2m_timestamp(value: str) -> Optional[float]:
    normalized_value = str(value).strip()
    if not normalized_value:
        return None

    try:
        return datetime.strptime(normalized_value, "%Y%m%dT%H%M%S,%f").timestamp()
    except ValueError:
        return None


def get_resource_age_seconds(resource: Dict[str, Any]) -> Optional[float]:
    created_timestamp = parse_onem2m_timestamp(str(resource.get("ct", "")))
    if created_timestamp is None:
        return None
    return max(0.0, time.time() - created_timestamp)


def claim_belongs_to_us(claim_resource: Dict[str, Any]) -> bool:
    claim_content = claim_resource.get("content", {})
    if not isinstance(claim_content, dict):
        return False

    return (
        str(claim_content.get("ae_name", "")).strip() == AE_NAME
        and str(claim_content.get("node_hostname", "")).strip() == LOCAL_HOSTNAME
        and str(claim_content.get("proxmox_node", "")).strip() == PROXMOX_NODE
    )


def get_oldest_claim_age_seconds(claim_resources: List[Dict[str, Any]]) -> Optional[float]:
    if not claim_resources:
        return None

    claim_ages = [get_resource_age_seconds(claim_resource) for claim_resource in claim_resources]
    valid_claim_ages = [claim_age for claim_age in claim_ages if claim_age is not None]
    if not valid_claim_ages:
        return None
    return min(valid_claim_ages)


def is_managed_guest_config(config: Dict[str, Any]) -> bool:
    description = str(config.get("description", ""))
    return "oneM2M request_id=" in description


def get_local_managed_guest_metrics() -> Dict[str, int]:
    metrics = {
        "managed_ct_count": 0,
        "managed_running_ct_count": 0,
        "managed_running_vcpu_total": 0,
        "managed_running_memory_mb_total": 0,
    }

    for container in list_lxc_containers():
        vmid = str(container.get("vmid", "")).strip()
        if not vmid:
            continue

        container_config = get_lxc_config(vmid)
        if not container_config or not is_managed_guest_config(container_config):
            continue

        metrics["managed_ct_count"] += 1
        container_status = str(container.get("status", "")).lower().strip()
        if container_status != "running":
            continue

        metrics["managed_running_ct_count"] += 1
        metrics["managed_running_vcpu_total"] += int(container_config.get("cores", 0) or 0)
        metrics["managed_running_memory_mb_total"] += int(container_config.get("memory", 0) or 0)

    return metrics


def get_create_claim_load_units(metrics: Dict[str, int]) -> int:
    return (metrics["managed_running_ct_count"] * 2) + metrics["managed_ct_count"]


def get_create_claim_ready_delay_seconds() -> float:
    metrics = get_local_managed_guest_metrics()
    load_units = get_create_claim_load_units(metrics)
    load_delay_seconds = min(CREATE_CLAIM_MAX_LOAD_DELAY_SECONDS, load_units * CREATE_CLAIM_LOAD_STEP_SECONDS)
    return CREATE_CLAIM_DISCOVERY_SECONDS + load_delay_seconds


def get_claim_priority_sort_key(claim_resource: Dict[str, Any]) -> tuple[Any, ...]:
    claim_content = claim_resource.get("content", {})
    if not isinstance(claim_content, dict):
        claim_content = {}

    return (
        int(claim_content.get("claim_load_units", 0) or 0),
        int(claim_content.get("managed_running_ct_count", 0) or 0),
        int(claim_content.get("managed_ct_count", 0) or 0),
        int(claim_content.get("managed_running_vcpu_total", 0) or 0),
        int(claim_content.get("managed_running_memory_mb_total", 0) or 0),
        str(claim_resource.get("ct", "")),
        str(claim_resource.get("ri", "")),
    )


def publish_claim(request_data: Dict[str, Any], request_resource: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = str(request_data.get("request_id", ""))
    claim_payload = {
        "request_id": request_id,
        "request_uri": request_resource.get("path"),
        "hostname": request_data.get("hostname"),
        "ae_name": AE_NAME,
        "ae_origin": AE_ORIGIN,
        "node_hostname": LOCAL_HOSTNAME,
        "node_ip": LOCAL_IP,
        "proxmox_node": PROXMOX_NODE,
        "status": "claimed",
        "timestamp": now(),
    }

    if str(request_data.get("action", "")).strip() == "create_lxc":
        claim_metrics = get_local_managed_guest_metrics()
        claim_payload.update(claim_metrics)
        claim_payload["claim_load_units"] = get_create_claim_load_units(claim_metrics)

    response_data = create_content_instance(CLAIMS_CONTAINER_PATH, claim_payload)
    if response_data is None:
        return None

    log_ok(f"Claim published for request {request_id}")
    return response_data


def is_our_claim_the_winner(request_id: str) -> bool:
    all_claims = claims_for_request(request_id)
    if not all_claims:
        return False

    winner = sorted(all_claims, key=get_claim_priority_sort_key)[0]
    winner_content = winner.get("content", {})
    winner_ae_name = str(winner_content.get("ae_name", ""))
    winner_node = str(winner_content.get("node_hostname", ""))
    return winner_ae_name == AE_NAME and winner_node == LOCAL_HOSTNAME


def proxmox_get_data(path: str) -> Optional[Any]:
    response = proxmox_request("GET", path)
    if response is None:
        return None
    if response.status_code != 200:
        log_error(f"Proxmox GET failed for {path}: HTTP {response.status_code}")
        log_error(response.text)
        return None
    return parse_json_response(response).get("data")


def proxmox_post_data(path: str, data: Dict[str, Any]) -> Optional[Any]:
    response = proxmox_request("POST", path, data=data)
    if response is None:
        return None
    if response.status_code not in (200, 202):
        log_error(f"Proxmox POST failed for {path}: HTTP {response.status_code}")
        log_error(response.text)
        return None
    return parse_json_response(response).get("data")


def get_storage_status(storage_name: str) -> Optional[Dict[str, Any]]:
    if not storage_name:
        return None
    data = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/storage/{storage_name}/status")
    return data if isinstance(data, dict) else None


def get_next_vmid() -> Optional[str]:
    next_vmid = proxmox_get_data("/cluster/nextid")
    return str(next_vmid) if next_vmid is not None else None


def can_host_create_request(request_data: Dict[str, Any]) -> bool:
    requested_disk_gb = int(request_data.get("disk_gb", 0) or 0)
    template_name = str(request_data.get("template", "")).strip()

    if not template_name:
        log_info("Skipping create claim because the request does not include a template.")
        return False

    resolved_template = resolve_template(template_name)
    if not resolved_template:
        log_info(
            f"Skipping create claim on {PROXMOX_NODE} because template '{template_name}' is unavailable here."
        )
        return False

    rootfs_status = get_storage_status(PROXMOX_ROOTFS_STORAGE)
    if rootfs_status is None:
        log_info(
            f"Skipping create claim on {PROXMOX_NODE} because storage '{PROXMOX_ROOTFS_STORAGE}' status is unavailable."
        )
        return False

    available_bytes = int(rootfs_status.get("avail", 0) or 0)
    required_bytes = int((requested_disk_gb + CAPACITY_DISK_MARGIN_GB) * (1024 ** 3))
    if requested_disk_gb > 0 and available_bytes < required_bytes:
        available_gb = round(available_bytes / (1024 ** 3), 1)
        required_gb = round(required_bytes / (1024 ** 3), 1)
        log_info(
            f"Skipping create claim on {PROXMOX_NODE} due to low storage space: avail={available_gb}GB required={required_gb}GB."
        )
        return False

    return True


def list_available_templates() -> List[Dict[str, Any]]:
    data = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/storage/{PROXMOX_TEMPLATE_STORAGE}/content")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("content") == "vztmpl"]


def get_template_alias_map() -> Dict[str, str]:
    # Accept the short names used by the client even when the API token cannot list storage content.
    aliases: Dict[str, str] = {
        "ubuntu-22.04-standard": "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst",
        "ubuntu-24.04-ssh-enabled": "local:vztmpl/ubuntu-24.04-ssh-enabled_amd64.tar.zst",
        "ubuntu-24.04-ssh-enabled_amd64": "local:vztmpl/ubuntu-24.04-ssh-enabled_amd64.tar.zst",
    }

    raw_aliases = os.getenv("PROXMOX_TEMPLATE_ALIASES", "").strip()
    if not raw_aliases:
        return aliases

    try:
        parsed_aliases = json.loads(raw_aliases)
    except ValueError:
        log_error("Ignoring invalid PROXMOX_TEMPLATE_ALIASES value. Expected a JSON object.")
        return aliases

    if not isinstance(parsed_aliases, dict):
        log_error("Ignoring invalid PROXMOX_TEMPLATE_ALIASES value. Expected a JSON object.")
        return aliases

    for template_name, volid in parsed_aliases.items():
        normalized_template_name = str(template_name).lower().strip()
        normalized_volid = str(volid).strip()
        if normalized_template_name and normalized_volid:
            aliases[normalized_template_name] = normalized_volid

    return aliases


def resolve_template(template_name: str) -> Optional[str]:
    if ":" in template_name and "/" in template_name:
        return template_name

    normalized_template_name = template_name.lower().strip()
    configured_alias = get_template_alias_map().get(normalized_template_name)
    if configured_alias:
        log_info(f"Using configured template alias for '{template_name}': {configured_alias}")
        return configured_alias

    matching_templates = []

    for template in list_available_templates():
        volid = str(template.get("volid", ""))
        file_name = volid.split("/")[-1].lower()
        if (
            file_name.startswith(normalized_template_name)
            or normalized_template_name in file_name
            or volid.lower() == normalized_template_name
        ):
            matching_templates.append(volid)

    if not matching_templates:
        log_error(f"No Proxmox template matched '{template_name}' on storage '{PROXMOX_TEMPLATE_STORAGE}'")
        return None

    matching_templates.sort()
    return matching_templates[0]


def wait_for_task(upid: str, timeout_seconds: int) -> Dict[str, Any]:
    return wait_for_task_on_node(PROXMOX_NODE, upid, timeout_seconds)


def wait_for_task_on_node(node_name: str, upid: str, timeout_seconds: int) -> Dict[str, Any]:
    encoded_upid = urllib.parse.quote(upid, safe="")
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        data = proxmox_get_data(f"/nodes/{node_name}/tasks/{encoded_upid}/status")
        if isinstance(data, dict):
            status = str(data.get("status", "")).lower()
            if status == "stopped":
                exit_status = str(data.get("exitstatus", ""))
                if exit_status.upper() == "OK":
                    return {"success": True, "details": data}
                return {"success": False, "details": data}
        time.sleep(2)

    return {"success": False, "details": {"status": "timeout", "exitstatus": "timeout"}}


def build_net0(bridge_name: str) -> str:
    return f"name=eth0,bridge={bridge_name},ip=dhcp,ip6=dhcp,type=veth"


def list_lxc_containers() -> List[Dict[str, Any]]:
    data = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/lxc")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def list_lxc_containers_on_node(node_name: str) -> List[Dict[str, Any]]:
    data = proxmox_get_data(f"/nodes/{node_name}/lxc")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def get_lxc_config(vmid: str) -> Optional[Dict[str, Any]]:
    data = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/config")
    return data if isinstance(data, dict) else None


def get_lxc_config_on_node(node_name: str, vmid: str) -> Optional[Dict[str, Any]]:
    data = proxmox_get_data(f"/nodes/{node_name}/lxc/{vmid}/config")
    return data if isinstance(data, dict) else None


def find_container_record_by_vmid(vmid: str) -> Optional[Dict[str, Any]]:
    return find_container_record_by_vmid_on_node(PROXMOX_NODE, vmid)


def find_container_record_by_vmid_on_node(node_name: str, vmid: str) -> Optional[Dict[str, Any]]:
    for container in list_lxc_containers_on_node(node_name):
        if str(container.get("vmid", "")).strip() == vmid:
            return container
    return None


def get_container_status(vmid: str) -> str:
    return get_container_status_on_node(PROXMOX_NODE, vmid)


def get_container_status_on_node(node_name: str, vmid: str) -> str:
    data = proxmox_get_data(f"/nodes/{node_name}/lxc/{vmid}/status/current")
    if not isinstance(data, dict):
        return ""
    return str(data.get("status", "")).lower().strip()


def wait_for_container_status(vmid: str, expected_status: str, timeout_seconds: int) -> bool:
    return wait_for_container_status_on_node(PROXMOX_NODE, vmid, expected_status, timeout_seconds)


def wait_for_container_status_on_node(node_name: str, vmid: str, expected_status: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        if get_container_status_on_node(node_name, vmid) == expected_status:
            return True
        time.sleep(2)

    return False


def wait_for_container_absence(vmid: str, timeout_seconds: int) -> bool:
    return wait_for_container_absence_on_node(PROXMOX_NODE, vmid, timeout_seconds)


def wait_for_container_absence_on_node(node_name: str, vmid: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        if not find_container_record_by_vmid_on_node(node_name, vmid):
            return True
        time.sleep(2)

    return False


def wait_for_container_presence_on_node(node_name: str, vmid: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        if find_container_record_by_vmid_on_node(node_name, vmid):
            return True
        time.sleep(2)

    return False


def find_existing_container_for_request(request_id: str, hostname: str) -> Optional[str]:
    for container in list_lxc_containers():
        vmid = str(container.get("vmid", ""))
        if not vmid:
            continue

        config = get_lxc_config(vmid)
        if not config:
            continue

        description = str(config.get("description", ""))
        configured_hostname = str(config.get("hostname", ""))

        if request_id and request_id in description:
            return vmid

        if configured_hostname == hostname and request_id in description:
            return vmid

    return None


def create_lxc_container(request_data: Dict[str, Any]) -> Dict[str, Any]:
    request_id = str(request_data["request_id"])
    hostname = str(request_data["hostname"])
    requested_root_password = request_data.get("root_password")

    existing_vmid = find_existing_container_for_request(request_id, hostname)
    if existing_vmid:
        log_info(f"Found existing CT for request {request_id}: VMID {existing_vmid}")
        return {"success": True, "vmid": existing_vmid, "created": False}

    vmid = get_next_vmid()
    if not vmid:
        return {"success": False, "error": "Unable to fetch next Proxmox VMID"}

    ostemplate = resolve_template(str(request_data["template"]))
    if not ostemplate:
        return {"success": False, "error": f"Unable to resolve template '{request_data['template']}'"}

    create_payload: Dict[str, Any] = {
        "vmid": vmid,
        "hostname": hostname,
        "ostemplate": ostemplate,
        "cores": int(request_data["cpu"]),
        "memory": int(request_data["memory_mb"]),
        "swap": CT_SWAP_MB,
        "rootfs": f"{PROXMOX_ROOTFS_STORAGE}:{int(request_data['disk_gb'])}",
        "net0": build_net0(str(request_data["network"])),
        "onboot": 1 if CT_ONBOOT else 0,
        "unprivileged": 1 if CT_UNPRIVILEGED else 0,
        "start": 1 if CT_START_AFTER_CREATE else 0,
        "ostype": "ubuntu",
        "description": (
            f"oneM2M request_id={request_id}; "
            f"request_owner={str(request_data.get('created_by', '')).strip()}; "
            f"reply_to={str(request_data.get('reply_to', '')).strip()}; "
            f"created_by={AE_NAME}; created_at={now()}"
        ),
    }

    if CT_FEATURES:
        create_payload["features"] = CT_FEATURES

    effective_root_password = ""
    if isinstance(requested_root_password, str) and requested_root_password:
        effective_root_password = requested_root_password
    elif CT_DEFAULT_PASSWORD:
        effective_root_password = CT_DEFAULT_PASSWORD

    if effective_root_password:
        create_payload["password"] = effective_root_password

    response = proxmox_request("POST", f"/nodes/{PROXMOX_NODE}/lxc", data=create_payload)
    if response is None:
        return {"success": False, "error": "Proxmox request failed while creating the CT"}

    if response.status_code not in (200, 202):
        response_data = parse_json_response(response)
        proxmox_message = str(response_data.get("message", "")).strip()
        if proxmox_message:
            error_message = f"Proxmox refused CT creation request: HTTP {response.status_code} - {proxmox_message}"
        else:
            error_message = f"Proxmox refused CT creation request: HTTP {response.status_code}"
        log_error(f"Proxmox POST failed for /nodes/{PROXMOX_NODE}/lxc: HTTP {response.status_code}")
        log_error(json.dumps(response_data, ensure_ascii=False))
        return {"success": False, "error": error_message}

    task_upid = parse_json_response(response).get("data")
    if not task_upid:
        return {"success": False, "error": "Proxmox accepted the CT request but did not return a task identifier"}

    log_info(f"CT creation task started for request {request_id}: {task_upid}")
    task_result = wait_for_task(str(task_upid), TASK_TIMEOUT_SECONDS)
    if not task_result["success"]:
        return {"success": False, "error": f"CT creation task failed: {task_result['details']}"}

    return {"success": True, "vmid": vmid, "created": True, "task_upid": str(task_upid)}


def reboot_lxc_container(request_data: Dict[str, Any]) -> Dict[str, Any]:
    vmid = str(request_data.get("vmid", "")).strip()
    if not vmid:
        return {"success": False, "error": "Missing VMID for reboot request"}

    if not find_container_record_by_vmid(vmid):
        return {"success": False, "error": f"CT {vmid} does not exist"}

    current_status = get_container_status(vmid)
    if current_status == "stopped":
        if not ensure_container_running(vmid):
            return {"success": False, "error": f"CT {vmid} is stopped and could not be started."}
        ip_address = get_container_ip(vmid, RESULT_IP_TIMEOUT_SECONDS)
        return {
            "success": True,
            "vmid": vmid,
            "machine_status": "running",
            "ip_address": ip_address,
            "message": "CT estava parado e foi iniciado com sucesso.",
        }

    task_upid = proxmox_post_data(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/status/reboot", {})
    if not task_upid:
        return {"success": False, "error": f"Proxmox refused reboot request for CT {vmid}"}

    task_result = wait_for_task(str(task_upid), 180)
    if not task_result["success"]:
        return {"success": False, "error": f"CT reboot task failed: {task_result['details']}"}

    if not ensure_container_running(vmid):
        return {"success": False, "error": f"CT {vmid} rebooted but did not return to running state."}

    ip_address = get_container_ip(vmid, RESULT_IP_TIMEOUT_SECONDS)
    return {
        "success": True,
        "vmid": vmid,
        "machine_status": "running",
        "ip_address": ip_address,
        "message": "CT reiniciado com sucesso.",
    }


def shutdown_lxc_container(request_data: Dict[str, Any]) -> Dict[str, Any]:
    vmid = str(request_data.get("vmid", "")).strip()
    if not vmid:
        return {"success": False, "error": "Missing VMID for shutdown request"}

    if not find_container_record_by_vmid(vmid):
        return {"success": False, "error": f"CT {vmid} does not exist"}

    current_status = get_container_status(vmid)
    if current_status == "stopped":
        return {
            "success": True,
            "vmid": vmid,
            "machine_status": "stopped",
            "message": "CT ja estava parado.",
        }

    task_upid = proxmox_post_data(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/status/shutdown", {})
    if not task_upid:
        return {"success": False, "error": f"Proxmox refused shutdown request for CT {vmid}"}

    task_result = wait_for_task(str(task_upid), 180)
    if not task_result["success"]:
        return {"success": False, "error": f"CT shutdown task failed: {task_result['details']}"}

    if not wait_for_container_status(vmid, "stopped", 120):
        return {"success": False, "error": f"CT {vmid} did not reach the stopped state within the timeout."}

    return {
        "success": True,
        "vmid": vmid,
        "machine_status": "stopped",
        "message": "CT desligado com sucesso.",
    }


def delete_lxc_container(request_data: Dict[str, Any]) -> Dict[str, Any]:
    vmid = str(request_data.get("vmid", "")).strip()
    if not vmid:
        return {"success": False, "error": "Missing VMID for delete request"}

    if not find_container_record_by_vmid(vmid):
        return {
            "success": True,
            "vmid": vmid,
            "machine_status": "deleted",
            "deleted": True,
            "message": "CT ja nao existia.",
        }

    current_status = get_container_status(vmid)
    if current_status == "running":
        stop_task_upid = proxmox_post_data(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/status/stop", {})
        if not stop_task_upid:
            return {"success": False, "error": f"Unable to stop CT {vmid} before deletion."}

        stop_task_result = wait_for_task(str(stop_task_upid), 180)
        if not stop_task_result["success"]:
            return {"success": False, "error": f"CT stop task failed before deletion: {stop_task_result['details']}"}

        if not wait_for_container_status(vmid, "stopped", 120):
            return {"success": False, "error": f"CT {vmid} did not stop before deletion."}

    response = proxmox_request(
        "DELETE",
        f"/nodes/{PROXMOX_NODE}/lxc/{vmid}",
        params={"purge": 1, "destroy-unreferenced-disks": 1},
    )
    if response is None:
        return {"success": False, "error": f"Proxmox request failed while deleting CT {vmid}"}

    if response.status_code not in (200, 202):
        response_data = parse_json_response(response)
        proxmox_message = str(response_data.get("message", "")).strip()
        if proxmox_message:
            error_message = f"Proxmox refused CT deletion request: HTTP {response.status_code} - {proxmox_message}"
        else:
            error_message = f"Proxmox refused CT deletion request: HTTP {response.status_code}"
        log_error(f"Proxmox DELETE failed for /nodes/{PROXMOX_NODE}/lxc/{vmid}: HTTP {response.status_code}")
        log_error(json.dumps(response_data, ensure_ascii=False))
        return {"success": False, "error": error_message}

    task_upid = parse_json_response(response).get("data")
    if not task_upid:
        return {"success": False, "error": f"Proxmox accepted CT deletion for {vmid} but did not return a task identifier"}

    task_result = wait_for_task(str(task_upid), TASK_TIMEOUT_SECONDS)
    if not task_result["success"]:
        return {"success": False, "error": f"CT deletion task failed: {task_result['details']}"}

    if not wait_for_container_absence(vmid, 120):
        return {"success": False, "error": f"CT {vmid} still exists after the deletion task completed."}

    return {
        "success": True,
        "vmid": vmid,
        "machine_status": "deleted",
        "deleted": True,
        "message": "CT apagado com sucesso.",
    }


def get_allowed_migration_targets(vmid: str) -> List[str]:
    data = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/migrate")
    if not isinstance(data, dict):
        return []

    allowed_nodes = data.get("allowed-nodes", [])
    if not isinstance(allowed_nodes, list):
        return []

    return [str(node).strip() for node in allowed_nodes if str(node).strip()]


def migrate_lxc_container(request_data: Dict[str, Any]) -> Dict[str, Any]:
    vmid = str(request_data.get("vmid", "")).strip()
    target_node = str(request_data.get("migration_target_proxmox_node", "")).strip()
    target_node_hostname = str(request_data.get("migration_target_node_hostname", "")).strip()
    if not vmid:
        return {"success": False, "error": "Missing VMID for migration request"}
    if not target_node:
        return {"success": False, "error": "Missing target Proxmox node for migration request"}
    if target_node == PROXMOX_NODE:
        return {"success": False, "error": "The target Proxmox node is the same as the current node."}

    if not find_container_record_by_vmid_on_node(PROXMOX_NODE, vmid):
        return {"success": False, "error": f"CT {vmid} does not exist on node {PROXMOX_NODE}"}

    allowed_targets = get_allowed_migration_targets(vmid)
    if target_node not in allowed_targets:
        return {
            "success": False,
            "error": f"CT {vmid} cannot be migrated from {PROXMOX_NODE} to {target_node}.",
        }

    source_status = get_container_status_on_node(PROXMOX_NODE, vmid)
    was_running = source_status == "running"
    if was_running:
        shutdown_result = shutdown_lxc_container(request_data)
        if not shutdown_result["success"]:
            return {
                "success": False,
                "error": f"Unable to stop CT {vmid} before migration: {shutdown_result['error']}",
            }

    migrate_task_upid = proxmox_post_data(
        f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/migrate",
        {"target": target_node},
    )
    if not migrate_task_upid:
        return {"success": False, "error": f"Proxmox refused migration request for CT {vmid}"}

    migrate_task_result = wait_for_task_on_node(PROXMOX_NODE, str(migrate_task_upid), MIGRATION_TIMEOUT_SECONDS)
    if not migrate_task_result["success"]:
        return {"success": False, "error": f"CT migration task failed: {migrate_task_result['details']}"}

    if not wait_for_container_absence_on_node(PROXMOX_NODE, vmid, 180):
        return {"success": False, "error": f"CT {vmid} still exists on {PROXMOX_NODE} after migration."}

    if not wait_for_container_presence_on_node(target_node, vmid, 180):
        return {"success": False, "error": f"CT {vmid} did not appear on {target_node} after migration."}

    machine_status = "stopped"
    ip_address: Optional[str] = None

    if was_running:
        if not ensure_container_running_on_node(target_node, vmid):
            return {
                "success": False,
                "error": f"CT {vmid} migrated to {target_node} but could not be started there.",
            }
        machine_status = "running"
        ip_address = get_container_ip_on_node(target_node, vmid, RESULT_IP_TIMEOUT_SECONDS)

    return {
        "success": True,
        "vmid": vmid,
        "machine_status": machine_status,
        "ip_address": ip_address,
        "target_proxmox_node": target_node,
        "target_node_hostname": target_node_hostname,
        "source_proxmox_node": PROXMOX_NODE,
        "source_node_hostname": LOCAL_HOSTNAME,
        "message": "CT migrado com sucesso.",
    }


def ensure_container_running(vmid: str) -> bool:
    return ensure_container_running_on_node(PROXMOX_NODE, vmid)


def ensure_container_running_on_node(node_name: str, vmid: str) -> bool:
    current_status = proxmox_get_data(f"/nodes/{node_name}/lxc/{vmid}/status/current")
    if isinstance(current_status, dict) and str(current_status.get("status", "")).lower() == "running":
        return True

    task_upid = proxmox_post_data(f"/nodes/{node_name}/lxc/{vmid}/status/start", {})
    if not task_upid:
        current_status = proxmox_get_data(f"/nodes/{node_name}/lxc/{vmid}/status/current")
        return isinstance(current_status, dict) and str(current_status.get("status", "")).lower() == "running"

    task_result = wait_for_task_on_node(node_name, str(task_upid), 120)
    if not task_result["success"]:
        log_error(f"Failed to start CT {vmid} on {node_name}: {task_result['details']}")
        return False

    return True


def get_container_ip(vmid: str, timeout_seconds: int) -> Optional[str]:
    return get_container_ip_on_node(PROXMOX_NODE, vmid, timeout_seconds)


def get_container_ip_on_node(node_name: str, vmid: str, timeout_seconds: int) -> Optional[str]:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        interfaces = proxmox_get_data(f"/nodes/{node_name}/lxc/{vmid}/interfaces")
        if isinstance(interfaces, list):
            for interface in interfaces:
                if not isinstance(interface, dict):
                    continue
                for address in interface.get("ip-addresses", []):
                    if not isinstance(address, dict):
                        continue
                    if address.get("ip-address-type") != "inet":
                        continue
                    ip_address = str(address.get("ip-address", ""))
                    if ip_address and not ip_address.startswith("127."):
                        return ip_address
        time.sleep(3)

    return None



def parse_property_map(value: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for part in str(value or "").split(","):
        if "=" not in part:
            continue
        key, item_value = part.split("=", 1)
        key = key.strip()
        item_value = item_value.strip()
        if key:
            parsed[key] = item_value
    return parsed


def parse_description_metadata(description: str) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for part in str(description or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            metadata[key] = value
    return metadata


def parse_disk_size_gb(rootfs_value: str, fallback_bytes: Any = None) -> Optional[float]:
    import re
    match = re.search(r"size=(?P<value>\d+(?:\.\d+)?)(?P<unit>[KMGTP])", str(rootfs_value or ""), re.IGNORECASE)
    if match:
        size_value = float(match.group("value"))
        unit = match.group("unit").upper()
        multipliers = {"K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024 * 1024}
        return round(size_value * multipliers[unit], 1)
    try:
        bytes_value = int(float(str(fallback_bytes or "0")))
    except ValueError:
        bytes_value = 0
    if bytes_value > 0:
        return round(bytes_value / (1024 ** 3), 1)
    return None


def build_inventory_machine(container: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    vmid = str(container.get("vmid", "")).strip()
    if not vmid:
        return None

    config = get_lxc_config(vmid) or {}
    description = str(config.get("description", "")).strip()
    if not is_managed_guest_config(config):
        return None

    metadata = parse_description_metadata(description)
    net0 = parse_property_map(str(config.get("net0", "")))
    hostname = str(config.get("hostname", "")).strip() or str(container.get("name", "")).strip() or f"ct-{vmid}"
    return {
        "vmid": vmid,
        "hostname": hostname,
        "name": str(container.get("name", "")).strip(),
        "machine_status": str(container.get("status", "")).strip().lower(),
        "status": str(container.get("status", "")).strip().lower(),
        "cpu": int(config.get("cores", container.get("cpus", 0)) or 0),
        "cpus": int(container.get("cpus", config.get("cores", 0)) or 0),
        "memory_mb": int(config.get("memory", 0) or 0) or round(float(container.get("maxmem", 0) or 0) / (1024 ** 2)) or None,
        "disk_gb": parse_disk_size_gb(str(config.get("rootfs", "")), container.get("maxdisk")),
        "network": net0.get("bridge", ""),
        "description": description,
        "rootfs": str(config.get("rootfs", "")).strip(),
        "request_id": str(metadata.get("request_id", "")).strip(),
        "owner_origin": str(metadata.get("request_owner", metadata.get("created_by", ""))).strip(),
        "owner_reply_to": str(metadata.get("reply_to", "")).strip(),
        "proxmox_node": PROXMOX_NODE,
        "node_hostname": LOCAL_HOSTNAME,
        "node_ip": LOCAL_IP,
        "allowed_migration_targets": get_allowed_migration_targets(vmid),
        "timestamp": now(),
    }


def build_inventory_snapshot() -> Dict[str, Any]:
    machines = []
    for container in list_lxc_containers():
        if not isinstance(container, dict):
            continue
        machine = build_inventory_machine(container)
        if machine:
            machines.append(machine)

    storage_status = get_storage_status(PROXMOX_ROOTFS_STORAGE) or {}
    node_status = proxmox_get_data(f"/nodes/{PROXMOX_NODE}/status")
    if not isinstance(node_status, dict):
        node_status = {}

    running_machines = [machine for machine in machines if str(machine.get("machine_status", "")) == "running"]
    return {
        "schema_version": 1,
        "ae_name": AE_NAME,
        "ae_origin": AE_ORIGIN,
        "node_hostname": LOCAL_HOSTNAME,
        "node_ip": LOCAL_IP,
        "proxmox_node": PROXMOX_NODE,
        "timestamp": now(),
        "machines": machines,
        "summary": {
            "managed_lxc_total": len(machines),
            "managed_lxc_running": len(running_machines),
            "managed_lxc_stopped": len(machines) - len(running_machines),
            "managed_running_vcpu_total": sum(int(machine.get("cpu") or 0) for machine in running_machines),
            "managed_running_memory_mb_total": sum(int(machine.get("memory_mb") or 0) for machine in running_machines),
        },
        "storage": {
            "name": PROXMOX_ROOTFS_STORAGE,
            "avail": storage_status.get("avail"),
            "total": storage_status.get("total"),
            "used": storage_status.get("used"),
        },
        "node_status": node_status,
    }


def publish_inventory_snapshot() -> bool:
    snapshot = build_inventory_snapshot()
    response_data = create_content_instance(INVENTORY_CONTAINER_PATH, snapshot)
    if response_data is None:
        return False
    log_ok(f"Inventory published for {PROXMOX_NODE}: {len(snapshot.get('machines', []))} managed CT(s).")
    return True


def publish_result(request_data: Dict[str, Any], result_payload: Dict[str, Any]) -> bool:
    results_container_path = resolve_result_container_path(request_data)
    response_data = create_content_instance(results_container_path, result_payload)
    if response_data is None:
        log_error(f"Failed to publish result for request {request_data['request_id']}")
        return False

    log_ok(f"Result published for request {request_data['request_id']} to {results_container_path}")
    return True


def build_result_base(request_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": request_data.get("request_id"),
        "action": request_data.get("action"),
        "hostname": request_data.get("hostname"),
        "reply_to": request_data.get("reply_to"),
        "claimed_by": AE_NAME,
        "ae_origin": AE_ORIGIN,
        "node_hostname": LOCAL_HOSTNAME,
        "node_ip": LOCAL_IP,
        "proxmox_node": PROXMOX_NODE,
        "container_type": "ct",
        "timestamp": now(),
    }


def build_failure_result(
    request_data: Dict[str, Any],
    error_message: str,
    vmid: Optional[str] = None,
    ip_address: Optional[str] = None,
    machine_status: Optional[str] = None,
) -> Dict[str, Any]:
    result = build_result_base(request_data)
    result["status"] = "failed"
    result["message"] = error_message
    if vmid:
        result["vmid"] = vmid
    if ip_address:
        result["ip_address"] = ip_address
    if machine_status:
        result["machine_status"] = machine_status
    return result


def build_success_result(request_data: Dict[str, Any], vmid: str, ip_address: Optional[str], created: bool) -> Dict[str, Any]:
    result = build_result_base(request_data)
    result["status"] = "completed" if ip_address else "created_no_ip"
    result["vmid"] = vmid
    result["ip_address"] = ip_address
    result["created"] = created
    result["machine_status"] = "running"
    result["message"] = "CT criado com sucesso." if ip_address else "CT criado mas o IP ainda nao ficou disponivel dentro do timeout."
    return result


def build_machine_action_success_result(
    request_data: Dict[str, Any],
    vmid: str,
    machine_status: str,
    message: str,
    ip_address: Optional[str] = None,
    deleted: bool = False,
) -> Dict[str, Any]:
    result = build_result_base(request_data)
    result["status"] = "completed"
    result["vmid"] = vmid
    result["machine_status"] = machine_status
    result["message"] = message
    if ip_address:
        result["ip_address"] = ip_address
    if deleted:
        result["deleted"] = True
    return result


def build_migration_success_result(
    request_data: Dict[str, Any],
    vmid: str,
    machine_status: str,
    target_proxmox_node: str,
    target_node_hostname: str,
    source_proxmox_node: str,
    source_node_hostname: str,
    message: str,
    ip_address: Optional[str] = None,
) -> Dict[str, Any]:
    result = build_machine_action_success_result(
        request_data,
        vmid,
        machine_status,
        message,
        ip_address=ip_address,
    )
    result["proxmox_node"] = target_proxmox_node
    if target_node_hostname:
        result["node_hostname"] = target_node_hostname
    target_node_ip = str(request_data.get("migration_target_node_ip", "")).strip()
    if target_node_ip:
        result["node_ip"] = target_node_ip
    result["previous_proxmox_node"] = source_proxmox_node
    result["previous_node_hostname"] = source_node_hostname
    result["previous_node_ip"] = LOCAL_IP
    return result


def provision_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    action = str(request_data.get("action", "")).strip()

    if action == "create_lxc":
        required_fields = ["request_id", "hostname", "template", "cpu", "memory_mb", "disk_gb", "network"]
        missing_fields = [field for field in required_fields if field not in request_data]
        if missing_fields:
            return build_failure_result(request_data, f"Missing request fields: {', '.join(missing_fields)}")

        creation_result = create_lxc_container(request_data)
        if not creation_result["success"]:
            return build_failure_result(request_data, str(creation_result["error"]))

        vmid = str(creation_result["vmid"])
        created = bool(creation_result.get("created", False))

        if not ensure_container_running(vmid):
            return build_failure_result(request_data, f"CT {vmid} was created but could not be started.", vmid=vmid)

        ip_address = get_container_ip(vmid, RESULT_IP_TIMEOUT_SECONDS)
        return build_success_result(request_data, vmid, ip_address, created)

    if action == "reboot_lxc":
        vmid = str(request_data.get("vmid", "")).strip()
        if not vmid:
            return build_failure_result(request_data, "Missing request field: vmid")

        reboot_result = reboot_lxc_container(request_data)
        if not reboot_result["success"]:
            return build_failure_result(
                request_data,
                str(reboot_result["error"]),
                vmid=vmid,
                machine_status="unknown",
            )

        return build_machine_action_success_result(
            request_data,
            vmid,
            str(reboot_result.get("machine_status", "running")),
            str(reboot_result.get("message", "CT reiniciado com sucesso.")),
            ip_address=reboot_result.get("ip_address"),
        )

    if action == "shutdown_lxc":
        vmid = str(request_data.get("vmid", "")).strip()
        if not vmid:
            return build_failure_result(request_data, "Missing request field: vmid")

        shutdown_result = shutdown_lxc_container(request_data)
        if not shutdown_result["success"]:
            return build_failure_result(
                request_data,
                str(shutdown_result["error"]),
                vmid=vmid,
                machine_status="unknown",
            )

        return build_machine_action_success_result(
            request_data,
            vmid,
            str(shutdown_result.get("machine_status", "stopped")),
            str(shutdown_result.get("message", "CT desligado com sucesso.")),
        )

    if action == "delete_lxc":
        vmid = str(request_data.get("vmid", "")).strip()
        if not vmid:
            return build_failure_result(request_data, "Missing request field: vmid")

        delete_result = delete_lxc_container(request_data)
        if not delete_result["success"]:
            return build_failure_result(
                request_data,
                str(delete_result["error"]),
                vmid=vmid,
                machine_status="unknown",
            )

        return build_machine_action_success_result(
            request_data,
            vmid,
            str(delete_result.get("machine_status", "deleted")),
            str(delete_result.get("message", "CT apagado com sucesso.")),
            deleted=bool(delete_result.get("deleted", False)),
        )

    if action == "migrate_lxc":
        vmid = str(request_data.get("vmid", "")).strip()
        if not vmid:
            return build_failure_result(request_data, "Missing request field: vmid")

        migration_result = migrate_lxc_container(request_data)
        if not migration_result["success"]:
            return build_failure_result(
                request_data,
                str(migration_result["error"]),
                vmid=vmid,
                machine_status="unknown",
            )

        return build_migration_success_result(
            request_data,
            vmid,
            str(migration_result.get("machine_status", "running")),
            str(migration_result.get("target_proxmox_node", PROXMOX_NODE)),
            str(migration_result.get("target_node_hostname", "")),
            str(migration_result.get("source_proxmox_node", PROXMOX_NODE)),
            str(migration_result.get("source_node_hostname", LOCAL_HOSTNAME)),
            str(migration_result.get("message", "CT migrado com sucesso.")),
            ip_address=migration_result.get("ip_address"),
        )

    return build_failure_result(
        request_data,
        "Unsupported action. Accepted actions are: create_lxc, reboot_lxc, shutdown_lxc, delete_lxc, migrate_lxc.",
    )


def process_pending_requests(state: Dict[str, List[str]]) -> None:
    processed_request_ids = set(state.get("processed_request_ids", []))

    for request_resource in list_content_instances(REQUESTS_CONTAINER_PATH):
        request_data = request_resource.get("content", {})
        if not isinstance(request_data, dict):
            continue

        request_id = str(request_data.get("request_id", "")).strip()
        if not request_id:
            continue

        results_container_path = resolve_result_container_path(request_data)

        if request_id in processed_request_ids:
            continue

        if result_exists_for_request(request_id, results_container_path):
            log_info(f"Request {request_id} already has a published result. Marking as processed.")
            mark_request_processed(state, request_id)
            processed_request_ids.add(request_id)
            continue

        action = str(request_data.get("action", "")).strip()
        target_proxmox_node = str(request_data.get("target_proxmox_node", "")).strip()
        if target_proxmox_node and target_proxmox_node != PROXMOX_NODE:
            log_info(
                f"Request {request_id} targets node {target_proxmox_node}. "
                f"Skipping on {PROXMOX_NODE}."
            )
            continue

        if action == "create_lxc" and not can_host_create_request(request_data):
            log_info(f"Node {PROXMOX_NODE} is not a fit for request {request_id}. Waiting for another AE to claim it.")
            continue

        existing_claims = claims_for_request(request_id)
        if action == "create_lxc":
            our_claim_exists = any(claim_belongs_to_us(claim_resource) for claim_resource in existing_claims)

            if not our_claim_exists:
                request_age_seconds = get_resource_age_seconds(request_resource)
                claim_ready_delay_seconds = get_create_claim_ready_delay_seconds()
                if request_age_seconds is not None and request_age_seconds < claim_ready_delay_seconds:
                    wait_seconds = round(claim_ready_delay_seconds - request_age_seconds, 1)
                    log_info(
                        f"Waiting {wait_seconds}s before publishing a create claim for request {request_id} "
                        f"so less-loaded nodes can compete first."
                    )
                    continue

                log_info(
                    f"Publishing load-aware create claim for request {request_id} on {PROXMOX_NODE}."
                )
                if publish_claim(request_data, request_resource) is None:
                    continue
                time.sleep(CLAIM_SETTLE_SECONDS)
                existing_claims = claims_for_request(request_id)
            else:
                log_info(f"We already published a create claim for request {request_id}. Checking the winner.")

            oldest_claim_age_seconds = get_oldest_claim_age_seconds(existing_claims)
            if oldest_claim_age_seconds is not None and oldest_claim_age_seconds < CLAIM_SETTLE_SECONDS:
                wait_seconds = round(CLAIM_SETTLE_SECONDS - oldest_claim_age_seconds, 1)
                log_info(
                    f"Create claim competition for request {request_id} is still settling for {wait_seconds}s."
                )
                continue
        else:
            if not existing_claims:
                log_info(f"No claim found for request {request_id}. Publishing our claim.")
                if publish_claim(request_data, request_resource) is None:
                    continue
                time.sleep(CLAIM_SETTLE_SECONDS)
            else:
                log_info(f"Request {request_id} already has {len(existing_claims)} claim(s). Checking the winner.")

        if not is_our_claim_the_winner(request_id):
            log_info(f"We are not the winning claim for request {request_id}.")
            continue

        log_ok(f"We won the claim for request {request_id}. Starting CT provisioning.")
        result_payload = provision_request(request_data)
        if publish_result(request_data, result_payload):
            mark_request_processed(state, request_id)
            processed_request_ids.add(request_id)


def main() -> int:
    ensure_required_config()

    global LOCAL_HOSTNAME, LOCAL_IP
    LOCAL_HOSTNAME = socket.gethostname()
    LOCAL_IP = resolve_local_ip()

    log_info("Starting Provisioning AE for Proxmox node")
    log_info(f"CSE base: {CSE_URL}/{CSE_BASE}")
    log_info(f"Provisioning AE path: /{CSE_BASE}/{PROVISIONING_AE}")
    log_info(f"Node AE name: {AE_NAME}")
    log_info(f"Node hostname: {LOCAL_HOSTNAME}")
    log_info(f"Node IP: {LOCAL_IP}")
    log_info(f"Proxmox host: {PROXMOX_HOST}")
    log_info(f"Proxmox node: {PROXMOX_NODE}")

    if not create_ae():
        return 1

    if not ensure_inventory_container():
        return 1

    state = load_state()
    last_inventory_publish = 0.0

    while True:
        try:
            current_time = time.time()
            if current_time - last_inventory_publish >= INVENTORY_PUBLISH_INTERVAL_SECONDS:
                if publish_inventory_snapshot():
                    last_inventory_publish = current_time
            process_pending_requests(state)
        except Exception as exc:
            log_error(f"Unexpected error in polling loop: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log_info("Execution interrupted by user.")
