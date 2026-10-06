"""Resolve Bilibili video metadata and all playback qualities available to the account."""

from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import requests

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
VIEW_URL = "https://api.bilibili.com/x/web-interface/wbi/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/wbi/playurl"
REFERER = "https://www.bilibili.com/"

WBI_MIXIN_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

QUALITY_NAMES = {
    6: "240P 极速",
    16: "360P 流畅",
    32: "480P 清晰",
    64: "720P 高清",
    74: "720P 高帧率",
    80: "1080P 高清",
    112: "1080P 高码率",
    116: "1080P 60帧",
    120: "4K",
    125: "HDR 真彩",
    126: "杜比视界",
    127: "8K",
}


def extract_bilibili_url(text: str) -> str | None:
    text = html.unescape(text or "")
    match = re.search(
        r"https?://(?:www\.)?(?:bilibili\.com/video/[^\s]+|b23\.tv/[^\s]+)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(0).rstrip("，。；：！？、）》）]}>\"'.,;:!?)]}")


def _parse_video_identity(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    match = re.search(r"/(BV[0-9A-Za-z]+|av\d+)", parsed.path, re.IGNORECASE)
    if not match:
        raise ValueError("链接中未找到有效的 BV 号或 AV 号")
    video_id = match.group(1)
    query = parse_qs(parsed.query)
    page_text = query.get("p", ["1"])[0]
    try:
        page = max(1, int(page_text))
    except ValueError:
        page = 1
    return video_id, page


def _wbi_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    if len(raw) <= max(WBI_MIXIN_TABLE):
        raise ValueError("Bilibili WBI 签名密钥无效")
    return "".join(raw[index] for index in WBI_MIXIN_TABLE)[:32]


def sign_wbi(params: dict, img_key: str, sub_key: str, timestamp: int | None = None) -> dict[str, str]:
    values = {key: str(value) for key, value in params.items()}
    values["wts"] = str(int(time.time()) if timestamp is None else timestamp)
    values = dict(sorted(values.items()))
    query = urlencode({key: re.sub(r"[!'()*]", "", value) for key, value in values.items()})
    values["w_rid"] = hashlib.md5((query + _wbi_mixin_key(img_key, sub_key)).encode()).hexdigest()
    return values


@dataclass(frozen=True)
class QualityOption:
    quality_id: int
    name: str
    width: int
    height: int
    codecs: tuple[str, ...]
    video_urls: tuple[str, ...]
    audio_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class BilibiliVideoInfo:
    bvid: str
    aid: int
    cid: int
    page: int
    title: str
    part: str
    author: str
    duration: int
    cover_url: str
    description: str
    qualities: tuple[QualityOption, ...]


class BilibiliParser:
    def __init__(self, cookies: dict[str, str] | None = None, timeout: float = 20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": REFERER})
        if cookies:
            self.session.cookies.update(cookies)
        self.timeout = timeout
        self._wbi_keys: tuple[str, str] | None = None

    def close(self) -> None:
        self.session.close()

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        last_error = None
        for attempt in range(2):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Bilibili API 返回格式无效")
                code = payload.get("code", 0)
                if code != 0:
                    message = payload.get("message") or payload.get("msg") or f"API 错误码 {code}"
                    raise RuntimeError(f"Bilibili API 请求失败：{message}")
                return payload.get("data") or {}
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.4)
        raise RuntimeError(f"Bilibili API 请求失败：{last_error}") from last_error

    def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys:
            return self._wbi_keys
        data = self._get_json(NAV_URL)
        wbi = data.get("wbi_img") or {}
        img_url = str(wbi.get("img_url") or "")
        sub_url = str(wbi.get("sub_url") or "")
        img_match = re.search(r"/([0-9A-Za-z]+)\.[^/]+$", urlparse(img_url).path)
        sub_match = re.search(r"/([0-9A-Za-z]+)\.[^/]+$", urlparse(sub_url).path)
        if not img_match or not sub_match:
            raise RuntimeError("无法从 Bilibili nav 接口获取 WBI 签名密钥")
        self._wbi_keys = img_match.group(1), sub_match.group(1)
        return self._wbi_keys

    def _signed_get(self, url: str, params: dict) -> dict:
        img_key, sub_key = self._get_wbi_keys()
        return self._get_json(url, sign_wbi(params, img_key, sub_key))

    def parse(self, url: str) -> BilibiliVideoInfo:
        if urlparse(url).netloc.lower() in {"b23.tv", "www.b23.tv"}:
            response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            url = response.url
            if urlparse(url).netloc.lower() not in {"bilibili.com", "www.bilibili.com"}:
                raise ValueError("Bilibili 短链接跳转到了非 Bilibili 域名")
        video_id, page_number = _parse_video_identity(url)
        view = self._signed_get(VIEW_URL, {"bvid": video_id} if video_id.upper().startswith("BV") else {"aid": video_id[2:]})
        pages = view.get("pages") or []
        if not pages:
            raise RuntimeError("Bilibili 未返回视频分 P 信息")
        if page_number > len(pages):
            raise ValueError(f"视频只有 {len(pages)} P，链接指定了第 {page_number} P")
        page = pages[page_number - 1]
        cid = int(page.get("cid") or 0)
        bvid = str(view.get("bvid") or video_id)
        if not cid:
            raise RuntimeError("Bilibili 视频信息缺少 cid")

        play_responses = []
        play_errors = []
        for requested_quality in (80, 127):
            try:
                play_responses.append(self._signed_get(PLAYURL_URL, {
                    "bvid": bvid,
                    "cid": cid,
                    "qn": requested_quality,
                    "fnver": 0,
                    "fnval": 4048,
                    "fourk": 1,
                }))
            except Exception as exc:
                play_errors.append(exc)
        if not play_responses:
            raise RuntimeError(f"无法获取 Bilibili 播放流：{play_errors[-1]}")

        quality_labels = {}
        grouped: dict[int, list[dict]] = {}
        audio_urls: list[str] = []
        for play in play_responses:
            for audio in (play.get("dash") or {}).get("audio") or []:
                candidates = [audio.get("baseUrl") or audio.get("base_url")]
                candidates.extend(audio.get("backupUrl") or audio.get("backup_url") or [])
                audio_urls.extend(str(value) for value in candidates if value)
            for item in play.get("support_formats") or []:
                if isinstance(item, dict) and str(item.get("quality", "")).isdigit():
                    quality_labels[int(item["quality"])] = str(
                        item.get("new_description") or item.get("description") or ""
                    )
            video_streams = ((play.get("dash") or {}).get("video") or [])
            for stream in video_streams:
                try:
                    quality_id = int(stream.get("id") or 0)
                    width = int(stream.get("width") or 0)
                    height = int(stream.get("height") or 0)
                except (TypeError, ValueError):
                    continue
                urls = [stream.get("baseUrl") or stream.get("base_url")]
                urls.extend(stream.get("backupUrl") or stream.get("backup_url") or [])
                urls = list(dict.fromkeys(str(value) for value in urls if value))
                if quality_id and urls:
                    entries = grouped.setdefault(quality_id, [])
                    codec = str(stream.get("codecs") or stream.get("codecid") or "unknown")
                    if not any(item["codec"] == codec for item in entries):
                        entries.append({
                            "width": width,
                            "height": height,
                            "codec": codec,
                            "urls": urls,
                        })

        qualities = []
        for quality_id in sorted(grouped, reverse=True):
            entries = grouped[quality_id]
            first = entries[0]
            fallback_name = f"{first['height']}P" if first["height"] else f"画质 {quality_id}"
            qualities.append(QualityOption(
                quality_id=quality_id,
                name=quality_labels.get(quality_id) or QUALITY_NAMES.get(quality_id) or fallback_name,
                width=first["width"],
                height=first["height"],
                codecs=tuple(dict.fromkeys(item["codec"] for item in entries)),
                video_urls=tuple(url for item in entries for url in item["urls"]),
                audio_urls=tuple(dict.fromkeys(audio_urls)),
            ))
        if not qualities:
            raise RuntimeError("Bilibili 未返回当前账号可用的视频流；请检查登录状态或稍后重试")

        owner = view.get("owner") or {}
        return BilibiliVideoInfo(
            bvid=bvid,
            aid=int(view.get("aid") or 0),
            cid=cid,
            page=page_number,
            title=str(view.get("title") or ""),
            part=str(page.get("part") or view.get("title") or ""),
            author=str(owner.get("name") or ""),
            duration=int(page.get("duration") or view.get("duration") or 0),
            cover_url=str(view.get("pic") or ""),
            description=str(view.get("desc") or ""),
            qualities=tuple(qualities),
        )
