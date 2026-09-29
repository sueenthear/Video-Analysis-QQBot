"""从分享文案中提取可用的视频链接。

当前支持：
- Bilibili 视频链接：``bilibili.com/video/...``
- 抖音分享链接：``v.douyin.com/...`` 和 ``douyin.com/video/...``

示例：
    >>> extract_links("看看这个视频 https://v.douyin.com/Y6uVig3nJPA/ F@H.VL")
    ['https://v.douyin.com/Y6uVig3nJPA/']
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 只匹配支持的平台，避免把文案中的普通网址误当成视频链接。
_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:bilibili\.com/video/[^\s]+|"
    r"v\.douyin\.com/[^\s]+|douyin\.com/video/[^\s]+)",
    re.IGNORECASE,
)

# 链接后常见的中文/英文标点不是 URL 的一部分。
_TRAILING_PUNCTUATION = "，。；：！？、）》）]}>\"'.,;:!?)]}"


def _clean_url(url: str) -> str:
    """清理候选链接末尾粘连的标点。"""
    return url.rstrip(_TRAILING_PUNCTUATION)


def _platform(url: str) -> str:
    """根据链接域名返回平台名称。"""
    return "bilibili" if "bilibili.com" in url.lower() else "douyin"


def extract_links(text: str) -> list[str]:
    """从文案中提取去重后的 Bilibili/抖音视频链接。

    链接按其在原文中的出现顺序返回。抖音链接后面常粘有分享口令，
    由于提取以空白字符为边界，因此不会把口令带入结果。
    """
    if not isinstance(text, str):
        raise TypeError("text 必须是 str")

    links: list[str] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.finditer(text):
        url = _clean_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


def extract_links_by_platform(text: str) -> dict[str, list[str]]:
    """按平台分组提取链接。"""
    result = {"bilibili": [], "douyin": []}
    for url in extract_links(text):
        result[_platform(url)].append(url)
    return result


def extract_from_texts(texts: Iterable[str]) -> list[str]:
    """从多段文案中提取并去重链接。"""
    return extract_links("\n".join(texts))


if __name__ == "__main__":
    import sys

    # 支持通过管道传入文案：Get-Content note.txt | uv run python link_parser.py
    content = sys.stdin.read()
    for link in extract_links(content):
        print(link)
