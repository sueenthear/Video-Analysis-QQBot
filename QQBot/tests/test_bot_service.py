from types import SimpleNamespace

from QQBot.bot_service import DouyinLinkBot
from QQBot.runtime import NapCatConfig


def test_ignores_messages_outside_allowlist(monkeypatch):
    config = NapCatConfig(listen=["*123"])
    sent = []
    service = DouyinLinkBot(config, SimpleNamespace(send_message=lambda *args: sent.append(args)), lambda _: None)
    monkeypatch.setattr("QQBot.bot_service.threading.Thread", lambda **kwargs: kwargs)
    service.on_event({
        "post_type": "message",
        "message_type": "group",
        "group_id": 456,
        "raw_message": "https://v.douyin.com/AbCd123/",
    })
    assert sent == []


def test_extracts_share_link_and_starts_async_parser(monkeypatch):
    config = NapCatConfig(listen=["*123"])
    callbacks = []
    service = DouyinLinkBot(config, SimpleNamespace(), lambda _: None)

    class FakeThread:
        def __init__(self, **kwargs):
            callbacks.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr("QQBot.bot_service.threading.Thread", FakeThread)
    service.on_event({
        "post_type": "message",
        "message_type": "private",
        "user_id": 123,
        "message_id": 88,
        "raw_message": "看看这个抖音 https://v.douyin.com/AbCd123/ 分享口令尾巴",
    })
    assert len(callbacks) == 1
    assert callbacks[0]["args"][0:2] == ("private", 123)
    assert callbacks[0]["args"][4] == "https://v.douyin.com/AbCd123/"
    assert callbacks[0]["args"][5] == "douyin"


def test_extracts_bilibili_link_and_routes_to_bilibili_parser(monkeypatch):
    config = NapCatConfig(listen=["*123"])
    callbacks = []
    service = DouyinLinkBot(config, SimpleNamespace(), lambda _: None)

    class FakeThread:
        def __init__(self, **kwargs):
            callbacks.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr("QQBot.bot_service.threading.Thread", FakeThread)
    service.on_event({
        "post_type": "message",
        "message_type": "private",
        "user_id": 123,
        "message_id": 89,
        "raw_message": "看这个：https://www.bilibili.com/video/BV1xx411c7mD/?p=2。",
    })
    assert len(callbacks) == 1
    assert callbacks[0]["args"][4:] == (
        "https://www.bilibili.com/video/BV1xx411c7mD/?p=2",
        "bilibili",
    )
    assert callbacks[0]["name"] == "bilibili-link-handler"
