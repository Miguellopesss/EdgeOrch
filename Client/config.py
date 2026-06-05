"""Configuration loader for the provisioning client."""

from dataclasses import dataclass
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from app_runtime import get_bundle_dir, get_runtime_dir


def _load_environment() -> None:
    """Load the closest .env file for the current runtime."""

    explicit_env_file = os.getenv("EDGEORCH_ENV_FILE", "").strip()
    candidate_paths = []
    if explicit_env_file:
        candidate_paths.append(Path(explicit_env_file).expanduser())

    candidate_paths.extend(
        [
            get_runtime_dir() / ".env",
            get_bundle_dir() / ".env",
            Path.cwd() / ".env",
            Path(__file__).resolve().parent / ".env",
        ]
    )

    loaded_paths: set[Path] = set()
    for candidate_path in candidate_paths:
        resolved_path = candidate_path.resolve()
        if resolved_path in loaded_paths or not resolved_path.exists():
            continue
        load_dotenv(dotenv_path=str(resolved_path), override=False)
        loaded_paths.add(resolved_path)


_load_environment()


@dataclass
class Config:
    """Holds the environment configuration required by the client."""

    cse_url: str
    cse_base: str
    provisioning_ae: str
    client_origin: str
    client_ae_name: str
    onem2m_release: str
    notify_ssh_host: str
    notify_ssh_port: int
    notify_ssh_user: str
    notify_ssh_password: str
    notify_remote_bind_host: str
    notify_timeout_seconds: int
    ct_login_user_hint: str
    ct_login_password_hint: str
    web_host: str
    web_port: int
    worker_proxmox_nodes: tuple[str, ...]
    worker_node_labels: dict[str, str]
    worker_node_ae_ips: dict[str, str]
    worker_node_ae_names: dict[str, str]
    worker_node_max_cpu: dict[str, int]
    worker_node_max_memory_mb: dict[str, int]
    machine_lease_seconds: int
    machine_renewal_prompt_seconds: int
    rebalance_min_count_gap: int
    rebalance_min_total_machines: int
    rebalance_proposal_timeout_seconds: int

    @property
    def requests_path(self) -> str:
        return f"/{self.cse_base}/{self.provisioning_ae}/requests"

    @property
    def latest_result_path(self) -> str:
        return f"/{self.cse_base}/{self.provisioning_ae}/results/la"

    @property
    def results_path(self) -> str:
        return f"/{self.cse_base}/{self.provisioning_ae}/results"

    @property
    def rebalance_decisions_path(self) -> str:
        return f"/{self.cse_base}/{self.provisioning_ae}/rebalance_decisions"

    @property
    def placement_reservations_path(self) -> str:
        return f"/{self.cse_base}/{self.provisioning_ae}/placement_reservations"

    @property
    def client_ae_path(self) -> str:
        return f"/{self.cse_base}/{self.client_ae_name}"

    @property
    def client_results_path(self) -> str:
        return f"{self.client_ae_path}/results"

    @property
    def client_results_acp_path(self) -> str:
        return f"{self.client_ae_path}/acp_results"

    @property
    def client_api(self) -> str:
        return "N.org.demo.proxmox.client.provisioning"


def _get_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""

    value = os.getenv(name, "").strip()
    if not value:
        runtime_hint = get_runtime_dir() / ".env"
        raise ValueError(f"Missing environment variable '{name}'. Check {runtime_hint}.")
    return value


def _get_optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_optional_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _get_optional_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return values or default


def _parse_mapping_env(name: str, default: dict[str, str]) -> dict[str, str]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return dict(default)

    parsed: dict[str, str] = {}
    for entry in raw_value.split(","):
        normalized_entry = entry.strip()
        if not normalized_entry or "=" not in normalized_entry:
            continue
        key, value = normalized_entry.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key and normalized_value:
            parsed[normalized_key] = normalized_value

    return parsed or dict(default)


def _parse_int_mapping_env(name: str, default: dict[str, int]) -> dict[str, int]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return dict(default)

    parsed: dict[str, int] = {}
    for entry in raw_value.split(","):
        normalized_entry = entry.strip()
        if not normalized_entry or "=" not in normalized_entry:
            continue
        key, value = normalized_entry.split("=", 1)
        normalized_key = key.strip()
        try:
            normalized_value = int(value.strip())
        except ValueError:
            continue
        if normalized_key and normalized_value > 0:
            parsed[normalized_key] = normalized_value

    return parsed or dict(default)


def _derive_client_ae_name(client_origin: str) -> str:
    normalized = client_origin
    if normalized.startswith("C") and len(normalized) > 1:
        normalized = normalized[1:]

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_")
    if not normalized:
        normalized = "client"

    return f"AE_{normalized}"


def _is_auto_identity_value(value: str) -> bool:
    normalized_value = str(value).strip()
    if not normalized_value:
        return True
    return normalized_value.upper() in {"AUTO", "CHANGE_ME"}


def _sanitize_identity_fragment(value: str, lowercase: bool = False) -> str:
    normalized_value = re.sub(r"[^A-Za-z0-9_-]+", "", str(value or "").strip())
    if lowercase:
        normalized_value = normalized_value.lower()
    return normalized_value


