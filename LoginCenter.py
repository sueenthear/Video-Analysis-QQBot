"""跨平台登录与 QQBot 运行管理中心。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF, FluentWindow, NavigationItemPosition, PushButton,
    ComboBox, DoubleSpinBox, RadioButton, SpinBox, StrongBodyLabel, SwitchButton, TextEdit, TitleLabel,
    setTheme, Theme,
)

ROOT = Path(__file__).resolve().parent
ICON_DIR = ROOT / "assets" / "fluent-icons"
sys.path.insert(0, str(ROOT / "DouyinCore"))
sys.path.insert(0, str(ROOT / "BiliCore"))

from douyin_core.douyin_parser import DouyinParser, extract_share_url  # noqa: E402
from douyin_core.login_manager import LoginManager, LoginState, close_active_browser  # noqa: E402
from QQBot.bot_service import DouyinLinkBot  # noqa: E402
from QQBot.platforms import PlatformFeatureStore  # noqa: E402
from QQBot.port_probe import PortProbe  # noqa: E402
from QQBot.runtime import ConfigStore, NapCatConfig, NapCatRuntime  # noqa: E402
from bilicore import BilibiliParser, BilibiliSettingsStore, QUALITY_CHOICES, select_default_quality  # noqa: E402
from bilicore.login import BilibiliLoginManager  # noqa: E402
from bilicore.parser import extract_bilibili_url  # noqa: E402
from bilicore.ffmpeg import find_ffmpeg, ffmpeg_version, install_ffmpeg  # noqa: E402


class LogChannel(QObject):
    message = Signal(str, str, str)
    _write_lock = threading.Lock()

    def __init__(self, belong: str, parent=None):
        super().__init__(parent)
        self.belong = belong
        self.log_dir = ROOT / "logs"

    def connect(self, slot):
        self.message.connect(lambda timestamp, tag, msg: slot(self.format(timestamp, tag, msg)))

    def format(self, timestamp: str, tag: str, msg: str) -> str:
        return f"{timestamp} | {tag} | {self.belong} | {msg}"

    def emit(self, msg: str, tag: str = "info"):
        text = str(msg)
        if tag == "info":
            if any(word in text for word in ("失败", "错误", "异常", "未找到", "无效")):
                tag = "error"
            elif any(word in text for word in ("警告", "重试", "暂时", "未确认", "跳过")):
                tag = "warning"
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        line = self.format(timestamp, tag, text)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self._write_lock:
                (self.log_dir / f"{now:%Y-%m-%d}.log").open("a", encoding="utf-8").write(line + "\n")
        except OSError:
            pass
        self.message.emit(timestamp, tag, text)


class UiSignals(QObject):
    status = Signal(str, str)
    bilibili_status = Signal(str, str)
    parse_result = Signal(str, str, str)
    napcat_status = Signal(str, str)
    napcat_port_status = Signal(bool, str)
    napcat_probe_done = Signal()
    bilibili_parse_result = Signal(str, str, str)
    ffmpeg_result = Signal(bool, str)
    runtime_status = Signal(str, str)
    napcat_config_saved = Signal(object)
    platform_enabled_changed = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.douyin_log = LogChannel("抖音", self)
        self.bilibili_log = LogChannel("哔哩哔哩", self)
        self.log = LogChannel("全局", self)


class LoginPage(QWidget):
    def __init__(self, title: str, description: str):
        super().__init__()
        self.log_belong = "全局"
        self.layout = QVBoxLayout(self)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("loginPage")
        self.layout.setContentsMargins(34, 30, 34, 30)
        self.layout.setSpacing(16)
        self.title_row = QWidget(self)
        self.title_layout = QHBoxLayout(self.title_row)
        self.title_layout.setContentsMargins(0, 0, 0, 0)
        self.title_layout.addWidget(TitleLabel(title))
        self.title_layout.addStretch(1)
        self.layout.addWidget(self.title_row)
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #59636e; font-size: 14px;")
        self.layout.addWidget(desc)
        self.layout.addSpacing(8)
        self.status = StrongBodyLabel("状态：尚未检测")
        self.layout.addWidget(self.status)
        self.log_box = TextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(180)
        self.layout.addWidget(self.log_box)
        self.layout.addStretch(1)

    def set_status(self, text: str, color: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def append_log(self, text: str):
        if " | " not in text:
            text = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | info | {self.log_belong} | {text}"
        self.log_box.append(text)


class DouyinPage(LoginPage):
    def __init__(self, signals: UiSignals):
        super().__init__("抖音登录", "静态 API 遇到页面签名门禁时复用已登录 Edge 页面。保持登录中心和浏览器运行。")
        self.signals = signals
        self.log_belong = "抖音"
        self.manager = LoginManager()
        self.feature_store = PlatformFeatureStore()
        self.features = self.feature_store.load()
        self.enable_switch = SwitchButton(self)
        self.enable_switch.setOnText("启用该解析")
        self.enable_switch.setOffText("已关闭")
        self.enable_switch.setChecked(self.features["douyin"])
        self.title_layout.addWidget(self.enable_switch)
        self.enable_switch.checkedChanged.connect(self._on_enabled_changed)
        self._parse_running = False
        self.login_button = PushButton("打开抖音登录页", self, FIF.QRCODE)
        self.refresh_button = PushButton("复用 profile 刷新 Cookie", self, FIF.SYNC)
        self.check_button = PushButton("检查登录状态", self, FIF.INFO)
        self.clear_button = PushButton("清空本地持久化", self, FIF.DELETE)
        for index, button in enumerate((self.login_button, self.refresh_button, self.check_button, self.clear_button), 4):
            self.layout.insertWidget(index, button)
        self.share_input = TextEdit(self)
        self.share_input.setPlaceholderText("粘贴抖音分享文案或链接")
        self.share_input.setMaximumHeight(110)
        self.parse_button = PushButton("测试解析", self, FIF.SEARCH)
        self.parse_button.setToolTip("解析作品信息和直链，不下载视频")
        self.parse_output = TextEdit(self)
        self.parse_output.setReadOnly(True)
        self.parse_output.setPlaceholderText("解析结果将在此显示")
        self.parse_output.setMinimumHeight(130)
        self.layout.insertWidget(8, StrongBodyLabel("分享链接测试"))
        self.layout.insertWidget(9, self.share_input)
        self.layout.insertWidget(10, self.parse_button)
        self.layout.insertWidget(11, self.parse_output)
        self.login_button.clicked.connect(self.start_login)
        self.refresh_button.clicked.connect(self.refresh_cookie)
        self.check_button.clicked.connect(self.check_login)
        self.clear_button.clicked.connect(self.clear_persistence)
        self.parse_button.clicked.connect(self.test_parse)
        signals.status.connect(self._on_status)
        signals.parse_result.connect(self._on_parse_result)
        signals.douyin_log.connect(self.append_log)
        self.append_log(f"Cookie 文件：{self.manager.store.path}")
        self.append_log(f"浏览器 profile：{Path(self.manager.store.path).parent / '.browser_profile'}")

    def _on_enabled_changed(self, enabled: bool):
        self.features["douyin"] = bool(enabled)
        self.feature_store.save(self.features)
        self.signals.platform_enabled_changed.emit("douyin", bool(enabled))
        if not enabled:
            self.set_status("状态：抖音解析已停用", "#8a8f98")
        self.append_log(f"抖音解析{'已启用' if enabled else '已停用'}")

    def _set_busy(self, busy: bool):
        for button in (self.login_button, self.refresh_button, self.check_button, self.clear_button):
            button.setEnabled(not busy and not self._parse_running)
        self.parse_button.setEnabled(not busy and not self._parse_running)

    def _result_callback(self, state: LoginState, message: str):
        self.signals.status.emit(state.value, message)

    def start_login(self):
        self._set_busy(True)
        self.set_status("状态：正在准备 Selenium 和 Edge/Chrome…", "#d99b00")
        self.manager.start_login_async(self._result_callback, progress_cb=self.signals.douyin_log.emit, timeout=300)

    def refresh_cookie(self):
        self._set_busy(True)
        self.set_status("状态：正在复用抖音浏览器 profile…", "#d99b00")

        def work():
            cookie = self.manager.refresh_cookie_sync(timeout=300, progress_cb=self.signals.douyin_log.emit, keep_browser=True)
            state = LoginState.LOGGED_IN if cookie else LoginState.NOT_LOGGED
            message = "Cookie 已刷新并保存；浏览器保持打开供页面签名使用" if cookie else "未能刷新 Cookie；请查看日志和浏览器窗口"
            self.signals.status.emit(state.value, message)

        threading.Thread(target=work, daemon=True).start()

    def test_parse(self):
        if not self.features["douyin"]:
            self.parse_output.setPlainText("抖音解析已关闭。")
            return
        text = self.share_input.toPlainText().strip()
        if not text:
            self.parse_output.setPlainText("请粘贴抖音分享文案或链接。")
            return
        url = extract_share_url(text)
        if not url:
            self.parse_output.setPlainText("未找到支持的抖音链接。")
            return
        self._parse_running = True
        self._set_busy(False)
        self.parse_output.setPlainText(f"正在解析：{url}")
        self.set_status("状态：正在解析作品信息…", "#d99b00")
        cookie = self.manager.store.load()

        def work():
            parser = DouyinParser(cookie=cookie, timeout=20)
            try:
                try:
                    info = parser.parse_text(text)
                    links = [info.play_url] if info.play_url else [image.url for image in info.images if image.url]
                    lines = ["解析成功", f"标题：{info.title or '（无标题）'}", f"作者：{info.author or '未知'}",
                             f"作品 ID：{info.item_id}", f"类型：{'图文' if info.is_image_post else '视频'}"]
                    if not info.is_image_post:
                        lines.extend((f"时长：{info.duration_text}", f"分辨率：{info.resolution_text}"))
                    lines.append(f"直链（{len(links)} 条）：")
                    lines.extend(links or ["（接口未返回媒体直链）"])
                    result = "\n".join(lines)
                    status = (LoginState.LOGGED_IN.value, "解析成功，未执行下载")
                except Exception as exc:
                    result = f"解析失败：{type(exc).__name__}: {exc}\n链接：{url}"
                    status = (LoginState.NOT_LOGGED.value, f"解析失败：{type(exc).__name__}")
                self.signals.parse_result.emit(result, *status)
            finally:
                parser.session.close()

        threading.Thread(target=work, daemon=True).start()

    def _on_parse_result(self, result: str, state: str, message: str):
        self.parse_output.setPlainText(result)
        self._parse_running = False
        self._set_busy(False)
        color = "#2e8b57" if state == LoginState.LOGGED_IN.value else "#c4314b"
        self.set_status(f"状态：{message}", color)

    def check_login(self):
        if not self.features["douyin"]:
            self.set_status("状态：抖音解析已关闭，未检测登录态", "#8a8f98")
            return
        self._set_busy(True)
        self.set_status("状态：正在检查抖音登录态…", "#d99b00")
        self.manager.check_login_async(self._result_callback)

    def clear_persistence(self):
        profile = Path(self.manager.store.path).parent / ".browser_profile"
        reply = QMessageBox.warning(
            self, "清空抖音本地登录态",
            "将永久删除抖音 Cookie 和专用浏览器 profile（包含其中保存的登录态）。\n\n"
            "此操作不会删除视频文件，也不会影响 Edge/Chrome 的其他用户配置。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        try:
            close_active_browser()
            if sys.platform == "win32" and profile.exists():
                script = '$ErrorActionPreference="SilentlyContinue"; Get-CimInstance Win32_Process -Filter "Name=''msedge.exe'' or Name=''chrome.exe''" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress'
                proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                      capture_output=True, text=True, timeout=15,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc.returncode != 0:
                    raise RuntimeError("无法确认抖音浏览器已关闭，未删除 profile")
                processes = json.loads(proc.stdout.strip()) if proc.stdout.strip() else []
                if isinstance(processes, dict):
                    processes = [processes]
                profile_path = str(profile.resolve()).casefold()
                holders = [item for item in processes
                           if profile_path in str(item.get("CommandLine") or "").replace("/", "\\").casefold()
                           and "--test-type=webdriver" in str(item.get("CommandLine") or "").lower()
                           and "--type=" not in str(item.get("CommandLine") or "").lower()]
                if holders:
                    pids = ", ".join(str(item.get("ProcessId")) for item in holders)
                    raise RuntimeError(f"Selenium 浏览器仍在运行（PID {pids}），请等待退出后再清理")
            self.manager.store.clear()
            if profile.exists():
                shutil.rmtree(profile)
            self.manager.cookie = ""
            self.manager.state = LoginState.NOT_LOGGED
            self.set_status("状态：本地 Cookie 和浏览器 profile 已清空", "#2e8b57")
            self.append_log("已清空抖音 cookies.json 和 .browser_profile。")
        except Exception as exc:
            self.set_status(f"状态：清理失败：{exc}", "#c4314b")
            self.append_log(f"清理失败：{type(exc).__name__}: {exc}")
        finally:
            self._set_busy(False)

    def _on_status(self, state: str, message: str):
        colors = {LoginState.CHECKING.value: "#d99b00", LoginState.WAITING.value: "#d99b00",
                  LoginState.LOGGED_IN.value: "#2e8b57", LoginState.NOT_LOGGED.value: "#c4314b",
                  LoginState.UNKNOWN.value: "#8a8f98"}
        self.set_status(f"状态：{message}", colors.get(state, "#8a8f98"))
        if state not in (LoginState.CHECKING.value, LoginState.WAITING.value):
            self._set_busy(False)
            self.append_log(f"登录流程结束：{message}")


class BilibiliPage(LoginPage):
    def __init__(self, signals: UiSignals):
        super().__init__("哔哩哔哩登录", "通过系统浏览器扫码登录；Cookie 保存在独立本地文件。")
        self.signals = signals
        self.log_belong = "哔哩哔哩"
        self._last_login_log: tuple[str, str] | None = None
        self.manager = BilibiliLoginManager()
        self.feature_store = PlatformFeatureStore()
        self.features = self.feature_store.load()
        self.enable_switch = SwitchButton(self)
        self.enable_switch.setOnText("启用该解析")
        self.enable_switch.setOffText("已关闭")
        self.enable_switch.setChecked(self.features["bilibili"])
        self.title_layout.addWidget(self.enable_switch)
        self.enable_switch.checkedChanged.connect(self._on_enabled_changed)
        self.login_button = PushButton("验证/扫码登录", self, FIF.QRCODE)
        self.refresh_button = PushButton("复用 profile 刷新 Cookie", self, FIF.SYNC)
        self.clear_button = PushButton("清空本地持久化", self, FIF.DELETE)
        self.check_button = PushButton("检查登录状态", self, FIF.INFO)
        self.ffmpeg_check_button = PushButton("检测 FFmpeg", self, FIF.INFO)
        self.ffmpeg_install_button = PushButton("一键安装 FFmpeg", self, FIF.DOWNLOAD)
        self.quality_store = BilibiliSettingsStore()
        self.quality_combo = ComboBox(self)
        saved_quality = self.quality_store.load()["default_quality_id"]
        for quality_id, label in QUALITY_CHOICES:
            self.quality_combo.addItem(label, userData=quality_id)
        saved_index = self.quality_combo.findData(saved_quality)
        self.quality_combo.setCurrentIndex(max(0, saved_index))
        self.quality_combo.currentIndexChanged.connect(self._save_default_quality)
        self.parse_input = TextEdit(self)
        self.parse_input.setPlaceholderText("粘贴 Bilibili 视频链接或分享文案")
        self.parse_input.setMaximumHeight(90)
        self.parse_button = PushButton("试解析", self, FIF.SEARCH)
        self.parse_output = TextEdit(self)
        self.parse_output.setReadOnly(True)
        self.parse_output.setPlaceholderText("解析结果和当前账号可用清晰度将在此显示，不下载视频")
        self.parse_output.setMinimumHeight(150)
        self.layout.insertWidget(4, self.login_button)
        self.layout.insertWidget(5, self.refresh_button)
        self.layout.insertWidget(6, self.clear_button)
        self.layout.insertWidget(7, self.check_button)
        self.layout.insertWidget(8, self.ffmpeg_check_button)
        self.layout.insertWidget(9, self.ffmpeg_install_button)
        self.layout.insertWidget(10, StrongBodyLabel("默认清晰度"))
        self.layout.insertWidget(11, self.quality_combo)
        self.layout.insertWidget(12, StrongBodyLabel("分享链接试解析"))
        self.layout.insertWidget(13, self.parse_input)
        self.layout.insertWidget(14, self.parse_button)
        self.layout.insertWidget(15, self.parse_output)
        self.login_button.clicked.connect(self.start_login)
        self.refresh_button.clicked.connect(self.refresh_cookie)
        self.clear_button.clicked.connect(self.clear_persistence)
        self.check_button.clicked.connect(self.check_login)
        self.ffmpeg_check_button.clicked.connect(self.check_ffmpeg)
        self.ffmpeg_install_button.clicked.connect(self.install_ffmpeg)
        self.parse_button.clicked.connect(self.test_parse)
        signals.bilibili_status.connect(self._on_status)
        signals.bilibili_log.connect(self.append_log)
        signals.bilibili_parse_result.connect(self._on_parse_result)
        signals.ffmpeg_result.connect(self._on_ffmpeg_result)
        self.signals.bilibili_log.emit(f"Cookie 文件：{self.manager.cookie_path}")
        self.signals.bilibili_log.emit(f"浏览器 profile：{self.manager.profile_dir}")
        QTimer.singleShot(0, self.check_login)

    def _on_enabled_changed(self, enabled: bool):
        self.features["bilibili"] = bool(enabled)
        self.feature_store.save(self.features)
        self.signals.platform_enabled_changed.emit("bilibili", bool(enabled))
        if not enabled:
            self.set_status("状态：哔哩哔哩解析已停用", "#8a8f98")
        self.append_log(f"哔哩哔哩解析{'已启用' if enabled else '已停用'}")

    def _run(self, operation):
        for button in (
            self.login_button, self.refresh_button, self.clear_button, self.check_button,
            self.ffmpeg_check_button, self.ffmpeg_install_button, self.parse_button,
        ):
            button.setEnabled(False)
        self.set_status("状态：任务进行中…", "#d99b00")

        def work():
            try:
                ok, message = operation()
            except Exception as exc:
                ok, message = False, f"任务失败：{type(exc).__name__}: {exc}"
            self.signals.bilibili_status.emit("logged_in" if ok else "not_logged", message)

        threading.Thread(target=work, daemon=True).start()

    def _save_default_quality(self, _index: int):
        quality_id = self.quality_combo.currentData()
        if quality_id is not None:
            self.quality_store.save(int(quality_id))

    def check_ffmpeg(self):
        executable = find_ffmpeg()
        if executable:
            self.set_status("状态：FFmpeg 已就绪", "#2e8b57")
            self.signals.bilibili_log.emit(f"FFmpeg 已就绪：{ffmpeg_version(executable)}")
        else:
            self.set_status("状态：未找到 FFmpeg，请点击一键安装", "#c4314b")
            self.signals.bilibili_log.emit("未找到 FFmpeg；Bilibili DASH 视频需要先安装 FFmpeg", "warning")

    def install_ffmpeg(self):
        for button in (self.login_button, self.refresh_button, self.clear_button, self.check_button, self.ffmpeg_check_button, self.ffmpeg_install_button, self.parse_button):
            button.setEnabled(False)
        self.set_status("状态：正在下载并安装 FFmpeg…", "#d99b00")

        def work():
            try:
                executable = install_ffmpeg()
                self.signals.ffmpeg_result.emit(True, f"FFmpeg 安装成功：{ffmpeg_version(executable)}")
            except Exception as exc:
                self.signals.ffmpeg_result.emit(False, f"FFmpeg 安装失败：{type(exc).__name__}: {exc}")

        threading.Thread(target=work, name="bilibili-ffmpeg-install", daemon=True).start()

    def _on_ffmpeg_result(self, success: bool, message: str):
        self.set_status(f"状态：{message}", "#2e8b57" if success else "#c4314b")
        self.signals.bilibili_log.emit(message, "info" if success else "error")
        for button in (self.login_button, self.refresh_button, self.clear_button, self.check_button, self.ffmpeg_check_button, self.ffmpeg_install_button, self.parse_button):
            button.setEnabled(True)

    def refresh_cookie(self):
        self._run(lambda: self.manager.refresh_cookie(self.signals.bilibili_log.emit))

    def start_login(self):
        cookies = self.manager.load()
        if self.manager.has_login_cookie(cookies):
            self._run(self.manager.check_login)
        else:
            self._run(lambda: self.manager.login(self.signals.bilibili_log.emit))

    def test_parse(self):
        if not self.features["bilibili"]:
            self.parse_output.setPlainText("哔哩哔哩解析已关闭。")
            return
        text = self.parse_input.toPlainText().strip()
        url = extract_bilibili_url(text)
        if not url:
            self.parse_output.setPlainText("未找到支持的 Bilibili 视频链接。")
            return
        self.parse_button.setEnabled(False)
        self.parse_output.setPlainText(f"正在解析：{url}")
        self.set_status("状态：正在解析 Bilibili 视频…", "#d99b00")
        cookies = self.manager.load()

        def work():
            parser = BilibiliParser(cookies=cookies, timeout=25)
            try:
                info = parser.parse(url)
                default_quality = select_default_quality(info.qualities, self.quality_combo.currentData())
                lines = [
                    "解析成功",
                    f"BVID：{info.bvid}",
                    f"标题：{info.title or '（无标题）'}",
                    f"作者：{info.author or '未知'}",
                    f"分 P：{info.page} · {info.part or '默认分 P'}",
                    f"可用清晰度：{len(info.qualities)} 档",
                ]
                lines.extend(f"- {item.name} ({item.width}x{item.height})" for item in info.qualities)
                if default_quality:
                    lines.append(f"默认选择：{default_quality.name} ({default_quality.width}x{default_quality.height})")
                result = "\\n".join(lines)
                self.signals.bilibili_parse_result.emit(result, "logged_in", "解析成功")
            except Exception as exc:
                self.signals.bilibili_parse_result.emit(
                    f"解析失败：{type(exc).__name__}: {exc}", "not_logged", "解析失败"
                )
            finally:
                parser.close()

        threading.Thread(target=work, name="bilibili-preview-parser", daemon=True).start()

    def _on_parse_result(self, result: str, state: str, message: str):
        self.parse_output.setPlainText(result)
        self.parse_button.setEnabled(True)
        color = "#2e8b57" if state == "logged_in" else "#c4314b"
        self.set_status(f"状态：{message}", color)

    def check_login(self):
        if not self.features["bilibili"]:
            self.set_status("状态：哔哩哔哩解析已关闭，未检测登录态", "#8a8f98")
            return
        self._run(self.manager.check_login)

    def _on_status(self, state: str, message: str):
        color = "#2e8b57" if state == "logged_in" else "#c4314b"
        self.set_status(f"状态：{message}", color)
        login_result = (state, message)
        if login_result != self._last_login_log:
            self._last_login_log = login_result
            self.signals.bilibili_log.emit(
                f"登录流程结束：{message}", "info" if state == "logged_in" else "error"
            )
        for button in (
            self.login_button, self.refresh_button, self.clear_button, self.check_button,
            self.ffmpeg_check_button, self.ffmpeg_install_button, self.parse_button,
        ):
            button.setEnabled(True)

    def clear_persistence(self):
        reply = QMessageBox.warning(
            self, "清空哔哩哔哩本地登录态",
            "将删除 Bilibili Cookie 和浏览器 profile。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.manager.clear()
            if self.manager.profile_dir.exists():
                shutil.rmtree(self.manager.profile_dir)
            self.set_status("状态：Bilibili 本地登录态已清空", "#2e8b57")
            self.signals.bilibili_log.emit("已清空 cookies.json 和 .browser_profile")
        except Exception as exc:
            self.set_status(f"状态：清理失败：{exc}", "#c4314b")
            self.signals.bilibili_log.emit(f"清理失败：{type(exc).__name__}: {exc}")


class StatusIndicator(QWidget):
    COLORS = {"unknown": "#8a8f98", "checking": "#d99b00", "offline": "#c4314b", "online": "#2e8b57"}

    def __init__(self, title: str, icon: str = ""):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        self.icon_label = QLabel(self) if icon else None
        if self.icon_label:
            self.icon_label.setPixmap(QIcon(icon).pixmap(18, 18))
            self.icon_label.setFixedSize(22, 22)
            row.addWidget(self.icon_label)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(18)
        self.label = QLabel(title)
        self.detail = QLabel("未检测")
        self.detail.setStyleSheet("color: #667085;")
        row.addWidget(self.dot)
        row.addWidget(self.label)
        row.addWidget(self.detail, 1)
        self.set_state("unknown", "未检测")

    def set_state(self, state: str, detail: str):
        color = self.COLORS.get(state, self.COLORS["unknown"])
        self.dot.setStyleSheet(f"color: {color}; font-size: 17px;")
        self.detail.setText(detail)


class NapCatPage(LoginPage):
    def __init__(self, signals: UiSignals):
        super().__init__("NapCat WebSocket", "配置 OneBot 11 正向 WebSocket。读超时须大于心跳间隔。")
        self.log_belong = "QQbot"
        self.signals = signals
        self.store = ConfigStore()
        self.config = self.store.load()
        self.ws_url = QLineEdit(self.config.ws_url)
        self.token = QLineEdit(self.config.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("鉴权 Token（可留空）")
        self.reconnect = SpinBox()
        self.reconnect.setRange(1, 120)
        self.reconnect.setSuffix(" 秒")
        self.reconnect.setValue(round(self.config.reconnect_interval))
        self.read_timeout = SpinBox()
        self.read_timeout.setRange(31, 600)
        self.read_timeout.setSuffix(" 秒")
        self.read_timeout.setValue(round(self.config.read_timeout))
        self.heartbeat = SpinBox()
        self.heartbeat.setRange(10000, 120000)
        self.heartbeat.setSingleStep(5000)
        self.heartbeat.setSuffix(" ms")
        self.heartbeat.setValue(self.config.heartbeat_interval)
        for spin in (self.reconnect, self.read_timeout, self.heartbeat):
            spin.setObjectName("napcatSpinBox")
            spin.upButton.setFixedSize(24, 24)
            spin.downButton.setFixedSize(24, 24)
            spin.upButton.setToolTip("增加")
            spin.downButton.setToolTip("减少")
        self.backend_choice = QButtonGroup(self)
        self.backend_choice.setExclusive(True)
        self.backend_radios = {}
        backend_row = QWidget(self)
        backend_layout = QHBoxLayout(backend_row)
        backend_layout.setContentsMargins(0, 0, 0, 0)
        for value, title in (("auto", "Auto"), ("napcat", "NapCat"), ("snowluma", "SnowLuma")):
            radio = RadioButton(title, backend_row)
            radio.setProperty("backend", value)
            self.backend_choice.addButton(radio)
            self.backend_radios[value] = radio
            backend_layout.addWidget(radio)
            if self.config.backend == value:
                radio.setChecked(True)
        if self.config.backend not in self.backend_radios:
            self.backend_radios["auto"].setChecked(True)
        self.listen = QPlainTextEdit("\n".join(self.config.listen))
        self.listen.setPlaceholderText("一行一个：*QQ号（私聊）或 #群号（群聊）")
        self.listen.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.listen.setMinimumHeight(360)
        self.save_button = PushButton("保存配置", self, FIF.SAVE)
        self.check_button = PushButton("检测 WebSocket", self, FIF.SYNC)
        self.connection_status = StatusIndicator("NapCat WebSocket", make_icon("link.svg"))
        self.port_probe: PortProbe | None = None
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow("连接后端", backend_row)
        form.addRow("WS 地址", self.ws_url)
        form.addRow("鉴权 Token", self.token)
        form.addRow("重连间隔", self.reconnect)
        form.addRow("读超时", self.read_timeout)
        form.addRow("心跳间隔", self.heartbeat)
        # LoginPage 默认把状态、日志和底部 stretch 放在主布局中；NapCat 将它们并入左栏，
        # 让右侧白名单面板成为和左栏同高的唯一主内容区。
        self.layout.removeWidget(self.status)
        self.status.hide()
        self.layout.removeWidget(self.log_box)
        if self.layout.count() and self.layout.itemAt(self.layout.count() - 1).spacerItem():
            self.layout.takeAt(self.layout.count() - 1)

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.addLayout(form)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.save_button)
        actions.addWidget(self.check_button)
        left_layout.addLayout(actions)
        left_layout.addWidget(self.connection_status)
        left_layout.addWidget(StrongBodyLabel("运行日志"))
        self.log_box.setMinimumHeight(120)
        self.log_box.setMaximumHeight(16777215)
        left_layout.addWidget(self.log_box, 1)

        whitelist_frame = QFrame(self)
        whitelist_frame.setFrameShape(QFrame.Shape.StyledPanel)
        whitelist_frame.setObjectName("whitelistFrame")
        whitelist_frame.setSizePolicy(whitelist_frame.sizePolicy().horizontalPolicy(),
                                      whitelist_frame.sizePolicy().verticalPolicy().Expanding)
        whitelist_frame.setMinimumWidth(250)
        whitelist_layout = QVBoxLayout(whitelist_frame)
        whitelist_layout.setContentsMargins(14, 12, 14, 14)
        whitelist_layout.setSpacing(8)
        whitelist_layout.addWidget(StrongBodyLabel("监听白名单"))
        whitelist_layout.addWidget(QLabel("一行一个：*QQ号（私聊）或 #群号（群聊）"))
        whitelist_layout.addWidget(self.listen, 1)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(left_panel, 1)
        columns.addWidget(whitelist_frame, 1)
        self.layout.insertLayout(3, columns, 1)
        self.save_button.clicked.connect(self.save_config)
        self.check_button.clicked.connect(self.check_connection)
        signals.napcat_port_status.connect(self._on_port_probe_result)
        signals.napcat_probe_done.connect(self._on_probe_done)
        self.restart_port_probe(self.config.ws_url)

    def build_config(self) -> NapCatConfig:
        entries = [line.strip() for line in self.listen.toPlainText().splitlines() if line.strip()]
        backend = next((str(button.property("backend")) for button in self.backend_choice.buttons() if button.isChecked()), "auto")
        return NapCatConfig(
            ws_url=self.ws_url.text().strip(),
            token=self.token.text(),
            reconnect_interval=float(self.reconnect.value()),
            read_timeout=float(self.read_timeout.value()),
            heartbeat_interval=self.heartbeat.value(),
            backend=backend,
            listen=entries,
        )

    def save_config(self, show_dialog: bool = True) -> NapCatConfig | None:
        try:
            config = self.build_config()
            self.store.save(config)
            (ROOT / "QQBot" / "allow.txt").write_text("\n".join(config.listen) + ("\n" if config.listen else ""), encoding="utf-8")
            self.config = config
            self.restart_port_probe(config.ws_url)
            self.append_log(f"配置已保存：{self.store.path}")
            self.signals.napcat_config_saved.emit(config)
            return config
        except Exception as exc:
            if show_dialog:
                QMessageBox.critical(self, "配置无效", str(exc))
            else:
                self.append_log(f"配置无效：{exc}")
            return None

    def restart_port_probe(self, ws_url: str):
        if self.port_probe:
            self.port_probe.stop()
        self.connection_status.set_state("unknown", "正在检测目标端口…")
        self.port_probe = PortProbe(
            ws_url,
            self.signals.napcat_port_status.emit,
            interval=5,
        )
        self.port_probe.start()

    def check_connection(self):
        config = self.save_config()
        if config is None:
            return
        self.check_button.setEnabled(False)

        def probe_once():
            if self.port_probe:
                ok, detail = self.port_probe.probe_once()
                self.signals.napcat_status.emit("connected" if ok else "disconnected", detail)
            self.signals.napcat_probe_done.emit()

        threading.Thread(target=probe_once, name="napcat-port-probe-once", daemon=True).start()

    def close_probe(self):
        if self.port_probe:
            self.port_probe.stop()
            self.port_probe = None

    def _on_probe_done(self):
        self.check_button.setEnabled(True)

    def _on_port_probe_result(self, reachable: bool, detail: str):
        self.connection_status.set_state("online" if reachable else "offline", detail)


class RuntimePage(LoginPage):
    def __init__(self, signals: UiSignals, douyin_page: DouyinPage, bilibili_page: BilibiliPage, napcat_page: NapCatPage):
        super().__init__("运行控制", "启动会顺序检查抖音、Bilibili 登录态和 NapCat QQ 登录状态。")
        self.signals = signals
        self.douyin_page = douyin_page
        self.bilibili_page = bilibili_page
        self.napcat_page = napcat_page
        self.runtime: NapCatRuntime | None = None
        self.bot_service: DouyinLinkBot | None = None
        self._generation = 0
        self._start_lock = threading.Lock()
        self._startup_finished = False
        self._auto_probe = False
        self._platforms_checked = False
        self.platform_features = PlatformFeatureStore()
        self.enabled_platforms = self.platform_features.load()
        self.douyin_status = StatusIndicator("抖音")
        self.bilibili_status = StatusIndicator("哔哩哔哩")
        self.napcat_status = StatusIndicator("NapCat", make_icon("link.svg"))
        self.start_button = PushButton("启动机器人", self, FIF.PLAY)
        self.stop_button = PushButton("停止", self, FIF.CANCEL)
        self.stop_button.setEnabled(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(220)
        self.layout.removeWidget(self.status)
        self.status.hide()
        self.layout.removeWidget(self.log_box)
        self.log_box.hide()
        if self.layout.count() and self.layout.itemAt(self.layout.count() - 1).spacerItem():
            self.layout.takeAt(self.layout.count() - 1)
        status_rows = QVBoxLayout()
        status_rows.setSpacing(12)
        for indicator in (self.douyin_status, self.bilibili_status, self.napcat_status):
            status_rows.addWidget(indicator)
        status_rows.addStretch(1)
        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        left_layout.addLayout(status_rows)
        left_layout.addLayout(actions)
        left_layout.addStretch(1)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(StrongBodyLabel("运行日志"))
        right_layout.addWidget(self.log_view, 1)

        columns = QHBoxLayout()
        columns.setSpacing(20)
        columns.addWidget(left_panel, 1)
        columns.addWidget(right_panel, 1)
        self.layout.insertLayout(3, columns, 1)
        self.start_button.clicked.connect(self.start_bot)
        self.stop_button.clicked.connect(self.stop_bot)
        signals.status.connect(self._on_douyin_status)
        signals.bilibili_status.connect(self._on_bilibili_status)
        signals.napcat_port_status.connect(self._on_napcat_port_status)
        signals.runtime_status.connect(self._on_runtime_status)
        signals.napcat_config_saved.connect(self._on_config_saved)
        signals.platform_enabled_changed.connect(self._on_platform_enabled_changed)
        signals.log.connect(self.append_log)
        if not self.enabled_platforms["douyin"]:
            self.douyin_status.set_state("unknown", "解析已关闭")
        if not self.enabled_platforms["bilibili"]:
            self.bilibili_status.set_state("unknown", "解析已关闭")

    def append_log(self, message: str):
        if " | " not in message:
            message = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | info | 全局 | {message}"
        self.log_view.appendPlainText(message)

    def _on_config_saved(self, _config):
        if self.runtime and self.runtime.running:
            self.append_log("配置已保存；当前连接仍使用启动时配置。")

    def _on_platform_enabled_changed(self, platform: str, enabled: bool):
        self.enabled_platforms[platform] = bool(enabled)
        indicator = {"douyin": self.douyin_status, "bilibili": self.bilibili_status}.get(platform)
        if indicator and not enabled:
            indicator.set_state("unknown", "解析已关闭，跳过检测")
        elif indicator:
            indicator.set_state("unknown", "待下次检测")

    def _on_douyin_status(self, state: str, message: str):
        mapped = {"checking": "checking", "waiting": "checking", "logged_in": "online", "not_logged": "offline"}
        self.douyin_status.set_state(mapped.get(state, "unknown"), message)

    def _on_bilibili_status(self, state: str, message: str):
        mapped = {"checking": "checking", "logged_in": "online", "not_logged": "offline", "unknown": "unknown"}
        self.bilibili_status.set_state(mapped.get(state, "unknown"), message)

    def _on_napcat_port_status(self, reachable: bool, message: str):
        self.napcat_status.set_state("online" if reachable else "offline", message)

    def _on_runtime_status(self, state: str, message: str):
        self.append_log(message)
        if state == "running":
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.set_status("状态：机器人正在监听", "#2e8b57")
        elif state == "checked":
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.set_status(f"状态：{message}", "#2e8b57")
        elif state in ("stopped", "failed"):
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.set_status(f"状态：{message}", "#c4314b" if state == "failed" else "#8a8f98")

    def start_bot(self):
        self._run_startup_check(auto_probe=False)

    def run_initial_check(self):
        self._run_startup_check(auto_probe=True)

    def _run_startup_check(self, auto_probe: bool):
        if not self._start_lock.acquire(blocking=False):
            return
        self._generation += 1
        generation = self._generation
        self._auto_probe = auto_probe
        self._startup_finished = False
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(not auto_probe)
        self.set_status("状态：正在顺序检查平台账号…", "#d99b00")
        self.signals.runtime_status.emit("checking", "启动时检测：抖音 → Bilibili → NapCat" if auto_probe else "启动检查：抖音 → Bilibili → NapCat")
        for indicator in (self.douyin_status, self.bilibili_status, self.napcat_status):
            indicator.set_state("checking", "检查中…")
        config = self.napcat_page.save_config(show_dialog=False)
        if config is None:
            self._finish_startup(generation, False, "NapCat 配置无效，请查看日志")
            return
        threading.Thread(target=self._startup_checks, args=(generation, config), name="qqbot-startup", daemon=True).start()

    def _startup_checks(self, generation: int, config: NapCatConfig):
        try:
            douyin_enabled = self.enabled_platforms.get("douyin", True)
            bili_enabled = self.enabled_platforms.get("bilibili", True)
            self.signals.log.emit("检查 1/3：抖音登录态" if douyin_enabled else "检查 1/3：抖音解析已关闭，跳过登录检查")
            if douyin_enabled:
                cookie = self.douyin_page.manager.store.load()
                if cookie:
                    from douyin_core.login_manager import verify_cookie_online
                    try:
                        douyin_valid = bool(verify_cookie_online(cookie))
                    except Exception as exc:
                        douyin_valid = False
                        self.signals.log.emit(f"抖音检查异常：{type(exc).__name__}: {exc}")
                else:
                    douyin_valid = False
                douyin_message = "登录有效" if douyin_valid else "未登录或登录态未通过在线检查"
                self.signals.status.emit(LoginState.LOGGED_IN.value if douyin_valid else LoginState.NOT_LOGGED.value, douyin_message)
                if not douyin_valid:
                    self.douyin_status.set_state("offline", douyin_message)
            else:
                douyin_valid = True
                self.douyin_status.set_state("unknown", "解析已关闭，跳过检测")

            if not self._is_current_generation(generation):
                return
            self.signals.log.emit("检查 2/3：Bilibili 登录态" if bili_enabled else "检查 2/3：Bilibili 解析已关闭，跳过登录检查")
            if bili_enabled:
                try:
                    bili_valid, bili_message = self.bilibili_page.manager.check_login(timeout=12)
                except Exception as exc:
                    bili_valid, bili_message = None, f"检查异常：{type(exc).__name__}: {exc}"
                bili_state = "logged_in" if bili_valid is True else "not_logged" if bili_valid is False else "unknown"
                self.signals.bilibili_status.emit(bili_state, bili_message)
            else:
                bili_valid, bili_message = True, "解析已关闭，跳过检测"
                self.bilibili_status.set_state("unknown", bili_message)

            if not self._is_current_generation(generation):
                return
            self.signals.log.emit("检查 3/3：NapCat WS 与 QQ 登录信息")
            precheck_message = ""
            if douyin_enabled and not douyin_valid:
                precheck_message = "抖音未登录或登录态检查失败"
            elif bili_enabled and bili_valid is not True:
                precheck_message = f"Bilibili 登录态未确认：{bili_message}"
            elif not self._auto_probe:
                try:
                    config.validate_listeners()
                except Exception as exc:
                    precheck_message = str(exc)
            self._launch_runtime(generation, config, auto_probe=self._auto_probe, precheck_message=precheck_message)
        except Exception as exc:
            self._finish_startup(generation, False, f"启动检查异常：{type(exc).__name__}: {exc}")

    def _launch_runtime(self, generation: int, config: NapCatConfig, auto_probe: bool, precheck_message: str = ""):
        effective_probe = auto_probe or bool(precheck_message)
        if self.runtime is None or not (self.runtime.running and self.runtime.self_id):
            if self.runtime is not None:
                self.runtime.stop(notify=False)
            self.runtime = NapCatRuntime(
                config,
                on_status=self.signals.napcat_status.emit,
                on_log=self.signals.log.emit,
                reconnect=not effective_probe,
            )
        if not effective_probe:
            self._attach_bot_service(config)
        try:
            if not self.runtime.self_id and not self.runtime.running:
                self.runtime.start(require_listeners=not effective_probe)
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline and self._is_current_generation(generation):
                if self.runtime.self_id:
                    if effective_probe:
                        self._platforms_checked = not precheck_message
                        detail = f"NapCat 在线：QQ {self.runtime.self_id}；{precheck_message or '机器人尚未启动'}"
                        self._finish_startup(generation, not precheck_message, detail, keep_napcat=True)
                    else:
                        self._attach_bot_service(config)
                        self._startup_finished = True
                        self.start_button.setEnabled(False)
                        self.stop_button.setEnabled(True)
                        self.set_status("状态：机器人正在监听", "#2e8b57")
                        self.signals.runtime_status.emit("running", f"机器人已启动，QQ：{self.runtime.self_id}，正在监听白名单消息")
                        if self._start_lock.locked():
                            self._start_lock.release()
                    return
                if not self.runtime.running:
                    break
                time.sleep(0.1)
            if self._is_current_generation(generation):
                self._finish_startup(generation, False, "NapCat 未返回 QQ 登录信息；请检查 WS、Token 和 QQ 登录状态")
        except Exception as exc:
            self._finish_startup(generation, False, f"NapCat 启动失败：{type(exc).__name__}: {exc}")

    def _attach_bot_service(self, config: NapCatConfig):
        if self.runtime is None:
            raise RuntimeError("NapCat runtime 尚未连接")
        self.bot_service = DouyinLinkBot(
            config,
            self.runtime,
            self.signals.log.emit,
            douyin_log=self.signals.douyin_log.emit,
            bilibili_log=self.signals.bilibili_log.emit,
        )
        self.runtime.on_event = self.bot_service.on_event

    def _finish_startup(self, generation: int, success: bool, message: str, keep_napcat: bool = False):
        if not self._is_current_generation(generation):
            return
        runtime = self.runtime
        if runtime is not None and not (keep_napcat and runtime.self_id):
            runtime.stop(notify=False)
            self.runtime = None
        self.bot_service = None
        self._startup_finished = False
        if success:
            self.signals.runtime_status.emit("checked", message)
        else:
            self.signals.runtime_status.emit("failed", message)
        if self._start_lock.locked():
            self._start_lock.release()

    def _is_current_generation(self, generation: int) -> bool:
        return generation == self._generation

    def _fail_start(self, generation: int, message: str):
        if not self._is_current_generation(generation):
            return
        self._startup_finished = False
        self.signals.runtime_status.emit("failed", message)
        runtime = self.runtime
        self.runtime = None
        self.bot_service = None
        if runtime:
            runtime.stop()
        if self._start_lock.locked():
            self._start_lock.release()

    def stop_bot(self):
        self._generation += 1
        was_running = self._startup_finished
        self._startup_finished = False
        runtime = self.runtime
        self.runtime = None
        self.bot_service = None
        if runtime:
            runtime.stop()
        if self._start_lock.locked():
            self._start_lock.release()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if was_running:
            self.signals.runtime_status.emit("stopped", "机器人已停止")
        else:
            self.signals.runtime_status.emit("stopped", "自动检测已取消")


def make_icon(name: str):
    return str(ICON_DIR / name)


class LoginCenter(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("loginCenter")
        self.setWindowTitle("视频解析机器人 · 控制台")
        self.setMinimumSize(900, 650)
        self._window_settings = QSettings("VideoAnalysisQQBot", "LoginCenter")
        self.resize(1120, 780)
        self.stackedWidget.setObjectName("contentStack")
        self.stackedWidget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowIcon(QIcon(make_icon("video.svg")))
        self.titleBar.setTitle("视频解析机器人")
        self.titleBar.setIcon(make_icon("video.svg"))
        self.setStyleSheet("""
            FluentWindow { background: #f5f7f9; }
            QWidget#contentStack {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 8px;
            }
            QWidget#loginPage { background: transparent; }
            QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox {
                border: 1px solid #d7dce1;
                border-radius: 6px;
                background: #ffffff;
                padding: 6px 8px;
            }
        """)

        self.signals = UiSignals()
        douyin = DouyinPage(self.signals)
        bilibili = BilibiliPage(self.signals)
        napcat = NapCatPage(self.signals)
        runtime = RuntimePage(self.signals, douyin, bilibili, napcat)
        self.pages = {"runtime": runtime, "douyin": douyin, "bilibili": bilibili, "napcat": napcat}
        keys = {"runtime": "runtimePage", "douyin": "douyinPage", "bilibili": "bilibiliPage", "napcat": "napcatPage"}
        for key, page in self.pages.items():
            page.setObjectName(keys[key])
        self.addSubInterface(runtime, make_icon("play.svg"), "运行控制")
        self.addSubInterface(douyin, make_icon("video.svg"), "抖音登录")
        self.addSubInterface(bilibili, make_icon("video.svg"), "哔哩哔哩登录")
        self.addSubInterface(
            napcat,
            make_icon("link.svg"),
            "NapCat WebSocket",
            position=NavigationItemPosition.BOTTOM,
        )
        self.switchTo(runtime)
        self._restore_window_state()
        QTimer.singleShot(0, runtime.run_initial_check)

    def _restore_window_state(self):
        settings = self._window_settings
        if bool(settings.value("window/maximized", False, type=bool)):
            self.showMaximized()
        else:
            width = settings.value("window/width", 1120, type=int)
            height = settings.value("window/height", 780, type=int)
            x = settings.value("window/x", None, type=int)
            y = settings.value("window/y", None, type=int)
            self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))
            if x is not None and y is not None:
                self.move(x, y)
        available = QApplication.primaryScreen().availableGeometry()
        if not available.intersects(self.frameGeometry()):
            self.move(available.topLeft())

    def closeEvent(self, event):
        normal_geometry = self.normalGeometry()
        self._window_settings.setValue("window/x", normal_geometry.x())
        self._window_settings.setValue("window/y", normal_geometry.y())
        self._window_settings.setValue("window/width", normal_geometry.width())
        self._window_settings.setValue("window/height", normal_geometry.height())
        self._window_settings.setValue("window/maximized", self.isMaximized())
        self._window_settings.sync()
        runtime = self.pages.get("runtime")
        if runtime:
            runtime.stop_bot()
        close_active_browser()
        super().closeEvent(event)


def _configure_ui_font(app: QApplication) -> None:
    if sys.platform == "win32":
        font_path = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts" / "msyh.ttc"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 10))


def main() -> int:
    setTheme(Theme.LIGHT)
    app = QApplication(sys.argv)
    _configure_ui_font(app)
    window = LoginCenter()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())