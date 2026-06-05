"""Receive oneM2M subscription notifications through a reverse SSH tunnel."""

from __future__ import annotations

import http.server
import json
import queue
import select
import socket
import socketserver
import threading
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

import paramiko

from config import Config


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class NotificationChannel:
    """Expose a local HTTP endpoint to the CSE via a reverse SSH tunnel."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.token = uuid.uuid4().hex
        self.local_host = "127.0.0.1"
        self.local_port = self._reserve_local_port()
        self.remote_port = self.local_port
        self.notification_path = f"/notify/{self.token}"
        self.notification_url = f"http://{self.config.notify_remote_bind_host}:{self.remote_port}{self.notification_path}"

        self._notifications: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._server: Optional[_ThreadedTCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._ssh_client: Optional[paramiko.SSHClient] = None
        self._transport: Optional[paramiko.Transport] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> Tuple[bool, str]:
        if not self.config.notify_ssh_password:
            return False, "A variavel NOTIFY_SSH_PASSWORD nao esta definida."

        try:
            self._start_http_server()
            self._start_reverse_tunnel()
        except Exception as exc:
            self.close()
            return False, f"Falha ao preparar o canal de notificacao: {exc}"

        return True, f"Canal de notificacao pronto em {self.notification_url}."

    def wait_for_request(self, request_id: str, timeout_seconds: Optional[int] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        timeout = timeout_seconds if timeout_seconds is not None else self.config.notify_timeout_seconds
        deadline = time.time() + timeout

        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                payload = self._notifications.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue

            if request_id in self._extract_request_ids(payload):
                return True, f"Notificacao recebida para o pedido {request_id}.", payload

        return False, f"Nao chegou nenhuma notificacao para o pedido {request_id} dentro do timeout.", None

    def close(self) -> None:
        self._stop_event.set()

        if self._transport is not None:
            try:
                self._transport.cancel_port_forward(self.config.notify_remote_bind_host, self.remote_port)
            except Exception:
                pass

        if self._ssh_client is not None:
            try:
                self._ssh_client.close()
            except Exception:
                pass

        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

        self._transport = None
        self._ssh_client = None
        self._server = None

    def _reserve_local_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.local_host, 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    def _start_http_server(self) -> None:
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                parent._handle_request(self)

            def do_PUT(self) -> None:
                parent._handle_request(self)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._server = _ThreadedTCPServer((self.local_host, self.local_port), Handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def _start_reverse_tunnel(self) -> None:
        self._stop_event.clear()
        self._ssh_client = paramiko.SSHClient()
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh_client.connect(
            hostname=self.config.notify_ssh_host,
            port=self.config.notify_ssh_port,
            username=self.config.notify_ssh_user,
            password=self.config.notify_ssh_password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )

        self._transport = self._ssh_client.get_transport()
        if self._transport is None:
            raise RuntimeError("Nao foi possivel obter o transporte SSH para o tunel.")

        self._transport.request_port_forward(self.config.notify_remote_bind_host, self.remote_port)
        self._accept_thread = threading.Thread(target=self._accept_forwarded_channels, daemon=True)
        self._accept_thread.start()

    def _accept_forwarded_channels(self) -> None:
        transport = self._transport
        if transport is None:
            return

        while not self._stop_event.is_set() and transport.is_active():
            try:
                channel = transport.accept(timeout=1.0)
            except Exception:
                if self._stop_event.is_set():
                    return
                break
            if channel is None:
                continue
            threading.Thread(target=self._bridge_channel, args=(channel,), daemon=True).start()

    def _bridge_channel(self, channel: paramiko.Channel) -> None:
        sock = socket.create_connection((self.local_host, self.local_port))
        try:
            while True:
                readable, _, _ = select.select([channel, sock], [], [], 1.0)
                if channel in readable:
                    data = channel.recv(4096)
                    if not data:
                        break
                    sock.sendall(data)
                if sock in readable:
                    data = sock.recv(4096)
                    if not data:
                        break
                    channel.sendall(data)
        finally:
            sock.close()
            channel.close()

    def _handle_request(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        if handler.path != self.notification_path:
            handler.send_response(404)
            handler.end_headers()
            return

        raw_length = handler.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            length = 0

        body = handler.rfile.read(length) if length > 0 else b""
        text_body = body.decode("utf-8", "replace")

        try:
            parsed_body = json.loads(text_body) if text_body else {}
        except json.JSONDecodeError:
            parsed_body = {"raw_body": text_body}

        self._notifications.put(parsed_body if isinstance(parsed_body, dict) else {"raw_body": parsed_body})

        response_body = b"ok"
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(response_body)))
        handler.end_headers()
        handler.wfile.write(response_body)

    def _extract_request_ids(self, payload: Any) -> Set[str]:
        found: Set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                request_id = node.get("request_id")
                if isinstance(request_id, str) and request_id.strip():
                    found.add(request_id.strip())

                content_instance = node.get("m2m:cin")
                if isinstance(content_instance, dict):
                    content = content_instance.get("con")
                    if isinstance(content, str):
                        try:
                            walk(json.loads(content))
                        except json.JSONDecodeError:
                            pass
                    else:
                        walk(content)

                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return found
