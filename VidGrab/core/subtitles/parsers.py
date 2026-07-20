"""字幕解析实现（core/subtitles/parsers.py）

把各平台下载下来的字幕文件，解析成项目统一的 Segment 列表。
目前支持三种常见格式：
  - WebVTT      ：YouTube / B站 最常见的字幕格式（扩展名 .vtt）
  - SRT         ：通用字幕格式（扩展名 .srt）
  - B站字幕 JSON：B站官方接口返回的结构（{'body':[{from,to,content}]}）

所有解析函数都返回 List[Segment]，Segment 定义在 core/__init__.py。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import List

from .. import Segment


def _ts_to_sec(hms: str, ms: str = "000") -> float:
    """把 'HH:MM:SS' 或 'MM:SS' 形式的时间戳转成「秒（浮点）」。

    ms 是毫秒部分（三位数字），用于更精细的时间轴。
    """

    parts = hms.split(":")
    while len(parts) < 3:
        parts.insert(0, "0")
    h, m, s = parts[-3], parts[-2], parts[-1]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms or "0") / 1000.0


def _ts_to_date(ts) -> str:
    """把 Unix 时间戳转成「YYYY年MM月DD日」格式（用于视频发布时间展示）。"""

    if not ts:
        return ""
    return time.strftime("%Y年%m月%d日", time.localtime(ts))


# 字幕时间轴正则：VTT 用小数点，SRT 用逗号
_VTT_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2})\.(\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2})\.(\d{3})"
)
_SRT_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}),(\d{3})"
)


def parse_vtt(path: Path) -> List[Segment]:
    """解析 WebVTT 字幕文件 -> Segment 列表。

    VTT 格式示例：
        WEBVTT

        00:00:01.000 --> 00:00:04.000
        大家好，欢迎来到本期视频
    """

    segments: List[Segment] = []
    text_lines: List[str] = []
    start = end = 0.0

    def flush() -> None:
        nonlocal text_lines, start, end
        if text_lines:
            segments.append(Segment(start=start, end=end, text=" ".join(text_lines).strip()))
            text_lines = []

    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = _VTT_CUE_RE.match(line)
            if m:
                flush()
                start = _ts_to_sec(m.group(1), m.group(2))
                end = _ts_to_sec(m.group(3), m.group(4))
            elif line.startswith("WEBVTT") or line.startswith("NOTE") or line.strip().isdigit():
                continue  # 跳过头信息 / 注释 / 序号行
            else:
                text_lines.append(line.strip())
    flush()
    return segments


def parse_srt(path: Path) -> List[Segment]:
    """解析 SRT 字幕文件 -> Segment 列表。

    SRT 格式示例：
        1
        00:00:01,000 --> 00:00:04,000
        大家好，欢迎来到本期视频
    """

    raw = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", raw.strip())
    segments: List[Segment] = []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        # 时间轴行可能在序号行之后，先找到它
        ts_idx = None
        for i, line in enumerate(lines):
            if _SRT_CUE_RE.match(line):
                ts_idx = i
                break
        if ts_idx is None:
            continue
        m = _SRT_CUE_RE.match(lines[ts_idx])
        start = _ts_to_sec(m.group(1), m.group(2))
        end = _ts_to_sec(m.group(3), m.group(4))
        text = " ".join(lines[ts_idx + 1:]).strip()
        segments.append(Segment(start=start, end=end, text=text))
    return segments


def parse_bilibili_json(path: Path) -> List[Segment]:
    """解析 B站字幕 JSON 文件 -> Segment 列表。

    B站字幕接口返回结构：
        {'body': [{'from': 起始秒, 'to': 结束秒, 'content': '文本'}, ...]}
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    body = data.get("body") if isinstance(data, dict) else data
    segments: List[Segment] = []
    for item in body or []:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        segments.append(
            Segment(start=float(item.get("from", 0)), end=float(item.get("to", 0)), text=content)
        )
    return segments
