"""从真实登录浏览器页面捕获抖音作品详情响应。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .login_manager import LoginError, _launch_driver_checked, find_browser, get_active_browser

_DETAIL_PATH = "/aweme/v1/web/aweme/detail/"
_CAPTURE_KEY = "__douyinAwemeDetailCapture"

# 作品页地址：图文作品在网页端是 /note/，视频是 /video/。用错路径时页面对应的
# 作品详情不会出现，见 _detail_page_url。
_PAGE_URLS = {
    "note": "https://www.douyin.com/note/{item_id}",
    "video": "https://www.douyin.com/video/{item_id}",
}

# 主动请求详情接口时附带的查询参数，与页面自身请求保持同款。
#
# 这里刻意不带 a_bogus / msToken：请求发生在页面上下文里，同源且自动携带页面
# cookie（含 Argus 需要的 uifid），实测图文作品也能直接取到 aweme_detail。
# 静态 HTTP 请求缺的正是这部分上下文，所以走浏览器兜底时必须由页面代发。
_DETAIL_QUERY = (
    "aid=6383&device_platform=webapp&channel=channel_pc_web&pc_client_type=1"
    "&version_code=290100&version_name=29.1.0"
)

# 打开页面后先等一会儿再主动补请求，让页面自身的指纹/令牌初始化完成。
_REFETCH_DELAY = 1.2

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

# 主动取详情：图文作品页是服务端直出，整个页面生命周期都不会自己请求
# _DETAIL_PATH，只靠被动捕获必然等到超时。
_ACTIVE_FETCH_TEMPLATE = """(() => {
  const key = %(key)s;
  const itemId = %(item_id)s;
  const url = %(path)s + '?aweme_id=' + encodeURIComponent(itemId) + '&' + %(query)s;
  fetch(url, { headers: { 'accept': 'application/json, text/plain, */*' }, credentials: 'include' })
    .then((response) => response.json())
    .then((data) => { if (data && data.aweme_detail) { window[key] = data; } })
    .catch(() => {});
  return true;
})();"""


def _detail_page_url(item_id: str, kind: str = "") -> str:
    """按作品类型给出网页端地址；类型未知时沿用视频页。"""
    template = _PAGE_URLS["note"] if kind == "note" else _PAGE_URLS["video"]
    return template.format(item_id=item_id)


def _active_fetch_script(item_id: str) -> str:
    """生成"在页面上下文里主动请求详情"的脚本。"""
    return _ACTIVE_FETCH_TEMPLATE % {
        "key": json.dumps(_CAPTURE_KEY),
        "item_id": json.dumps(item_id),
        "path": json.dumps(_DETAIL_PATH),
        "query": json.dumps(_DETAIL_QUERY),
    }


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


def fetch_detail_from_browser(item_id: str, kind: str = "", timeout: float = 35.0) -> dict[str, Any]:
    """打开作品页并读取该作品的详情 JSON。

    两条取数路径，任一先到即可：

    1. 捕获页面自身发出的详情请求（视频页会发）；
    2. 在页面上下文主动 fetch 一次详情接口 —— 图文作品页是服务端直出、
       从不自发该请求，只靠路径 1 会一直等到超时。

    :param kind: ``"note"``（图文）/ ``"video"`` / ``""``（未知，按视频页打开）
    """
    browser = find_browser()
    if not browser:
        raise LoginError("未找到 Edge/Chrome，无法使用浏览器页面签名解析")

    browser_kind, _ = browser
    driver = get_active_browser()
    owns_driver = driver is None
    original_url = ""
    try:
        if owns_driver:
            profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profile")
            driver = _launch_driver_checked(browser_kind, profile_dir)
        else:
            try:
                original_url = driver.current_url
            except Exception:
                original_url = "https://www.douyin.com/"

        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _CAPTURE_SCRIPT})
        driver.get(_detail_page_url(item_id, kind))

        started = time.monotonic()
        deadline = started + timeout
        refetched = False
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

            if not refetched and time.monotonic() - started >= _REFETCH_DELAY:
                # 页面自身没发详情请求（图文作品页即如此）时，由页面代发一次。
                driver.execute_script(_active_fetch_script(item_id))
                refetched = True

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
