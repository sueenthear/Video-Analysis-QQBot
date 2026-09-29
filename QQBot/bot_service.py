"""OneBot event handler matching the original video/gallery reply flow."""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from douyin_core import downloader
from douyin_core.douyin_parser import DouyinParser, extract_share_url, _image_ext
from douyin_core.login_manager import CookieStore
from bilicore import BilibiliParser, BilibiliSettingsStore, extract_bilibili_url, select_default_quality
from bilicore.login import BilibiliLoginManager

from .runtime import NapCatConfig, NapCatRuntime

TITLE_LIMIT = 100
NODE_TITLE_LIMIT = 500


def _segment(kind: str, **data) -> dict:
    return {"type": kind, "data": data}


def _node(uin: int, name: str, content: list[dict]) -> dict:
    return {"type": "node", "data": {"uin": str(uin), "name": name or "抖音", "content": content}}


def _clip(text: str, limit: int = TITLE_LIMIT) -> str:
    value = (text or "").strip()
    return value if len(value) <= limit else value[:limit] + "…"


def guess_media_type(text: str) -> str:
    if "图文" in (text or ""):
        return "image"
    if "作品" in (text or ""):
        return "video"
    return "unknown"


def format_bilibili_info(info) -> str:
    duration = f"{info.duration // 60}:{info.duration % 60:02d}"
    lines = [
        "【哔哩哔哩视频】",
        f"标题：{_clip(info.title) or '（无标题）'}",
        f"作者：{info.author or '未知'}",
        f"分 P：{info.page} · {info.part or '默认分 P'}",
        f"时长：{duration}",
    ]
    return "\n".join(lines)


def format_info(info) -> str:
    if info.is_image_post:
        head = "【抖音图文】"
        lines = [
            f"标题：{_clip(info.title) or '（无标题）'}",
            f"作者：{info.author or '未知'}",
            f"发布：{info.create_time or '未知'}",
            f"图片：{info.image_count} 张 / {info.resolution_text}",
        ]
    else:
        head = "【抖音视频】"
        lines = [
            f"标题：{_clip(info.title) or '（无标题）'}",
            f"作者：{info.author or '未知'}",
            f"发布：{info.create_time or '未知'}",
            f"时长/分辨率：{info.duration_text} / {info.resolution_text}",
        ]
    lines.append(f"互动：赞 {info.digg_count} · 评 {info.comment_count} · 藏 {info.collect_count} · 转 {info.share_count}")
    return head + "\n" + "\n".join(lines)


def build_image_nodes(self_id: int, author: str, title: str, paths: list[str]) -> list[dict]:
    name = author or "抖音"
    nodes = [_node(self_id, name, [_segment("text", text=_clip(title, NODE_TITLE_LIMIT))])]
    nodes.extend(
        _node(self_id, name, [_segment("image", file=Path(path).resolve().as_uri())])
        for path in paths
    )
    return nodes