def _derive_runtime_identity_seed() -> str:
    for candidate in (
        os.getenv("USERNAME", "").strip(),
        os.getenv("USER", "").strip(),
        os.getenv("COMPUTERNAME", "").strip(),
    ):
        sanitized_candidate = _sanitize_identity_fragment(candidate)
        if sanitized_candidate:
            return sanitized_candidate
    return "EdgeOrchClient"


def _resolve_client_origin() -> str:
    configured_origin = _get_optional_env("CLIENT_ORIGIN")
    if not _is_auto_identity_value(configured_origin):
        return configured_origin

    identity_seed = _derive_runtime_identity_seed()
    return f"Cclient-provisioning-{_sanitize_identity_fragment(identity_seed, lowercase=True)}"


def _resolve_client_ae_name(client_origin: str) -> str:
    configured_ae_name = _get_optional_env("CLIENT_AE_NAME")
    if not _is_auto_identity_value(configured_ae_name):
        return configured_ae_name.strip("/")

    identity_seed = _derive_runtime_identity_seed()
    sanitized_seed = _sanitize_identity_fragment(identity_seed)
    if sanitized_seed:
        return f"AE_Client_{sanitized_seed}"
    return _derive_client_ae_name(client_origin)


def load_config() -> Config:
    """Load and normalize configuration from the .env file."""

    client_origin = _resolve_client_origin()
    client_ae_name = _resolve_client_ae_name(client_origin)

    default_worker_nodes = ("sdei-mm01", "sdei-mm02")
    default_worker_labels = {
        "sdei-mm01": "AE1",
        "sdei-mm02": "AE2",
    }
    default_worker_ae_ips = {
        "sdei-mm01": "192.168.0.141",
        "sdei-mm02": "192.168.0.142",
    }
    default_worker_ae_names = {
        "sdei-mm01": "AE_Proxmox_Monitor_MM01",
        "sdei-mm02": "AE_Proxmox_Monitor",
    }
    default_worker_max_cpu = {
        "sdei-mm01": 4,
        "sdei-mm02": 4,
    }
    default_worker_max_memory_mb = {
        "sdei-mm01": 16384,
        "sdei-mm02": 16384,
    }

    return Config(
        cse_url=_get_env("CSE_URL").rstrip("/"),
        cse_base=_get_env("CSE_BASE").strip("/"),
        provisioning_ae=_get_env("PROVISIONING_AE").strip("/"),
        client_origin=client_origin,
        client_ae_name=client_ae_name.strip("/"),
        onem2m_release=_get_env("ONEM2M_RELEASE"),
        notify_ssh_host=_get_optional_env("NOTIFY_SSH_HOST", "192.168.0.143"),
        notify_ssh_port=int(_get_optional_env("NOTIFY_SSH_PORT", "22") or "22"),
        notify_ssh_user=_get_optional_env("NOTIFY_SSH_USER", "root"),
        notify_ssh_password=_get_optional_env("NOTIFY_SSH_PASSWORD"),
        notify_remote_bind_host=_get_optional_env("NOTIFY_REMOTE_BIND_HOST", "127.0.0.1"),
        notify_timeout_seconds=int(_get_optional_env("NOTIFY_TIMEOUT_SECONDS", "180") or "180"),
        ct_login_user_hint=_get_optional_env("CT_LOGIN_USER_HINT", "root"),
        ct_login_password_hint=_get_optional_env("CT_LOGIN_PASSWORD_HINT", "ubuntu"),
        web_host=_get_optional_env("WEB_HOST", "127.0.0.1"),
        web_port=int(_get_optional_env("WEB_PORT", "8000") or "8000"),
        worker_proxmox_nodes=_parse_csv_env("WORKER_PROXMOX_NODES", default_worker_nodes),
        worker_node_labels=_parse_mapping_env("WORKER_NODE_LABELS", default_worker_labels),
        worker_node_ae_ips=_parse_mapping_env("WORKER_NODE_AE_IPS", default_worker_ae_ips),
        worker_node_ae_names=_parse_mapping_env("WORKER_NODE_AE_NAMES", default_worker_ae_names),
        worker_node_max_cpu=_parse_int_mapping_env("WORKER_NODE_MAX_CPU", default_worker_max_cpu),
        worker_node_max_memory_mb=_parse_int_mapping_env("WORKER_NODE_MAX_MEMORY_MB", default_worker_max_memory_mb),
        machine_lease_seconds=max(60, _get_optional_int_env("MACHINE_LEASE_SECONDS", 300)),
        machine_renewal_prompt_seconds=max(5, _get_optional_int_env("MACHINE_RENEWAL_PROMPT_SECONDS", 15)),
        rebalance_min_count_gap=max(1, _get_optional_int_env("REBALANCE_MIN_COUNT_GAP", 2)),
        rebalance_min_total_machines=max(2, _get_optional_int_env("REBALANCE_MIN_TOTAL_MACHINES", 3)),
        rebalance_proposal_timeout_seconds=max(5, _get_optional_int_env("REBALANCE_PROPOSAL_TIMEOUT_SECONDS", 15)),
    )
