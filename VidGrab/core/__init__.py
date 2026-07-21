# VidGrab Core Module
# Phase 0 — 数据结构定义
"""VidGrab 核心数据结构。

所有跨模块流转的数据对象都在这里定义，便于后续 Phase 1（导出 / 多模型）复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Platform(str, Enum):
    """支持的视频平台。"""

    YOUTUBE = "youtube"
    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    TWITTER = "twitter"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            Platform.YOUTUBE: "YouTube",
            Platform.BILIBILI: "B站",
            Platform.DOUYIN: "抖音",
            Platform.KUAISHOU: "快手",
            Platform.TWITTER: "Twitter/X",
            Platform.UNKNOWN: "未知",
        }.get(self, self.value)


@dataclass
class Segment:
    """单条字幕 / 转录片段。"""

    start: float  # 起始时间（秒）
    end: float    # 结束时间（秒）
    text: str     # 文本


@dataclass
class Transcript:
    """一次提取的结果：视频元信息 + 时间轴文本。"""

    platform: Platform
    video_id: str
    title: str
    author: str = ""
    publish_time: str = ""      # 展示用，如 "2026-07-19" 或 "2026年07月19日"
    language: str = ""          # 如 "zh" / "en"
    duration: float = 0.0       # 秒
    segments: List[Segment] = field(default_factory=list)
    source: str = "subtitle"    # subtitle（字幕） | audio（待转录音频）
    audio_path: str = ""        # 无字幕时下载的音频路径，交给 transcriber
    page_index: Optional[int] = None  # 合集选集（0-based）；单P 为 0 或 None
    is_collection: bool = False       # 是否为合集/多P 视频（决定文件名是否带 -Pxx）


@dataclass
class DetailedRow:
    """详细内容表格的一行：时间点 + 该段核心要点 + 内容 + 用户备注。"""

    timestamp: str  # MM:SS
    point: str = ""   # 该时间段的核心要点（短句）
    content: str = "" # 该时间段讲什么
    remark: str = ""  # 用户自己写的备注（AI 不填，留空给用户事后笔记）


@dataclass
class GoldenQuote:
    """金句：值得摘抄/记住的原话或凝练观点，附带它在视频里出现的时间戳。"""

    timestamp: str = ""  # MM:SS，该金句在视频中出现的时间点
    text: str = ""       # 金句内容


@dataclass
class Summary:
    """结构化摘要，对应最终导出的 Markdown。"""

    title: str
    source: str = ""            # 来源标签，如 "B站" / "YouTube"
    author: str = ""
    publish_time: str = ""
    duration_text: str = ""     # 展示用，如 "12分30秒"
    content_overview: str = ""  # 基本信息里的「内容概述」（AI 生成一句话）
    detailed: List[DetailedRow] = field(default_factory=list)  # 详细内容表格（含每段时间的核心要点）
    golden_quotes: List[GoldenQuote] = field(default_factory=list)  # 金句模块（带时间戳，2-5 条）
    conclusion: str = ""        # 总结段落
    mode_label: str = "精简"    # 内容模式标签，渲染在标题后：标题 - 精简/详细/自定义：xxx
    full_text: str = ""         # 全文文案模式：带时间戳的连续转录文案（非空时模板切换为全文模式）


def format_timestamp(seconds: float) -> str:
    """秒 -> MM:SS。"""

    seconds = int(round(float(seconds)))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes:02d}:{sec:02d}"


def format_duration(seconds: float) -> str:
    """秒 -> 中文时长，如「12分30秒」/「1小时02分05秒」。"""

    seconds = int(round(float(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{sec:02d}秒"
    if minutes:
        return f"{minutes}分{sec:02d}秒"
    return f"{sec}秒"
