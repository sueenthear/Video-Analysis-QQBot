"""NapCat OneBot 11 WebSocket runtime and local configuration."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import websocket


@dataclass
class NapCatConfig:
    ws_url: str = "ws://127.0.0.1:3001/"
    token: str = ""
    reconnect_interval: float = 5.0
    read_timeout: float = 90.0
    heartbeat_interval: int = 30000
    backend: str = "auto"
    listen: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.ws_url.startswith(("ws://", "wss://")):
            raise ValueError("WS 地址必须以 ws:// 或 wss:// 开头")
        if self.reconnect_interval < 1:
            raise ValueError("重连间隔不能小于 1 秒")
        if self.read_timeout <= self.heartbeat_interval / 1000:
            raise ValueError("读超时必须大于心跳间隔（默认心跳 30 秒，建议读超时 90 秒）")
        if self.backend not in {"auto", "napcat", "snowluma"}:
            raise ValueError("backend 必须是 auto、napcat 或 snowluma")
        for entry in self.listen:
            if not (entry.startswith("#") or entry.startswith("*")) or not entry[1:].isdigit():
                raise ValueError(f"白名单格式错误：{entry}，群聊用 #群号，私聊用 *QQ号")

    def validate_listeners(self) -> None:
        self.validate()
        if not self.listen:
            raise ValueError("启动机器人前至少配置一个监听对象：*QQ号 或 #群号")

    def is_watched(self, message_type: str, target_id: int) -> bool:
        prefix = "#" if message_type == "group" else "*" if message_type == "private" else ""
        return bool(prefix) and f"{prefix}{target_id}" in self.listen


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(__file__).resolve().parent / "config.json"

    def load(self) -> NapCatConfig:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return NapCatConfig()
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"读取 NapCat 配置失败：{exc}") from exc
        napcat = raw.get("napcat", raw)
        config = NapCatConfig(
            ws_url=str(napcat.get("ws_url") or "ws://127.0.0.1:3001/"),
            token=str(napcat.get("token") or ""),
            reconnect_interval=float(napcat.get("reconnect_interval", 5)),
            read_timeout=float(napcat.get("read_timeout", 90)),
            heartbeat_interval=int(napcat.get("heartbeat_interval", 30000)),
            backend=str(napcat.get("backend") or "auto").lower(),
            listen=list(raw.get("listen") or []),
        )
        config.validate()
        return config

    def save(self, config: NapCatConfig) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "napcat": {
                "ws_url": config.ws_url,
                "token": config.token,
                "reconnect_interval": config.reconnect_interval,
                "read_timeout": config.read_timeout,
                "heartbeat_interval": config.heartbeat_interval,
                "backend": config.backend,
            },
            "listen": config.listen,
        }
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


class NapCatRuntime:
    """Background OneBot WS manager with echo-matched API calls and event delivery."""

    def __init__(
        self,
        config: NapCatConfig,
        on_status: Callable[[str, str], None],
        on_log: Callable[[str], None],
        on_event: Callable[[dict], None] | None = None,
        reconnect: bool = True,
    ):
        self.config = config
        self.on_status = on_status
        self.on_log = on_log
        self.on_event = on_event
        self.reconnect = reconnect
        self._stop = threading.Event()
        self._suppress_final_status = False
        self._final_status: tuple[str, str] | None = None
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._ws = None
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self.self_id: int | None = None
        self.self_nickname = ""
        self.detected_backend = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, require_listeners: bool = True) -> None:
        if self.running:
            return
        self.config.validate_listeners() if require_listeners else self.config.validate()
        self._suppress_final_status = False
        self._final_status = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="napcat-runtime", daemon=True)
        self._thread.start()

    def stop(self, notify: bool = True) -> None:
        self._suppress_final_status = not notify
        self._stop.set()
        with self._lock:
            ws = self._ws
            pending = list(self._pending.values())
            self._pending.clear()
        for slot in pending:
            slot["error"] = "runtime stopped"
            slot["event"].set()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        if notify:
            self._set_status("disconnected", "NapCat WS 已停止")

    def _set_status(self, state: str, detail: str) -> None:
        self._final_status = (state, detail)
        self.on_status(state, detail)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.self_id = None
            self.self_nickname = ""
            self._set_status("checking", "正在连接 NapCat WebSocket…")
            ws = None
            try:
                headers = [f"Authorization: Bearer {self.config.token}"] if self.config.token else []
                ws = websocket.create_connection(
                    self.config.ws_url,
                    header=headers,
                    timeout=15,
                    enable_multithread=True,
                )
                ws.settimeout(self.config.read_timeout)
                with self._lock:
                    self._ws = ws
                self._reader = threading.Thread(target=self._read_loop, args=(ws,), name="napcat-reader", daemon=True)
                self._reader.start()
                self.on_log(f"WS 已连接：{self.config.ws_url}")
                info = self.call("get_login_info", timeout=12)
                self.self_id = int(info.get("user_id") or 0) or None
                self.self_nickname = str(info.get("nickname") or "")
                if not self.self_id:
                    raise RuntimeError("get_login_info 未返回 user_id，NapCat 可能尚未登录 QQ")
                self.detected_backend = self.config.backend
                if self.config.backend == "auto":
                    try:
                        version = self.call("get_version_info", timeout=5)
                        app_name = str(version.get("app_name") or "").lower()
                        self.detected_backend = "snowluma" if "snowluma" in app_name else "napcat"
                    except Exception as exc:
                        self.detected_backend = "napcat"
                        self.on_log(f"后端自动识别失败，回退到 NapCat：{type(exc).__name__}: {exc}")
                self.on_log(f"实际协议端：{self.detected_backend}")
                self._set_status("connected", f"已登录 QQ：{self.self_id}（{self.self_nickname}）")
                self.on_log(f"NapCat 登录确认：{self.self_id} {self.self_nickname}".strip())
                while not self._stop.is_set() and self._reader.is_alive():
                    self._reader.join(timeout=0.5)
                if not self._stop.is_set():
                    raise RuntimeError("WebSocket 读线程已退出")
            except Exception as exc:
                if not self._stop.is_set():
                    self._set_status("disconnected", f"WS 断开：{exc}")
                    self.on_log(f"WS 连接/运行错误：{type(exc).__name__}: {exc}")
                if not self.reconnect:
                    break
            finally:
                with self._lock:
                    self._ws = None
                    pending = list(self._pending.values())
                    self._pending.clear()
                for slot in pending:
                    slot["error"] = "连接已断开"
                    slot["event"].set()
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            if not self._stop.is_set():
                self._set_status("checking", f"{self.config.reconnect_interval:g} 秒后重连…")
                self._stop.wait(self.config.reconnect_interval)
        if not self._suppress_final_status:
            self._set_status("disconnected", "NapCat WS 已停止")

    def _read_loop(self, ws) -> None:
        while not self._stop.is_set():
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                return
            if not raw:
                continue
            try:
                packet = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(packet, dict):
                continue
            echo = packet.get("echo")
            if echo:
                with self._lock:
                    slot = self._pending.pop(str(echo), None)
                if slot is not None:
                    slot["response"] = packet
                    slot["event"].set()
                    continue
            if packet.get("post_type") == "meta_event" and packet.get("meta_event_type") == "heartbeat":
                continue
            if self.on_event:
                try:
                    self.on_event(packet)
                except Exception as exc:
                    self.on_log(f"事件处理异常：{type(exc).__name__}: {exc}")

    def call(self, action: str, timeout: float = 10.0, **params) -> dict:
        with self._lock:
            ws = self._ws
            if ws is None:
                raise RuntimeError("NapCat WS 尚未连接")
            echo = uuid.uuid4().hex
            slot = {"event": threading.Event(), "response": None, "error": ""}
            self._pending[echo] = slot
        try:
            ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
        except Exception:
            with self._lock:
                self._pending.pop(echo, None)
            raise
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(echo, None)
            raise TimeoutError(f"{action} 响应超时")
        if slot["error"]:
            raise RuntimeError(slot["error"])
        response = slot["response"] or {}
        if response.get("status") != "ok" or response.get("retcode", 0) != 0:
            raise RuntimeError(response.get("message") or response.get("wording") or f"{action} 调用失败")
        data = response.get("data") or {}
        return data if isinstance(data, dict) else {}

    def send_message(self, message_type: str, target_id: int, text: str) -> dict:
        if message_type == "group":
            return self.call("send_group_msg", group_id=target_id, message=text)
        elif message_type == "private":
            return self.call("send_private_msg", user_id=target_id, message=text)
        else:
            raise ValueError(f"不支持的消息类型：{message_type}")

    def send_video(self, message_type: str, target_id: int, path: str, thumb: str = "") -> dict:
        if self.detected_backend not in {"napcat", "snowluma"}:
            raise RuntimeError("协议端尚未识别；请在 WS 配置中指定后端或检查 get_version_info")
        from pathlib import Path
        data = {"file": Path(path).resolve().as_uri()}
        if thumb and self.detected_backend == "napcat":
            data["thumb"] = Path(thumb).resolve().as_uri()
        # SnowLuma 只收到 video segment；NapCat video segment 可附加作品封面 thumb。
        return self.send_message(message_type, target_id, [{"type": "video", "data": data}])

    def send_forward(self, message_type: str, target_id: int, nodes: list[dict]) -> dict:
        action = "send_group_forward_msg" if message_type == "group" else "send_private_forward_msg"
        target_key = "group_id" if message_type == "group" else "user_id"
        return self.call(action, **{target_key: target_id, "messages": nodes})

    def _summarize_event(self, event: dict) -> str:
        post_type = event.get("post_type") or "event"
        message_type = event.get("message_type") or ""
        if post_type == "message":
            target = event.get("group_id") if message_type == "group" else event.get("user_id")
            text = str(event.get("raw_message") or "").replace("\n", " ")
            return f"收到 {message_type or 'message'} {target or ''}: {text[:180]}".strip()
        return f"收到事件：{post_type}/{event.get('notice_type') or event.get('meta_event_type') or ''}"
