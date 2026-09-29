import socket
import threading

from QQBot.port_probe import PortProbe


def test_parse_websocket_endpoint_defaults_ports():
    assert PortProbe.endpoint("ws://localhost/path") == ("localhost", 80)
    assert PortProbe.endpoint("wss://example.test/ws") == ("example.test", 443)
    assert PortProbe.endpoint("ws://127.0.0.1:3001/") == ("127.0.0.1", 3001)


def test_probe_once_reports_reachable_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = threading.Thread(target=lambda: listener.accept()[0].close(), daemon=True)
    accepted.start()
    probe = PortProbe(f"ws://127.0.0.1:{port}/", lambda *_: None, timeout=1)
    try:
        ok, detail = probe.probe_once()
    finally:
        listener.close()
    assert ok is True
    assert str(port) in detail


def test_probe_once_reports_refused_port():
    probe = PortProbe("ws://127.0.0.1:1/", lambda *_: None, timeout=0.3)
    ok, detail = probe.probe_once()
    assert ok is False
    assert "不可连接" in detail


def test_periodic_probe_emits_boolean_result():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = threading.Thread(target=lambda: listener.accept()[0].close(), daemon=True)
    accepted.start()
    results = []
    done = threading.Event()

    def on_result(ok, detail):
        results.append((ok, detail))
        done.set()

    probe = PortProbe(f"ws://127.0.0.1:{port}/", on_result, interval=10, timeout=1)
    try:
        probe.start()
        assert done.wait(3)
    finally:
        probe.stop()
        listener.close()
    assert results[0][0] is True
