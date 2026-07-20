"""字幕解析子包（core/subtitles）

负责把「各平台下载下来的字幕文件」解析成项目统一的 Segment 列表。
具体解析实现见 parsers.py。
"""

from .parsers import (
    parse_vtt,
    parse_srt,
    parse_bilibili_json,
    _ts_to_sec,
    _ts_to_date,
)

__all__ = [
    "parse_vtt",
    "parse_srt",
    "parse_bilibili_json",
    "_ts_to_sec",
    "_ts_to_date",
]
