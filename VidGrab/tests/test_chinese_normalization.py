"""中文规范化测试：繁体字幕/转录 → 简体中文 + 标点恢复。"""

from __future__ import annotations

from core import Segment
from core.lang import _normalize_chinese
from core.summarizer import _build_chunk_text, _build_full_text


def test_normalize_chinese_traditional_to_simplified():
    """繁体中文应被转换为简体中文。"""
    assert _normalize_chinese("組織們歡迎各位來到級大學") == "组织们欢迎各位来到级大学"
    assert _normalize_chinese("這是一個繁體字幕") == "这是一个繁体字幕"


def test_normalize_chinese_english_unchanged():
    """英文文本不应被改动。"""
    assert _normalize_chinese("Hello, world!") == "Hello, world!"


def test_build_full_text_converts_traditional():
    """全文文案模式应把繁体 segment 转为简体输出。"""
    segments = [
        Segment(start=0.0, end=2.0, text="組織們歡迎各位"),
        Segment(start=2.0, end=4.0, text="來到級大學"),
    ]
    text = _build_full_text(segments)
    assert "组织们欢迎各位" in text
    assert "来到级大学" in text
    assert "組織" not in text
    assert "級大學" not in text


def test_build_chunk_text_converts_traditional():
    """摘要分块文本应把繁体 segment 转为简体输出。"""
    segments = [
        Segment(start=0.0, end=2.0, text="這是一個繁體字幕"),
    ]
    text = _build_chunk_text(segments)
    assert "这是一个繁体字幕" in text
    assert "這是一個繁體字幕" not in text
