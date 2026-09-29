"""NapCat/OneBot 文件消息相关的路径工具。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def to_file_uri(path: str | Path) -> str:
    """将本地文件路径转换为 OneBot 可用的 ``file:///`` URI。"""
    normalized = Path(path).resolve().as_posix()
    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = f"/{normalized}"
    return "file://" + quote(normalized, safe="/:@")
