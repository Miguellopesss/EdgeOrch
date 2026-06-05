"""Desktop launcher that opens EdgeOrch inside a native app window."""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import urllib.request
from contextlib import closing

import uvicorn

from app_runtime import get_bundle_dir, get_runtime_dir


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return int(probe_socket.getsockname()[1])


def _wait_for_server(url: str, timeout_seconds: int = 20) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return int(response.status) == 200
        except Exception:
            time.sleep(0.25)
    return False


def _start_server() -> tuple[uvicorn.Server, str]:
    runtime_dir = get_runtime_dir()
    os.environ.setdefault("EDGEORCH_RUNTIME_DIR", str(runtime_dir))
    os.environ["WEB_HOST"] = "127.0.0.1"
    os.environ["WEB_PORT"] = str(_find_free_port())

    from web_client import app, config  # imported after runtime env is prepared

    server_config = uvicorn.Config(
        app,
        host=config.web_host,
        port=config.web_port,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(server_config)
    server.install_signal_handlers = lambda: None

    server_thread = threading.Thread(target=server.run, name="EdgeOrchUvicorn", daemon=True)
    server_thread.start()

    base_url = f"http://{config.web_host}:{config.web_port}"
    if not _wait_for_server(base_url):
        raise RuntimeError("EdgeOrch could not start the local web server.")

    return server, base_url


def run_smoke_test() -> int:
    server, base_url = _start_server()
    try:
        if not _wait_for_server(base_url):
            print("smoke-failed")
            return 1
        print(f"smoke-ok {base_url}")
        return 0
    finally:
        server.should_exit = True
        time.sleep(1)


def run_desktop_window() -> int:
    server, base_url = _start_server()
    try:
        import webview
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        server.should_exit = True
        raise RuntimeError(f"pywebview is required for the desktop app: {exc}") from exc

    icon_path = get_bundle_dir() / "Icon.png"
    window = webview.create_window(
        "EdgeOrch",
        base_url,
        width=1440,
        height=960,
        min_size=(1200, 760),
        text_select=True,
    )

    def _shutdown() -> None:
        server.should_exit = True

    window.events.closed += _shutdown
    webview.start()
    time.sleep(1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EdgeOrch desktop launcher")
    parser.add_argument("--smoke-test", action="store_true", help="Start the local server briefly and verify the app responds.")
    args = parser.parse_args()

    if args.smoke_test:
        return run_smoke_test()

    return run_desktop_window()


if __name__ == "__main__":
    raise SystemExit(main())
