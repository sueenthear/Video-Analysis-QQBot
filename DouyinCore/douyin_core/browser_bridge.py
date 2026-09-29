"""从真实登录浏览器页面捕获抖音作品详情响应。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .login_manager import LoginError, _launch_driver_checked, find_browser, get_active_browser

_DETAIL_PATH = "/aweme/v1/web/aweme/detail/"
_CAPTURE_KEY = "__douyinAwemeDetailCapture"

_CAPTURE_SCRIPT = r"""(() => {
  const key = '__douyinAwemeDetailCapture';
  window[key] = null;
  const isDetail = (url) => String(url || '').includes('/aweme/v1/web/aweme/detail/');
  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function(...args) {
      const requestUrl = args[0] && (args[0].url || args[0]);
      return originalFetch.apply(this, args).then((response) => {
        if (isDetail(requestUrl)) {
          response.clone().json().then((data) => { window[key] = data; }).catch(() => {});
        }
        return response;
      });
    };
  }
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__douyinDetailRequest = isDetail(url);
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    if (this.__douyinDetailRequest) {
      this.addEventListener('load', () => {
        try { window[key] = JSON.parse(this.responseText); } catch (_) {}
      }, {once: true});
    }
    return originalSend.apply(this, args);
  };
})();"""


def _extract_detail(payload: Any, item_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        return None
    response_id = str(detail.get("aweme_id") or "")
    if response_id and response_id != str(item_id):
        return None
    return detail


def fetch_detail_from_browser(item_id: str, timeout: float = 35.0) -> dict[str, Any]:
    """打开作品页并读取页面自身 fetch/XHR 产生的详情 JSON。"""
    browser = find_browser()
    if not browser:
        raise LoginError("未找到 Edge/Chrome，无法使用浏览器页面签名解析")

    kind, _ = browser
    driver = get_active_browser()
    owns_driver = driver is None
    original_url = ""
    try:
        if owns_driver:
            profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profile")
            driver = _launch_driver_checked(kind, profile_dir)
        else:
            try:
                original_url = driver.current_url
            except Exception:
                original_url = "https://www.douyin.com/"

        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _CAPTURE_SCRIPT})
        driver.get(f"https://www.douyin.com/video/{item_id}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = driver.execute_script(f"return window['{_CAPTURE_KEY}'] || null")
            detail = _extract_detail(payload, item_id)
            if detail is not None:
                return detail

            body_text = str(driver.execute_script(
                "return (document.body && document.body.innerText) || ''"
            ) or "")
            if any(marker in body_text for marker in ("验证码", "安全验证", "验证后继续")):
                raise LoginError("抖音页面要求安全验证，请在打开的浏览器中完成验证后重试")
            time.sleep(0.25)

        raise LoginError(
            "浏览器页面未捕获到匹配的作品详情响应；请确认登录/profile 可用，"
            "或页面已完成验证后重试"
        )
    finally:
        if owns_driver and driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        elif not owns_driver and driver is not None and original_url:
            try:
                driver.get(original_url)
            except Exception:
                pass
