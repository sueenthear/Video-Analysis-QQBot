"""本地平台解析功能启用状态。"""

from __future__ import annotations

import json
from pathlib import Path


class PlatformFeatureStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(__file__).resolve().parent / "platforms.json"

    def load(self) -> dict[str, bool]:
        defaults = {"douyin": True, "bilibili": True}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return defaults
        except (OSError, json.JSONDecodeError):
            return defaults
        for key in defaults:
            if key in raw:
                defaults[key] = bool(raw[key])
        return defaults

    def save(self, values: dict[str, bool]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({key: bool(values.get(key, True)) for key in ("douyin", "bilibili")}, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
