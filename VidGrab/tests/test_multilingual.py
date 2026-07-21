"""多语种检测与指令测试（ja/ko/zh/en）。"""

from __future__ import annotations

from core import Segment
from core.lang import _detect_language
from core.summarizer import _language_instruction


def _seg(text: str) -> Segment:
    return Segment(start=0.0, end=1.0, text=text)


def test_detect_japanese():
    """含假名的文本应判定为日语。"""
    assert _detect_language([_seg("こんにちは、今日はいい天気ですね")]) == "ja"
    assert _detect_language([_seg("カタカナも検出できます")]) == "ja"
    # 日文汉字+假名
    assert _detect_language([_seg("図書館で本を読みます")]) == "ja"


def test_detect_korean():
    """含 Hangul 的文本应判定为韩语。"""
    assert _detect_language([_seg("안녕하세요 오늘 날씨가 좋네요")]) == "ko"


def test_detect_chinese():
    """纯汉字（无假名/Hangul）且 CJK 占比高 → zh。"""
    assert _detect_language([_seg("今天我们来聊聊人工智能")]) == "zh"


def test_detect_english():
    """无 CJK/假名/Hangul → en。"""
    assert _detect_language([_seg("This is an English video about AI.")]) == "en"


def test_language_instruction_ja():
    """日语应返回日文输出指令。"""
    inst = _language_instruction("ja")
    assert "日本語" in inst
    assert "中国語" in inst  # 禁止翻译成中文


def test_language_instruction_ko():
    """韩语应返回韩文输出指令。"""
    inst = _language_instruction("ko")
    assert "한국어" in inst
    assert "중국어" in inst  # 禁止翻译成中文
