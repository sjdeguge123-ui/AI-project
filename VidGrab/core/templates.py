# 摘要模板（单一事实源）
# Phase 0 — 固化 Markdown 渲染模板，改模板只改这里
"""摘要的 Markdown 渲染模板与渲染函数。

模板结构（与产品定义一致）：
  ## 视频摘要：标题
  ### 基本信息
  ### 内容脉络（时间 | 核心要点 | 内容 | 备注，合并表）
  ### 金句
"""

from __future__ import annotations

import re

from . import DetailedRow, Summary

SUMMARY_MD_TEMPLATE = """\
## 视频摘要：{title} - {mode_label}

### 基本信息
- 来源：{source}
- 作者：{author}
- 发布时间：{publish_time}
- 视频时长：{duration_text}
- 内容概述：{content_overview}

### 内容脉络

| 时间 | 核心要点 | 内容 | 备注 |
|------|----------|------|------|
{rows}

### 金句
{golden_quotes}
"""

# 全文文案模式：基本信息（含内容概述）+ 连续文案（时间戳已在生成时注入）
FULLTEXT_MD_TEMPLATE = """\
## 视频摘要：{title} - {mode_label}

### 基本信息
- 来源：{source}
- 作者：{author}
- 发布时间：{publish_time}
- 视频时长：{duration_text}
- 内容概述：{content_overview}

### 全文文案

{full_text}
"""

# 时间戳正则：匹配 [MM:SS] 或 [HH:MM:SS]
_TS_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def _render_full_text_html(text: str) -> str:
    """把全文文案里的时间戳 [[MM:SS]] 标红加粗，与关键词红色风格一致。"""

    return _TS_RE.sub(
        r'<span style="color:#e74c3c;font-weight:bold;">[\1]</span>', text
    )


def render_summary_md(summary: Summary) -> str:
    """把 Summary 渲染成最终 Markdown 字符串。

    full_text 非空时走「全文文案」模式（无内容脉络表格，直接输出带时间戳的连续文案）。
    """

    if summary.full_text:
        return FULLTEXT_MD_TEMPLATE.format(
            title=summary.title,
            mode_label=summary.mode_label or "全文文案",
            source=summary.source or "未知",
            author=summary.author or "未知",
            publish_time=summary.publish_time or "未知",
            duration_text=summary.duration_text or "未知",
            content_overview=summary.content_overview or "（暂无）",
            full_text=summary.full_text,
        )

    if summary.detailed:
        rows = "\n".join(
            f"| {r.timestamp} | {r.point} | {r.content} | {r.remark or ''} |"
            for r in summary.detailed
        )
    else:
        rows = "| --:-- | （暂无） | （暂无） |  |"

    if summary.golden_quotes:
        golden_quotes = "\n".join(
            f"{i}. [{q.timestamp}] {q.text}" for i, q in enumerate(summary.golden_quotes, 1)
        )
    else:
        golden_quotes = "（暂无）"

    return SUMMARY_MD_TEMPLATE.format(
        title=summary.title,
        mode_label=summary.mode_label or "精简",
        source=summary.source or "未知",
        author=summary.author or "未知",
        publish_time=summary.publish_time or "未知",
        duration_text=summary.duration_text or "未知",
        content_overview=summary.content_overview or "（暂无）",
        rows=rows,
        golden_quotes=golden_quotes,
    )

