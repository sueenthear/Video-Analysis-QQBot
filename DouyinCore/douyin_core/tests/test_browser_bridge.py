import pytest

from douyin_core import browser_bridge
from douyin_core.login_manager import LoginError


class FakeDriver:
    def __init__(self, payload=None):
        self.payload = payload or {
            "aweme_detail": {
                "aweme_id": "123",
                "desc": "browser detail",
            }
        }
        self.quit_calls = 0
        self.current_url = "https://www.douyin.com/"
        self.injected_script = None

    def execute_cdp_cmd(self, method, params):
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self.injected_script = params["source"]
        return {}

    def get(self, url):
        self.current_url = url

    def execute_script(self, script):
        if "__douyinAwemeDetailCapture" in script:
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
