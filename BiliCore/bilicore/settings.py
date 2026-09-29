"""Local Bilibili parser preferences."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_QUALITY_ID = 80
QUALITY_CHOICES = (
    (127, "8K"),
    (126, "杜比视界"),
    (125, "HDR 真彩"),
    (120, "4K"),
    (116, "1080P 60帧"),
    (112, "1080P 高码率"),
    (80, "1080P 高清"),
    (74, "720P 高帧率"),
    (64, "720P 准高清"),
    (32, "480P 标清"),
    (16, "360P 流畅"),
    (6, "240P 极速"),
)


def select_default_quality(qualities, default_quality_id: int):
    eligible = [item for item in qualities if item.quality_id <= int(default_quality_id)]
    return max(eligible, key=lambda item: item.quality_id) if eligible else None


class BilibiliSettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(__file__).resolve().parent / "settings.json"

    def load(self) -> dict[str, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            quality_id = int(data.get("default_quality_id", DEFAULT_QUALITY_ID))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            quality_id = DEFAULT_QUALITY_ID
        allowed = {quality for quality, _ in QUALITY_CHOICES}
        if quality_id not in allowed:
            quality_id = DEFAULT_QUALITY_ID
        return {"default_quality_id": quality_id}

    def save(self, default_quality_id: int) -> None:
        allowed = {quality for quality, _ in QUALITY_CHOICES}
        quality_id = int(default_quality_id)
        if quality_id not in allowed:
            raise ValueError("不支持的 Bilibili 默认清晰度")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps({"default_quality_id": quality_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