class DouyinLinkBot:
    def __init__(
        self,
        config: NapCatConfig,
        runtime: NapCatRuntime,
        log: Callable[[str], None],
        douyin_log: Callable[[str], None] | None = None,
        bilibili_log: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.runtime = runtime
        self.log = log
        self.douyin_log = douyin_log or log
        self.bilibili_log = bilibili_log or log
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self.stats = {"received": 0, "video": 0, "image": 0, "bilibili": 0, "failed": 0}

    def on_event(self, event: dict) -> None:
        if event.get("post_type") != "message":
            return
        message_type = event.get("message_type")
        target_id = event.get("group_id") if message_type == "group" else event.get("user_id")
        if target_id is None or not self.config.is_watched(message_type, int(target_id)):
            return
        text = self._message_text(event)
        url = extract_bilibili_url(text)
        platform = "bilibili"
        if not url:
            url = extract_share_url(text)
            platform = "douyin"
        if not url:
            return
        message_id = str(event.get("message_id") or "")
        key = f"{message_type}:{target_id}:{message_id or url}"
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            if len(self._seen) > 2000:
                self._seen.clear()
        self.stats["received"] += 1
        threading.Thread(
            target=self._process,
            args=(message_type, int(target_id), message_id, text, url, platform),
            name=f"{platform}-link-handler",
            daemon=True,
        ).start()

    @staticmethod
    def _message_text(event: dict) -> str:
        raw = event.get("raw_message")
        if isinstance(raw, str) and raw:
            return raw
        message = event.get("message")
        if isinstance(message, str):
            return message
        return "".join(
            str((segment.get("data") or {}).get("text") or "")
            for segment in (message or [])
            if isinstance(segment, dict) and segment.get("type") == "text"
        )

    def _process(
        self, message_type: str, target_id: int, message_id: str, text: str,
        url: str, platform: str = "douyin",
    ) -> None:
        if platform == "bilibili":
            self._process_bilibili(message_type, target_id, message_id, url)
            return
        log = self.douyin_log
        kind = guess_media_type(text)
        notice = {
            "video": "检测到抖音视频分享链接，正在解析中……",
            "image": "检测到抖音图文分享链接，正在解析中……",
            "unknown": "检测到抖音分享链接，正在解析中……",
        }[kind]
        try:
            content = [_segment("reply", id=message_id), _segment("text", text=notice)] if message_id else notice
            self.runtime.send_message(message_type, target_id, content)
        except Exception as exc:
            log(f"[进度回复失败] {type(exc).__name__}: {exc}")

        parser = DouyinParser(cookie=CookieStore().load(), timeout=20)
        work_dir = Path(tempfile.mkdtemp(prefix="douyin-qqbot-"))
        try:
            log(f"开始解析：{url}")
            info = parser.parse_text(text)
            self.runtime.send_message(message_type, target_id, format_info(info))
            if info.is_image_post:
                self.stats["image"] += 1
                self._send_image_post(message_type, target_id, info, work_dir)
            else:
                self.stats["video"] += 1
                self._send_video_post(message_type, target_id, info, work_dir)
            log(f"[解析成功] {info.item_id} {_clip(info.title, 60)}")
        except Exception as exc:
            self.stats["failed"] += 1
            log(f"[解析/媒体发送失败] {type(exc).__name__}: {exc}")
            try:
                self.runtime.send_message(message_type, target_id, f"解析失败：{type(exc).__name__}: {exc}\n链接：{url}")
            except Exception as send_exc:
                log(f"[错误回复失败] {type(send_exc).__name__}: {send_exc}")
        finally:
            parser.session.close()
            shutil.rmtree(work_dir, ignore_errors=True)

    def _process_bilibili(self, message_type: str, target_id: int, message_id: str, url: str) -> None:
        log = self.bilibili_log
        try:
            content = [_segment("reply", id=message_id), _segment("text", text="检测到哔哩哔哩视频链接，正在解析并准备发送视频……")] if message_id else "检测到哔哩哔哩视频链接，正在解析并准备发送视频……"
            self.runtime.send_message(message_type, target_id, content)
        except Exception as exc:
            log(f"[Bilibili 进度回复失败] {type(exc).__name__}: {exc}")

        parser = None
        try:
            cookies = BilibiliLoginManager().load()
            if not BilibiliLoginManager.has_login_cookie(cookies):
                raise RuntimeError("未找到 Bilibili 登录 Cookie，请先扫码登录并确认登录状态")
            parser = BilibiliParser(cookies=cookies, timeout=20)
            log(f"开始解析 Bilibili：{url}")
            info = parser.parse(url)
            self.runtime.send_message(message_type, target_id, format_bilibili_info(info))
            self._send_bilibili_video(message_type, target_id, info)
            self.stats["bilibili"] = self.stats.get("bilibili", 0) + 1
            log(f"[Bilibili 解析成功] {info.bvid}，已发送默认清晰度视频")
        except Exception as exc:
            self.stats["failed"] += 1
            log(f"[Bilibili 解析失败] {type(exc).__name__}: {exc}")
            try:
                self.runtime.send_message(message_type, target_id, f"哔哩哔哩解析失败：{type(exc).__name__}: {exc}\n链接：{url}")
            except Exception as send_exc:
                log(f"[Bilibili 错误回复失败] {type(send_exc).__name__}: {send_exc}")
        finally:
            if parser is not None:
                parser.close()

    def _send_bilibili_video(self, message_type: str, target_id: int, info) -> None:
        log = self.bilibili_log
        quality = select_default_quality(
            info.qualities, BilibiliSettingsStore().load()["default_quality_id"]
        )
        if quality is None:
            raise RuntimeError("当前视频没有不高于默认清晰度的可用视频流")
        work_dir = Path(tempfile.mkdtemp(prefix="bilibili-qqbot-"))
        try:
            filename = downloader.safe_filename(
                f"{info.author or '未知作者'}_{info.title or info.bvid}_{quality.quality_id}.mp4"
            )
            path = downloader.download_file(
                quality.video_urls[0],
                str(work_dir),
                filename,
                headers={"Referer": "https://www.bilibili.com/"},
                url_fallbacks=list(quality.video_urls[1:]),
            )
            thumb_path = ""
            if info.cover_url and self.runtime.detected_backend == "napcat":
                thumb_path = downloader.download_file(
                    info.cover_url,
                    str(work_dir),
                    f"thumb_{info.bvid}.jpg",
                    headers={"Referer": "https://www.bilibili.com/"},
                )
            if self.runtime.detected_backend == "snowluma":
                self.runtime.send_video(message_type, target_id, path)
            else:
                self.runtime.send_video(message_type, target_id, path, thumb=thumb_path)
            suffix = "（含缩略图）" if thumb_path else ""
            log(f"[Bilibili 视频] 已发送 {quality.name} ({quality.width}x{quality.height}){suffix}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _send_video_post(self, message_type: str, target_id: int, info, work_dir: Path) -> None:
        log = self.douyin_log
        if not info.play_url:
            raise RuntimeError("未取到无水印播放地址")
        filename = downloader.build_filename(info)
        video_path = downloader.download_file(
            info.play_url, str(work_dir), filename, url_fallbacks=info.play_url_fallbacks
        )
        cover_path = ""
        if info.cover_url and self.runtime.detected_backend == "napcat":
            extension = _image_ext(info.cover_url)
            try:
                cover_path = downloader.download_file(
                    info.cover_url,
                    str(work_dir),
                    f"thumb_{info.item_id}.{extension}",
                    headers={"Referer": "https://www.douyin.com/"},
                )
            except downloader.DownloadError as exc:
                self.log(f"[缩略图下载失败] {exc}")
        if self.runtime.detected_backend == "snowluma":
            self.runtime.send_video(message_type, target_id, video_path)
        else:
            self.runtime.send_video(message_type, target_id, video_path, thumb=cover_path)
        suffix = "（含缩略图）" if cover_path else ""
        self.log(f"[视频] 已通过 {self.runtime.detected_backend or self.config.backend} 发送 video segment{suffix}")

    def _send_image_post(self, message_type: str, target_id: int, info, work_dir: Path) -> None:
        log = self.douyin_log
        paths = []
        for index, image in enumerate(info.images):
            filename = f"{info.item_id}_{index + 1}.{image.ext or 'jpeg'}"
            try:
                paths.append(downloader.download_file(
                    image.url, str(work_dir), filename, url_fallbacks=image.fallbacks
                ))
            except downloader.DownloadError as exc:
                self.log(f"[图文图片下载失败] 第 {index + 1} 张：{exc}")
        if not paths:
            raise RuntimeError("图文图片下载失败")
        nodes = build_image_nodes(self.runtime.self_id or 0, info.author, info.title, paths)
        self.runtime.send_forward(message_type, target_id, nodes)
        self.log(f"[图文] {len(paths)} 张图片已构造合并转发并发送")
