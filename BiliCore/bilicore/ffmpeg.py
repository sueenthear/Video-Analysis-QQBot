"""Detect and install a project-local FFmpeg binary for Bilibili DASH merging."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

PROJECT_FFMPEG_DIR = Path(__file__).resolve().parent / "ffmpeg"
PROJECT_FFMPEG = PROJECT_FFMPEG_DIR / "ffmpeg.exe"
PROJECT_FFPROBE = PROJECT_FFMPEG_DIR / "ffprobe.exe"
DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def find_ffmpeg() -> str | None:
    """Return project-local or system FFmpeg executable path."""
    if PROJECT_FFMPEG.exists():
        return str(PROJECT_FFMPEG)
    system_path = shutil.which("ffmpeg")
    return system_path


def install_ffmpeg() -> str:
    """Download and install FFmpeg executables into the BiliCore package directory."""
    PROJECT_FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bili-ffmpeg-") as temp_dir:
        archive = Path(temp_dir) / "ffmpeg.zip"
        subprocess.run(
            ["curl.exe", "-L", "--fail", "--silent", "--show-error", DOWNLOAD_URL, "-o", str(archive)],
            check=True,
            timeout=300,
        )
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
            shutil.copy2(binaries["ffmpeg.exe"], PROJECT_FFMPEG)
            shutil.copy2(binaries["ffprobe.exe"], PROJECT_FFPROBE)
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
