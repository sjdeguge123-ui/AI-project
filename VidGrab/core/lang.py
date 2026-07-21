"""语言检测公共函数（core/lang.py）

供字幕提取、转录、摘要等模块统一使用，避免多处重复实现导致不一致。
"""

from __future__ import annotations

from typing import Iterable


def _detect_language(segments) -> str:
    """按文本中的中文字符占比判断真实语种。

    规则：
    - 文本中 CJK 字符占比 > 8% 视为 'zh'。
    - 否则视为 'en'（Phase 0 仅处理中文/英文，其他语种统一归到 'en'，
      由 LLM 根据实际文本自由发挥，避免误判）。
    - 空文本返回 ''，交给上层再判定。

    为什么阈值是 8%：
    - 纯英文视频里若混入少量中英混合字幕、中文标题水印、或少量中文注释，
      通常不会超过 8%；而真正的中文字幕/转录占比远高于此。
    """
    text = " ".join(getattr(s, "text", "") or "" for s in (segments or []))
    if not text.strip():
        return ""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return "zh" if cjk / max(1, len(text)) > 0.08 else "en"
