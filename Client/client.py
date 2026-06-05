"""Interactive client that publishes LXC provisioning requests to oneM2M."""

import json
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Any, Dict, Tuple
import uuid

from config import load_config
from notification_channel import NotificationChannel
from onem2m_client import OneM2MClient


DEFAULTS = {
    "hostname": "ct-demo-01",
    "template": "ubuntu-24.04-ssh-enabled",
    "cpu": 2,
    "memory_mb": 2048,
    "disk_gb": 20,
    "network": "vmbr0",
}

STATE_FILE = Path(__file__).with_name(".client_state.json")
REDACTED_SECRET = "<oculta>"


def prompt_text(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def prompt_int(label: str, default: int) -> int:
    while True:
        value = input(f"{label} [{default}]: ").strip()
        if not value:
            return default

        try:
            parsed_value = int(value)
            if parsed_value <= 0:
                print("Introduza um numero inteiro positivo.")
                continue
            return parsed_value
        except ValueError:
            print("Valor invalido. Introduza um numero inteiro.")


def prompt_root_password(default_password_hint: str) -> Tuple[str, bool]:
    value = getpass(f"password root [Enter para usar '{default_password_hint}']: ")
    if not value:
        return default_password_hint, True
    if value == default_password_hint:
        return value, True
    return value, False


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"last_request_id": "", "request_history": []}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_request_id": "", "request_history": []}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def remember_request(request_id: str) -> None:
    state = load_state()
    history = state.get("request_history", [])
    if not isinstance(history, list):
        history = []

    history.append(request_id)
    state["last_request_id"] = request_id
    state["request_history"] = history[-20:]
    save_state(state)


