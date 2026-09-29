from types import SimpleNamespace

from bilicore.parser import BilibiliVideoInfo, QualityOption
from QQBot import bot_service
from QQBot.bot_service import DouyinLinkBot
from QQBot.runtime import NapCatConfig


def _make_bot(monkeypatch, cookies):
    calls = []
    logs = []
    runtime = SimpleNamespace(
        send_message=lambda *args: calls.append(("message", args)),
        send_video=lambda *args, **kwargs: calls.append(("video", args, kwargs)),
        detected_backend="napcat",
    )
    class FakeLoginManager:
        def load(self):
            return cookies

        @staticmethod
        def has_login_cookie(value):
            return bool(value.get("SESSDATA"))

    monkeypatch.setattr(bot_service, "BilibiliLoginManager", FakeLoginManager)
    bot = DouyinLinkBot(NapCatConfig(listen=["*123"]), runtime, logs.append)
    return bot, calls, logs


def test_bilibili_link_replies_with_metadata_and_available_qualities(monkeypatch):
    bot, calls, logs = _make_bot(monkeypatch, {"SESSDATA": "test-cookie"})
    info = BilibiliVideoInfo(
        bvid="BV1xx411c7mD", aid=123, cid=456, page=2, title="解析测试", part="第二 P",
        author="UP 主", duration=125, cover_url="", description="",
        qualities=(
            QualityOption(80, "1080P 高清", 1920, 1080, ("avc1",), ("https://cdn/high",)),
            QualityOption(64, "720P 高清", 1280, 720, ("avc1",), ("https://cdn/low",)),
        ),
    )
    closed = []

    class FakeParser:
        def __init__(self, **kwargs):
            assert kwargs["cookies"] == {"SESSDATA": "test-cookie"}

        def parse(self, url):
            assert url == "https://www.bilibili.com/video/BV1xx411c7mD/"
            return info

        def close(self):
            closed.append(True)

    monkeypatch.setattr(bot_service, "BilibiliParser", FakeParser)
    monkeypatch.setattr(
        bot_service.downloader,
        "download_file",
        lambda _url, directory, filename, **_kwargs: str(__import__("pathlib").Path(directory) / filename),
    )
    bot._process("private", 123, "msg-1", "", "https://www.bilibili.com/video/BV1xx411c7mD/", "bilibili")

    assert [call[0] for call in calls] == ["message", "message", "video"]
    assert calls[0][1][2][0]["type"] == "reply"
    assert "解析测试" in calls[1][1][2]
    assert "1080P 高清 (1920x1080)" in calls[1][1][2]
    assert "720P 高清 (1280x720)" in calls[1][1][2]
    assert calls[2][1][2].endswith("80.mp4")
    assert bot.stats["bilibili"] == 1
    assert closed == [True]
    assert any("已发送默认清晰度视频" in line for line in logs)
    assert any("1080P 高清" in line for line in logs)


def test_bilibili_link_without_login_cookie_replies_clearly(monkeypatch):
    bot, calls, logs = _make_bot(monkeypatch, {})

    class ParserMustNotStart:
        def __init__(self, **_kwargs):
            raise AssertionError("parser must not start without login cookie")

    monkeypatch.setattr(bot_service, "BilibiliParser", ParserMustNotStart)
    bot._process("group", 77, "msg-2", "", "https://www.bilibili.com/video/BV1xx411c7mD/", "bilibili")

    assert len(calls) == 2
    assert "请先扫码登录" in calls[1][1][2]
    assert bot.stats["failed"] == 1
    assert any("未找到 Bilibili 登录 Cookie" in line for line in logs)
