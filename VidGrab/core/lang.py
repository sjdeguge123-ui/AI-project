"""语言检测公共函数（core/lang.py）

供字幕提取、转录、摘要等模块统一使用，避免多处重复实现导致不一致。
"""

from __future__ import annotations

from typing import Iterable


def _detect_language(segments) -> str:
    """按文本中的字符脚本判断真实语种。

    规则（按优先级）：
    - 含日文假名（平假名/片假名）→ 'ja'。
    - 含韩文 Hangul → 'ko'。
    - 文本中 CJK 字符占比 > 8% → 'zh'。
    - 否则 → 'en'。
    - 空文本返回 ''，交给上层再判定。

    为什么阈值是 8%：
    - 纯英文视频里若混入少量中英混合字幕、中文标题水印、或少量中文注释，
      通常不会超过 8%；而真正的中文字幕/转录占比远高于此。
    """
    text = " ".join(getattr(s, "text", "") or "" for s in (segments or []))
    if not text.strip():
        return ""
    # 日文假名
    if any("\u3040" <= ch <= "\u309f" or "\u30a0" <= ch <= "\u30ff" for ch in text):
        return "ja"
    # 韩文 Hangul
    if any("\uac00" <= ch <= "\ud7af" for ch in text):
        return "ko"
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return "zh" if cjk / max(1, len(text)) > 0.08 else "en"


def _normalize_chinese(text: str) -> str:
    """把中文文本统一规范为简体中文。

    使用 OpenCC 进行繁简转换（优先纯 Python 实现的 opencc-python-reimplemented，
    无需系统库）。若文本含日文假名或韩文 Hangul，则判定非中文并直接回退原文，
    避免把日文/韩文汉字误当繁体中文简化。

    若未安装 opencc 或转换失败，直接回退原文，绝不阻塞主流程。
    """
    if not text:
        return text
    # 安全守卫：含日文假名或韩文 Hangul 的文本不归为中文，不转换
    if any(
        "\u3040" <= ch <= "\u309f"  # 平假名
        or "\u30a0" <= ch <= "\u30ff"  # 片假名
        or "\uac00" <= ch <= "\ud7af"  # Hangul
        for ch in text
    ):
        return text
    try:
        import opencc

        converter = getattr(_normalize_chinese, "_converter", None)
        if converter is None:
            converter = opencc.OpenCC("t2s")  # 繁体转简体
            _normalize_chinese._converter = converter
        return converter.convert(text)
    except Exception:  # noqa: BLE001
        return text


def _map_bili_lang(lan: str) -> str:
    """把 B站字幕/视频的 lan 字段归一为内部语种码（zh/ja/ko/en）。

    B站 subtitle.lan 形如 "zh-CN" / "zh-Hans" / "ja" / "ko" / "en" / "ai-zh" 等。
    仅对「辨识度高、元数据可靠」的日文/韩文/英文直接采用；zh 退回文本判定，
    避免元数据标错把英文/日文视频误判成中文。无法识别的返回 '' 交给上层回退。
    """
    lan = (lan or "").lower().strip()
    if not lan:
        return ""
    base = lan.split("-")[0]
    if base in ("ja", "jp"):
        return "ja"
    if base == "ko":
        return "ko"
    if base == "en":
        return "en"
    # zh / cn / ai-zh 等：退回 _detect_language 文本判定，避免元数据误标
    return ""
