"""Detect and install a project-local FFmpeg binary for Bilibili DASH merging."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_FFMPEG_DIR = PROJECT_ROOT / "ffmpeg"
PROJECT_FFMPEG = PROJECT_FFMPEG_DIR / "ffmpeg.exe"
PROJECT_FFPROBE = PROJECT_FFMPEG_DIR / "ffprobe.exe"
LEGACY_FFMPEG_DIR = Path(__file__).resolve().parent / "ffmpeg"
DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def find_ffmpeg() -> str | None:
    """Return shared project-root or system FFmpeg executable path."""
    if not PROJECT_FFMPEG.exists() and (LEGACY_FFMPEG_DIR / "ffmpeg.exe").exists():
        PROJECT_FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_FFMPEG_DIR / "ffmpeg.exe"), str(PROJECT_FFMPEG))
        legacy_probe = LEGACY_FFMPEG_DIR / "ffprobe.exe"
        if legacy_probe.exists():
            shutil.move(str(legacy_probe), str(PROJECT_FFPROBE))
    if PROJECT_FFMPEG.exists():
        return str(PROJECT_FFMPEG)
    system_path = shutil.which("ffmpeg")
    return system_path


def install_ffmpeg(progress: Callable[[str], None] | None = None) -> str:
    """Download and install shared FFmpeg executables into the project root."""
    report = progress or (lambda _message: None)
    PROJECT_FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    report("准备下载 FFmpeg 安装包…")
    with tempfile.TemporaryDirectory(prefix="bili-ffmpeg-") as temp_dir:
        archive = Path(temp_dir) / "ffmpeg.zip"
        report("正在下载 FFmpeg（安装包较大，请耐心等待）…")
        subprocess.run(
            ["curl.exe", "-L", "--fail", "--show-error", "--progress-bar", DOWNLOAD_URL, "-o", str(archive)],
            check=True,
            timeout=600,
        )
        report(f"下载完成：{archive.stat().st_size / 1024 / 1024:.1f} MB，正在解压…")
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            binaries = {}
            for filename in ("ffmpeg.exe", "ffprobe.exe"):
                match = next((name for name in names if name.replace("\\", "/").endswith("/bin/" + filename)), None)
                if match is None:
                    raise RuntimeError(f"安装包中未找到 {filename}")
                target = Path(temp_dir) / filename
                with package.open(match) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                binaries[filename] = target
            report("解压完成，正在安装 ffmpeg.exe…")
            shutil.copy2(binaries["ffmpeg.exe"], PROJECT_FFMPEG)
            report("正在安装 ffprobe.exe…")
            shutil.copy2(binaries["ffprobe.exe"], PROJECT_FFPROBE)
    report(f"FFmpeg 已安装到：{PROJECT_FFMPEG_DIR}")
    return str(PROJECT_FFMPEG)


def ffmpeg_version(executable: str | None = None) -> str:
    path = executable or find_ffmpeg()
    if not path:
        return "未安装"
    result = subprocess.run(
        [path, "-version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    first_line = (result.stdout or result.stderr).splitlines()
    return first_line[0] if first_line else "FFmpeg 已安装"
