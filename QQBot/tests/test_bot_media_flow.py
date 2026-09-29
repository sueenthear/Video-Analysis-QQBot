from types import SimpleNamespace

from douyin_core.douyin_parser import ImageItem, VideoInfo

from QQBot import bot_service
from QQBot.bot_service import DouyinLinkBot
from QQBot.runtime import NapCatConfig


class FakeParser:
    info = None

    def __init__(self, **_kwargs):
        self.session = SimpleNamespace(close=lambda: None)

    def parse_text(self, _text):
        return self.info


def _make_bot(monkeypatch, tmp_path, backend="napcat"):
    config = NapCatConfig(backend=backend, listen=["#77"])
    calls = []
    runtime = SimpleNamespace(
        self_id=999,
        detected_backend=backend,
        send_message=lambda *args: calls.append(("message", args)),
        send_video=lambda *args, **kwargs: calls.append(("video", args, kwargs)),
        send_forward=lambda *args: calls.append(("forward", args)),
    )
    monkeypatch.setattr(bot_service, "CookieStore", lambda: SimpleNamespace(load=lambda: "test-cookie"))
    monkeypatch.setattr(bot_service, "DouyinParser", FakeParser)
    monkeypatch.setattr(bot_service.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(bot_service.shutil, "rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot_service.downloader, "download_file", lambda url, directory, name, **kwargs: str(tmp_path / name))
    logs = []
    return DouyinLinkBot(config, runtime, logs.append), calls, logs


def test_video_reply_order_downloads_cover_and_sends_backend_media(tmp_path, monkeypatch):
    bot, calls, logs = _make_bot(monkeypatch, tmp_path, "napcat")
    FakeParser.info = VideoInfo(
        item_id="123", title="测试视频", author="作者", play_url="https://cdn/video.mp4",
        cover_url="https://cdn/cover.webp",
    )

    bot._process("group", 77, "msg-1", "看看作品 https://v.douyin.com/abc/", "https://v.douyin.com/abc/")

    assert [entry[0] for entry in calls] == ["message", "message", "video"]
    progress = calls[0][1][2]
    assert progress[0]["type"] == "reply"
    assert "视频分享链接" in progress[1]["data"]["text"]
    assert "【抖音视频】" in calls[1][1][2]
    assert calls[2][2]["thumb"].endswith("thumb_123.webp")
    assert not any("download_file" in line for line in logs)


def test_snowluma_video_uses_single_video_segment_without_thumb(tmp_path, monkeypatch):
    bot, calls, _logs = _make_bot(monkeypatch, tmp_path, "snowluma")
    FakeParser.info = VideoInfo(
        item_id="123", title="测试", play_url="https://cdn/video.mp4",
        cover_url="https://cdn/cover.jpg",
    )

    bot._send_video_post("group", 77, FakeParser.info, tmp_path)

    assert len(calls) == 1
    assert calls[0][0] == "video"
    assert calls[0][2] == {}


def test_gallery_reply_uses_caption_node_and_ordered_image_nodes(tmp_path, monkeypatch):
    bot, calls, _logs = _make_bot(monkeypatch, tmp_path, "napcat")
    FakeParser.info = VideoInfo(
        item_id="456", title="图文标题", author="图文作者",
        images=[ImageItem(url="https://cdn/1.jpg"), ImageItem(url="https://cdn/2.jpg")],
        media_type="image",
    )

    bot._process("group", 77, "msg-2", "看看图文作品 https://v.douyin.com/gallery/", "https://v.douyin.com/gallery/")

    assert [entry[0] for entry in calls] == ["message", "message", "forward"]
    nodes = calls[2][1][2]
    assert len(nodes) == 3
    assert nodes[0]["data"]["content"][0]["data"]["text"] == "图文标题"
    assert [node["data"]["content"][0]["type"] for node in nodes[1:]] == ["image", "image"]
