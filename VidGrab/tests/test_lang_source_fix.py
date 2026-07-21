"""语种信号修复（2026-07-21）单测：

覆盖三个修复点：
1. core.lang._map_bili_lang 的「元数据 × 文本」交叉校验（B站常错标 lan）；
2. core.platforms.bilibili._choose_subtitle 的字幕选择策略（原语种优先非机翻、中文翻译优先 zh）；
3. core.summarizer._restore_punctuation 的失败兜底（不再完全无标点）+ _rule_based_punctuate。
"""

import sys
from pathlib import Path

# 让 tests/ 也能 import core / skill
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.lang import _map_bili_lang, _detect_language  # noqa: E402
from core.platforms.bilibili import _choose_subtitle, _is_machine_translation  # noqa: E402
from core.summarizer import _restore_punctuation, _rule_based_punctuate  # noqa: E402
from core.config import AIConfig  # noqa: E402


# ───────────────────────── 1. _map_bili_lang 交叉校验 ─────────────────────────

def test_map_bili_lang_ko_but_chinese_text():
    # B站把中文机翻字幕错标成 ko → 应以文本为准回落 zh（问题4根因）
    text = "田中广播大家好我是田中最近日本非常热你们住的地方怎么样"
    assert _map_bili_lang("ko", text) == "zh"


def test_map_bili_lang_ko_but_japanese_text():
    # 标 ko 但文本含假名（实为日文）→ 应为 ja，而非盲信 ko
    text = "こんにちは世界今日は良い天気ですね皆さん元気ですか"
    assert _map_bili_lang("ko", text) == "ja"


def test_map_bili_lang_ja_but_chinese_text():
    text = "这是一段纯中文内容用于测试语种交叉校验逻辑"
    assert _map_bili_lang("ja", text) == "zh"


def test_map_bili_lang_en_but_chinese_text():
    text = "中文内容测试一下标点恢复与语种判断逻辑是否一致"
    assert _map_bili_lang("en", text) == "zh"


def test_map_bili_lang_real_ja():
    # 真实日文（有假名）且 lan=ja → 保持 ja
    assert _map_bili_lang("ja", "こんにちは世界") == "ja"


def test_map_bili_lang_real_en():
    assert _map_bili_lang("en", "Hello world this is a test") == "en"


def test_map_bili_lang_ai_zh_returns_empty():
    # zh 类（含 ai-zh）回落文本判定，空文本时返回 ""（交由上层）
    assert _map_bili_lang("ai-zh", "") == ""


def test_map_bili_lang_empty():
    assert _map_bili_lang("", "中文") == ""


# ───────────────────────── 2. _choose_subtitle 选择策略 ─────────────────────────

def test_choose_subtitle_original_prefers_non_zh():
    subs = [
        {"lan": "ai-zh", "subtitle_url": "u1"},
        {"lan": "ja", "subtitle_url": "u2"},
    ]
    chosen, degraded = _choose_subtitle(subs, prefer_chinese=False)
    assert chosen["lan"] == "ja"
    assert degraded is False


def test_choose_subtitle_original_degrades_to_zh():
    # 只有中文（原声或机翻）→ 降级并标记
    subs = [
        {"lan": "ai-zh", "subtitle_url": "u1"},
        {"lan": "zh-CN", "subtitle_url": "u2"},
    ]
    chosen, degraded = _choose_subtitle(subs, prefer_chinese=False)
    assert "zh" in chosen["lan"]
    assert degraded is True


def test_choose_subtitle_chinese_prefers_zh():
    subs = [
        {"lan": "ja", "subtitle_url": "u1"},
        {"lan": "ai-zh", "subtitle_url": "u2"},
    ]
    chosen, degraded = _choose_subtitle(subs, prefer_chinese=True)
    assert "zh" in chosen["lan"]
    assert degraded is False


def test_is_machine_translation():
    assert _is_machine_translation("ai-zh") is True
    assert _is_machine_translation("ai-ja") is True
    assert _is_machine_translation("ja") is False
    assert _is_machine_translation("zh-CN") is False


# ───────────────────────── 3. 标点鲁棒性兜底 ─────────────────────────

def test_rule_based_punctuate_adds_period():
    text = "[00:00] 大家好我是田中最近日本非常热你们住的地方怎么样"
    out = _rule_based_punctuate(text)
    assert out.endswith("。")
    assert "[00:00]" in out
    assert "田中" in out  # 不丢内容


def test_rule_based_punctuate_keeps_existing_punct():
    text = "[00:00] 你好。世界"
    out = _rule_based_punctuate(text)
    assert "你好。" in out


class _FailingClient:
    """模拟 LLM 调用必失败（限流/网络），用于验证标点兜底。"""

    @property
    def chat(self):
        raise RuntimeError("simulated 429/network failure")


def test_restore_punctuation_fallback_on_error():
    cfg = AIConfig()
    text = "[00:00] 大家好我是田中最近日本非常热"
    out = _restore_punctuation(text, _FailingClient(), cfg, "")
    # 兜底：应含句号，不应等于无标点的原文本；且保留时间戳
    assert "。" in out
    assert out.startswith("[00:00]")
    assert out != text  # 确实做了兜底处理


def test_restore_punctuation_no_punct_needed_passthrough():
    # 已有标点的文本不应被改动
    cfg = AIConfig()
    text = "[00:00] 你好。世界！"
    out = _restore_punctuation(text, _FailingClient(), cfg, "")
    assert out == text


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