def sanitize_for_display(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "root_password":
                sanitized[key] = REDACTED_SECRET
            else:
                sanitized[key] = sanitize_for_display(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_for_display(item) for item in value]

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            return json.dumps(sanitize_for_display(parsed), ensure_ascii=False)
        return value

    return value


def build_request_payload(client_origin: str, default_password_hint: str) -> Tuple[Dict[str, Any], bool]:
    now = datetime.now()
    request_suffix = uuid.uuid4().hex[:8]
    root_password, using_default_password = prompt_root_password(default_password_hint)

    payload = {
        "request_id": now.strftime(f"req-%Y%m%d-%H%M%S-{request_suffix}"),
        "action": "create_lxc",
        "hostname": prompt_text("hostname", DEFAULTS["hostname"]),
        "template": prompt_text("template", DEFAULTS["template"]),
        "cpu": prompt_int("cpu", DEFAULTS["cpu"]),
        "memory_mb": prompt_int("memory_mb", DEFAULTS["memory_mb"]),
        "disk_gb": prompt_int("disk_gb", DEFAULTS["disk_gb"]),
        "network": prompt_text("network", DEFAULTS["network"]),
        "reply_to": "",
        "status": "requested",
        "created_by": client_origin,
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not using_default_password:
        payload["root_password"] = root_password

    return payload, using_default_password


def create_request(client: OneM2MClient) -> None:
    print("\nCriacao de pedido de container LXC")
    print("Carregue Enter para usar os valores por defeito.\n")
    print(
        f"Credenciais por defeito do CT: utilizador {client.config.ct_login_user_hint} "
        f"e password {client.config.ct_login_password_hint}.\n"
    )

    payload, using_default_password = build_request_payload(
        client.config.client_origin,
        client.config.ct_login_password_hint,
    )
    payload["reply_to"] = client.config.client_results_path
    print("\nPedido a publicar:")
    print(json.dumps(sanitize_for_display(payload), indent=2, ensure_ascii=False))

    notification_channel = NotificationChannel(client.config)
    subscription_path = ""
    use_notifications = False

    try:
        notify_success, notify_message = notification_channel.start()
        if notify_success:
            print(f"\n{notify_message}")
            subscription_name = f"sub_{str(payload['request_id']).replace('-', '_')}"
            sub_success, sub_message, subscription_path = client.create_results_subscription(
                notification_channel.notification_url,
                subscription_name,
            )
            print(sub_message)
            use_notifications = sub_success
        else:
            print(f"\nAviso: {notify_message}")
    except Exception as exc:
        print(f"\nAviso: falha ao preparar a notificacao por subscription: {exc}")

    try:
        success, message, response_data = client.publish_request(payload)
        print(f"\n{message}")

        if success:
            remember_request(str(payload["request_id"]))

        if success and response_data:
            print("Resposta do CSE:")
            print(json.dumps(sanitize_for_display(response_data), indent=2, ensure_ascii=False))

        if not success:
            return

        if use_notifications:
            print(f"\nA aguardar notificacao do pedido {payload['request_id']}...")
            notified, notified_message, _ = notification_channel.wait_for_request(str(payload["request_id"]))
            print(notified_message)

            if notified:
                result_success, result_message, result = client.get_result_for_request(str(payload["request_id"]))
            else:
                print("A tentar recuperar o resultado diretamente do CSE...")
                result_success, result_message, result = client.wait_for_result(
                    str(payload["request_id"]),
                    timeout_seconds=15,
                    poll_interval_seconds=2,
                )
        else:
            print(f"\nA aguardar resultado do pedido {payload['request_id']} por polling...")
            result_success, result_message, result = client.wait_for_result(str(payload["request_id"]))

        print(result_message)
        if result_success and result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if using_default_password:
                print(
                    f"\nAcesso esperado ao CT: utilizador {client.config.ct_login_user_hint} "
                    f"e password {client.config.ct_login_password_hint}."
                )
            else:
                print(
                    f"\nAcesso esperado ao CT: utilizador {client.config.ct_login_user_hint} "
                    f"e a password definida no pedido."
                )
    finally:
        if subscription_path:
            cleanup_success, cleanup_message = client.delete_resource(subscription_path)
            if not cleanup_success:
                print(f"\nAviso: {cleanup_message}")
        notification_channel.close()


def show_last_request_result(client: OneM2MClient) -> None:
    state = load_state()
    request_id = str(state.get("last_request_id", "")).strip()
    if not request_id:
        print("\nAinda nao existe nenhum pedido local registado neste client.")
        return

    print(f"\nA consultar o resultado do ultimo pedido local: {request_id}")
    success, message, result = client.get_result_for_request(request_id)
    print(message)

    if success and result:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def show_result_by_request_id(client: OneM2MClient) -> None:
    request_id = input("\nrequest_id: ").strip()
    if not request_id:
        print("Introduza um request_id valido.")
        return

    success, message, result = client.get_result_for_request(request_id)
    print(message)

    if success and result:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def show_menu() -> None:
    print("\n=== Client Provisioning oneM2M ===")
    print("1. Criar pedido de CT")
    print("2. Consultar resultado do ultimo pedido deste client")
    print("3. Consultar resultado por request_id")
    print("4. Sair")


def main() -> None:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"Erro de configuracao: {exc}")
        return

    client = OneM2MClient(config)

    success, message = client.ensure_private_result_channel()
    if not success:
        print(f"Erro ao preparar o canal privado do client: {message}")
        return

    print("Configuracao carregada com sucesso.")
    print(message)
    print(f"CSE: {config.cse_url}/{config.cse_base}")
    print(f"Container de pedidos: {config.requests_path}")
    print(f"AE do client: {config.client_ae_path}")
    print(f"Container de resultados: {config.client_results_path}")
    print(f"Credenciais por defeito dos CTs: {config.ct_login_user_hint} / {config.ct_login_password_hint}")

    while True:
        show_menu()
        option = input("Escolha uma opcao: ").strip()

        if option == "1":
            create_request(client)
        elif option == "2":
            show_last_request_result(client)
        elif option == "3":
            show_result_by_request_id(client)
        elif option == "4":
            print("A terminar o client.")
            break
        else:
            print("Opcao invalida. Escolha 1, 2, 3 ou 4.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExecucao interrompida pelo utilizador.")
