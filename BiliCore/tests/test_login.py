import json

from bilicore.login import BilibiliLoginManager


def test_cookie_store_round_trip_and_clear(tmp_path):
    manager = BilibiliLoginManager(tmp_path)
    assert manager.load() == {}
    manager.save({"SESSDATA": "secret", "DedeUserID": "123"})
    assert manager.load() == {"SESSDATA": "secret", "DedeUserID": "123"}
    assert json.loads(manager.cookie_path.read_text(encoding="utf-8"))["cookies"]["SESSDATA"] == "secret"
    manager.clear()
    assert manager.load() == {}


def test_cookie_store_ignores_corrupt_file(tmp_path):
    manager = BilibiliLoginManager(tmp_path)
    manager.cookie_path.parent.mkdir(parents=True, exist_ok=True)
    manager.cookie_path.write_text("{broken", encoding="utf-8")
    assert manager.load() == {}


def test_has_login_cookie_requires_session_credential():
    assert BilibiliLoginManager.has_login_cookie({"SESSDATA": "value"})
    assert BilibiliLoginManager.has_login_cookie({"bili_jct": "value"})
    assert not BilibiliLoginManager.has_login_cookie({"buvid3": "device-only"})
    assert not BilibiliLoginManager.has_login_cookie({})


def test_check_login_classifies_logged_out(tmp_path, monkeypatch):
    manager = BilibiliLoginManager(tmp_path)
    manager.save({"SESSDATA": "expired"})

    class Response:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {"code": -101, "message": "账号未登录", "data": {"isLogin": False}}

    import bilicore.login as bilibili_login
    monkeypatch.setattr(bilibili_login.requests, "get", lambda *args, **kwargs: Response())
    valid, message = manager.check_login()
    assert valid is False
    assert "失效" in message


def test_check_login_classifies_server_error_as_unknown(tmp_path, monkeypatch):
    manager = BilibiliLoginManager(tmp_path)
    manager.save({"SESSDATA": "keep"})

    class Response:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {"code": -412, "message": "请求被拦截", "data": {}}

    import bilicore.login as bilibili_login
    monkeypatch.setattr(bilibili_login.requests, "get", lambda *args, **kwargs: Response())
    valid, message = manager.check_login()
    assert valid is None
    assert "请求被拦截" in message
    assert manager.load()["SESSDATA"] == "keep"


def test_check_login_preserves_cookie_on_network_error(tmp_path, monkeypatch):
    import requests
    import bilicore.login as bilibili_login

    manager = BilibiliLoginManager(tmp_path)
    manager.save({"SESSDATA": "keep-me"})
    monkeypatch.setattr(
        bilibili_login.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("offline")),
    )
    valid, message = manager.check_login()
    assert valid is None
    assert "无法验证" in message
    assert manager.load()["SESSDATA"] == "keep-me"


def test_login_waits_when_scan_state_has_not_reached_nav_yet(tmp_path, monkeypatch):
    import bilicore.login as bilibili_login

    manager = BilibiliLoginManager(tmp_path)
    manager.profile_dir.mkdir(parents=True)
    monkeypatch.setattr(bilibili_login.os, "name", "nt")

    class Options:
        def add_argument(self, _argument):
            pass

    class Driver:
        def __init__(self):
            self.closed = False

        def get(self, _url):
            pass

        def get_cookies(self):
            return [{"name": "SESSDATA", "value": "fresh"}]

        def quit(self):
            self.closed = True

    driver = Driver()
    webdriver = type("WebDriver", (), {"Edge": staticmethod(lambda **_kwargs: driver)})
    monkeypatch.setitem(__import__("sys").modules, "selenium", type("Selenium", (), {"webdriver": webdriver}))
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver", type("WebDriverModule", (), {"Edge": staticmethod(lambda **_kwargs: driver)}))
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver.chrome", type("Chrome", (), {"options": type("ChromeOptionsModule", (), {"Options": Options})}))
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver.chrome.options", type("ChromeOptions", (), {"Options": Options}))
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver.edge", type("Edge", (), {"options": type("EdgeOptionsModule", (), {"Options": Options})}))
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver.edge.options", type("EdgeOptions", (), {"Options": Options}))

    class Response:
        def __init__(self, logged_in):
            self.logged_in = logged_in

        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"isLogin": self.logged_in}}

    results = iter((Response(False), Response(True)))
    monkeypatch.setattr(bilibili_login.requests.Session, "get", lambda *_args, **_kwargs: next(results))
    sleeps = []
    monkeypatch.setattr(bilibili_login.time, "sleep", lambda seconds: sleeps.append(seconds))
    progress = []

    ok, message = manager.login(progress.append, timeout=10)

    assert ok is True
    assert "安全保存" in message
    assert manager.load()["SESSDATA"] == "fresh"
    assert sleeps == [2]
    assert any("继续等待" in line for line in progress)
    assert driver.closed
