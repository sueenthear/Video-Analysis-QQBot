from pathlib import Path

import pytest

from QQBot.runtime import NapCatConfig, NapCatRuntime


@pytest.mark.parametrize(
    ("backend", "expect_thumb"),
    [("napcat", True), ("snowluma", False)],
)
def test_video_segment_backend_payload(tmp_path, monkeypatch, backend, expect_thumb):
    video = tmp_path / "clip #1.mp4"
    thumb = tmp_path / "cover 1.jpeg"
    video.write_bytes(b"video")
    thumb.write_bytes(b"image")
    runtime = NapCatRuntime(NapCatConfig(backend=backend), lambda *_: None, lambda _: None)
    runtime.detected_backend = backend
    captured = []
    monkeypatch.setattr(runtime, "send_message", lambda *args: captured.append(args) or {})

    runtime.send_video("group", 42, str(video), str(thumb))

    message = captured[0][2]
    assert [segment["type"] for segment in message] == ["video"]
    payload = message[0]["data"]
    assert "%23" in payload["file"]
    assert ("thumb" in payload) is expect_thumb
    if expect_thumb:
        assert "%20" in payload["thumb"]


def test_unresolved_auto_backend_refuses_video_send(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    runtime = NapCatRuntime(NapCatConfig(backend="auto"), lambda *_: None, lambda _: None)
    with pytest.raises(RuntimeError, match="尚未识别"):
        runtime.send_video("private", 1, str(video))


def test_forward_action_and_target_are_backend_independent(monkeypatch):
    runtime = NapCatRuntime(NapCatConfig(), lambda *_: None, lambda _: None)
    calls = []
    monkeypatch.setattr(runtime, "call", lambda action, **kwargs: calls.append((action, kwargs)) or {})
    nodes = [{"type": "node", "data": {"content": []}}]
    runtime.send_forward("group", 22, nodes)
    runtime.send_forward("private", 11, nodes)
    assert calls == [
        ("send_group_forward_msg", {"group_id": 22, "messages": nodes}),
        ("send_private_forward_msg", {"user_id": 11, "messages": nodes}),
    ]
