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


def render_summary_md(summary: Summary) -> str:
    """把 Summary 渲染成最终 Markdown 字符串。"""

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
