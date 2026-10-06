import pytest

from douyin_core import browser_bridge
from douyin_core.login_manager import LoginError

_UNSET = object()


class FakeDriver:
    def __init__(self, payload=_UNSET):
        self.payload = (
            {"aweme_detail": {"aweme_id": "123", "desc": "browser detail"}}
            if payload is _UNSET
            else payload
        )
        self.quit_calls = 0
        self.current_url = "https://www.douyin.com/"
        self.injected_script = None
        self.opened_urls = []
        self.scripts = []
        self.on_active_fetch = None

    def execute_cdp_cmd(self, method, params):
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self.injected_script = params["source"]
        return {}

    def get(self, url):
        self.current_url = url
        self.opened_urls.append(url)

    def execute_script(self, script):
        self.scripts.append(script)
        if "encodeURIComponent" in script:
            # 主动取详情脚本：页面自身沉默时靠它拿数据。
            if self.on_active_fetch is not None:
                self.on_active_fetch()
            return True
        if "return window[" in script:
            return self.payload
        return ""

    def quit(self):
        self.quit_calls += 1


def test_bridge_reuses_active_browser_and_keeps_it_open(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(browser_bridge, "find_browser", lambda: ("edge", "edge.exe"))
    monkeypatch.setattr(browser_bridge, "get_active_browser", lambda: driver)
    monkeypatch.setattr(
        browser_bridge,
        "_launch_driver_checked",
        lambda *args: pytest.fail("active browser should be reused"),
    )

    detail = browser_bridge.fetch_detail_from_browser("123", timeout=0.2)

    assert detail["aweme_id"] == "123"
    assert "XMLHttpRequest.prototype.send" in driver.injected_script
    assert driver.current_url == "https://www.douyin.com/"
    assert driver.quit_calls == 0


def test_bridge_ignores_other_aweme_detail_response(monkeypatch):
    driver = FakeDriver({"aweme_detail": {"aweme_id": "another-video"}})
    monkeypatch.setattr(browser_bridge, "find_browser", lambda: ("edge", "edge.exe"))
    monkeypatch.setattr(browser_bridge, "get_active_browser", lambda: driver)
    monkeypatch.setattr(browser_bridge.time, "sleep", lambda _seconds: None)

    with pytest.raises(LoginError, match="未捕获到匹配的作品详情响应"):
        browser_bridge.fetch_detail_from_browser("123", timeout=0)

    assert driver.quit_calls == 0


def test_bridge_closes_driver_it_created(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(browser_bridge, "find_browser", lambda: ("edge", "edge.exe"))
    monkeypatch.setattr(browser_bridge, "get_active_browser", lambda: None)
    monkeypatch.setattr(browser_bridge, "_launch_driver_checked", lambda *args: driver)

    detail = browser_bridge.fetch_detail_from_browser("123", timeout=0.2)

    assert detail["aweme_id"] == "123"
    assert driver.quit_calls == 1


def test_bridge_opens_note_page_for_image_post(monkeypatch):
    """图文作品在网页端是 /note/；用 /video/ 打开取不到该作品。"""
    item_id = "7693215825234461129"
    driver = FakeDriver({"aweme_detail": {"aweme_id": item_id, "desc": "图文"}})
    monkeypatch.setattr(browser_bridge, "find_browser", lambda: ("edge", "edge.exe"))
    monkeypatch.setattr(browser_bridge, "get_active_browser", lambda: None)
    monkeypatch.setattr(browser_bridge, "_launch_driver_checked", lambda *args: driver)

    detail = browser_bridge.fetch_detail_from_browser(item_id, kind="note", timeout=0.2)

    assert detail["desc"] == "图文"
    assert driver.opened_urls[0] == f"https://www.douyin.com/note/{item_id}"
    assert driver.quit_calls == 1


def test_bridge_active_fetch_recovers_silent_image_page(monkeypatch):
    """图文笔记页从不自发详情请求，必须由页面上下文主动补一次。"""
    driver = FakeDriver(None)
    driver.on_active_fetch = lambda: setattr(
        driver, "payload", {"aweme_detail": {"aweme_id": "123", "desc": "主动取到"}}
    )
    monkeypatch.setattr(browser_bridge, "find_browser", lambda: ("edge", "edge.exe"))
    monkeypatch.setattr(browser_bridge, "get_active_browser", lambda: driver)
    monkeypatch.setattr(browser_bridge, "_REFETCH_DELAY", 0.0)
    monkeypatch.setattr(browser_bridge.time, "sleep", lambda _seconds: None)

    detail = browser_bridge.fetch_detail_from_browser("123", kind="note", timeout=2.0)

    assert detail["desc"] == "主动取到"
    active_scripts = [script for script in driver.scripts if "encodeURIComponent" in script]
    assert len(active_scripts) == 1
    assert "/aweme/v1/web/aweme/detail/" in active_scripts[0]
    assert "aweme_id=' + encodeURIComponent(itemId)" in active_scripts[0]
    assert driver.current_url == "https://www.douyin.com/"
