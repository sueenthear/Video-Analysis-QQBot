"""Bilibili 登录态管理：系统浏览器扫码登录并本地保存 Cookie。"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests

BILIBILI_HOME = "https://www.bilibili.com/"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    ),
    "Referer": BILIBILI_HOME,
}
LOGIN_COOKIES = {"SESSDATA", "bili_jct", "DedeUserID"}


class BilibiliLoginManager:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or Path(__file__).resolve().parent
        self.cookie_path = self.data_dir / "cookies.json"
        self.profile_dir = self.data_dir / ".browser_profile"
        self._lock = threading.Lock()
        self._driver = None

    def load(self) -> dict[str, str]:
        try:
            data = json.loads(self.cookie_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies", {})
            return cookies if isinstance(cookies, dict) else {}
        except (OSError, json.JSONDecodeError, AttributeError):
            return {}

    def save(self, cookies: dict[str, str]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "cookies": cookies,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        temp_path = self.cookie_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, self.cookie_path)

    def clear(self) -> None:
        try:
            self.cookie_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def has_login_cookie(cookies: dict[str, str]) -> bool:
        return any(cookies.get(name) for name in LOGIN_COOKIES)

    def check_login(self, timeout: float = 12.0) -> tuple[bool | None, str]:
        cookies = self.load()
        if not self.has_login_cookie(cookies):
            return False, "未登录：未找到有效会话 Cookie"
        try:
            response = requests.get(NAV_URL, cookies=cookies, headers=BROWSER_HEADERS, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            return None, f"暂时无法验证登录状态：{type(exc).__name__}"
        data = payload.get("data") or {}
        if payload.get("code") == 0 and data.get("isLogin"):
            return True, "已登录，Cookie 校验通过"
        if payload.get("code") == -101 or (payload.get("code") == 0 and not data.get("isLogin")):
            return False, "登录已失效，请重新扫码登录"
        message = payload.get("message") or payload.get("msg") or "服务器暂未确认登录状态"
        return None, f"{message}，请稍后重试"

    def login(self, on_progress: Callable[[str], None], timeout: float = 300.0) -> tuple[bool, str]:
        """打开隔离的系统浏览器档案，等待扫码登录并校验后保存 Cookie。"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.edge.options import Options as EdgeOptions
        except ImportError:
            return False, "Selenium 未安装，请运行 uv sync"

        driver = None
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            options_cls = EdgeOptions if os.name == "nt" else ChromeOptions
            options = options_cls()
            options.add_argument(f"--user-data-dir={self.profile_dir.resolve()}")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            driver = webdriver.Edge(options=options) if os.name == "nt" else webdriver.Chrome(options=options)
            with self._lock:
                self._driver = driver
            on_progress("浏览器已打开，请扫码登录 Bilibili…")
            driver.get(BILIBILI_HOME)
            deadline = time.monotonic() + timeout
            cookies: dict[str, str] = {}
            while time.monotonic() < deadline:
                try:
                    cookies = {
                        str(item["name"]): str(item["value"])
                        for item in driver.get_cookies()
                        if item.get("name") and item.get("value")
                    }
                except Exception:
                    cookies = {}
                if self.has_login_cookie(cookies):
                    on_progress("检测到登录凭据，正在校验…")
                    session = requests.Session()
                    try:
                        response = session.get(NAV_URL, cookies=cookies, headers=BROWSER_HEADERS, timeout=12)
                        response.raise_for_status()
                        payload = response.json()
                    except (requests.RequestException, ValueError) as exc:
                        on_progress(f"登录校验暂时失败（{type(exc).__name__}），继续等待扫码状态…")
                        time.sleep(2)
                        continue
                    if payload.get("code") == 0 and (payload.get("data") or {}).get("isLogin"):
                        self.save(cookies)
                        return True, "登录成功，Cookie 已安全保存"
                    if payload.get("code") == -101 or (
                        payload.get("code") == 0 and not (payload.get("data") or {}).get("isLogin")
                    ):
                        on_progress("Bilibili 尚未确认扫码登录，继续等待…")
                    elif payload.get("code") != 0:
                        message = payload.get("message") or payload.get("msg") or "未知接口错误"
                        on_progress(f"Bilibili 暂未确认登录：{message}，继续等待…")
                time.sleep(2)
            return False, "等待扫码登录校验超时；请确认手机端显示登录成功后重试"
        except Exception as exc:
            return False, f"无法启动登录浏览器：{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._driver = None
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def refresh_cookie(self, on_progress: Callable[[str], None] | None = None) -> tuple[bool, str]:
        """复用已有浏览器 profile 获取并校验 Cookie，不要求重新扫码。"""
        return self.login(on_progress or (lambda _message: ""), timeout=300)

    def cancel(self) -> None:
        with self._lock:
            driver = self._driver
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
