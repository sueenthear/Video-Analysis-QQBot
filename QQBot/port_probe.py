"""Periodic TCP reachability probe for a NapCat WebSocket endpoint."""

from __future__ import annotations

import socket
import threading
from urllib.parse import urlsplit
from typing import Callable


class PortProbe:
    def __init__(
        self,
        ws_url: str,
        on_result: Callable[[bool, str], None],
        interval: float = 5.0,
        timeout: float = 2.0,
    ):
        self.ws_url = ws_url
        self.on_result = on_result
        self.interval = max(1.0, float(interval))
        self.timeout = max(0.2, float(timeout))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def endpoint(ws_url: str) -> tuple[str, int]:
        parsed = urlsplit(ws_url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise ValueError("WS 地址无效，无法解析目标端口")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        return parsed.hostname, port

    def probe_once(self) -> tuple[bool, str]:
        try:
            host, port = self.endpoint(self.ws_url)
            with socket.create_connection((host, port), timeout=self.timeout):
                return True, f"端口可连接：{host}:{port}"
        except (OSError, ValueError) as exc:
            return False, f"端口不可连接：{exc}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="napcat-port-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.timeout + 1)

    def _run(self) -> None:
        try:
            host, port = self.endpoint(self.ws_url)
        except ValueError as exc:
            self.on_result(False, str(exc))
            return
        while not self._stop.is_set():
            try:
                with socket.create_connection((host, port), timeout=self.timeout):
                    self.on_result(True, f"端口可连接：{host}:{port}")
            except OSError as exc:
                self.on_result(False, f"端口不可连接：{host}:{port}（{exc}）")
            self._stop.wait(self.interval)
