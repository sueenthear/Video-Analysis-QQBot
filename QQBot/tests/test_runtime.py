import json
import threading

import pytest

from QQBot.runtime import ConfigStore, NapCatConfig, NapCatRuntime


def test_config_round_trip(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    config = NapCatConfig(
        ws_url="ws://127.0.0.1:3001/",
        token="secret-token",
        reconnect_interval=7,
        read_timeout=90,
        heartbeat_interval=30000,
        listen=["*12345", "#67890"],
    )
    store.save(config)
    loaded = store.load()
    assert loaded == config
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["napcat"]["token"] == "secret-token"
    assert raw["listen"] == ["*12345", "#67890"]


def test_empty_listen_is_valid_for_configuration_but_not_start(tmp_path):
    config = NapCatConfig()
    config.validate()
    with pytest.raises(ValueError, match="至少配置一个监听对象"):
        config.validate_listeners()


def test_config_rejects_read_timeout_not_above_heartbeat():
    with pytest.raises(ValueError, match="读超时必须大于心跳"):
        NapCatConfig(read_timeout=30, heartbeat_interval=30000).validate()


def test_allowlist_matches_group_and_private():
    config = NapCatConfig(listen=["*123", "#456"])
    assert config.is_watched("private", 123)
    assert config.is_watched("group", 456)
    assert not config.is_watched("group", 123)
    assert not config.is_watched("guild", 456)


def test_start_connection_probe_does_not_require_listener_allowlist(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.timeout = None
            self.sent = []
            self.closed = False
            self.messages = []

        def settimeout(self, value):
            self.timeout = value

        def send(self, raw):
            packet = json.loads(raw)
            self.sent.append(packet)
            self.messages.append(json.dumps({
                "echo": packet["echo"],
                "status": "ok",
                "retcode": 0,
                "data": {"user_id": 987, "nickname": "test-bot"},
            }))

        def recv(self):
            if self.messages:
                return self.messages.pop(0)
            raise __import__("websocket").WebSocketTimeoutException()

        def close(self):
            self.closed = True

    socket = FakeSocket()
    monkeypatch.setattr("QQBot.runtime.websocket.create_connection", lambda *args, **kwargs: socket)
    statuses = []
    runtime = NapCatRuntime(NapCatConfig(listen=[]), lambda *x: statuses.append(x), lambda _: None)
    runtime.start(require_listeners=False)
    for _ in range(100):
        if runtime.self_id:
            break
        __import__("time").sleep(0.01)
    assert runtime.self_id == 987
    assert socket.sent[0]["action"] == "get_login_info"
    assert runtime.config.listen == []
    runtime.stop(notify=False)
    assert socket.closed


def test_call_correlates_echo_and_validates_response():

    runtime = NapCatRuntime(NapCatConfig(listen=["*1"]), lambda *_: None, lambda _: None)

    class FakeSocket:
        def send(self, raw):
            packet = json.loads(raw)
            self.echo = packet["echo"]
            self.action = packet["action"]

    socket = FakeSocket()
    runtime._ws = socket
    result = {}

    def invoke():
        result.update(runtime.call("get_login_info", timeout=1))

    thread = threading.Thread(target=invoke)
    thread.start()
    while not hasattr(socket, "echo"):
        pass
    with runtime._lock:
        slot = runtime._pending.pop(socket.echo)
    slot["response"] = {
        "echo": socket.echo,
        "status": "ok",
        "retcode": 0,
        "data": {"user_id": 42, "nickname": "bot"},
    }
    slot["event"].set()
    thread.join(timeout=1)
    assert result == {"user_id": 42, "nickname": "bot"}
