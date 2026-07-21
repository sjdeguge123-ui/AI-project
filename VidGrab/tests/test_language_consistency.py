"""验证转录后 transcript.language 被正确设置，且摘要入口能防御空 language。

无需真实 faster-whisper/openai，mock transcribe 底层即可。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock  # noqa: E402

from core import Platform, Segment, Transcript  # noqa: E402
from core.config import WhisperConfig  # noqa: E402


def _make_transcript(segments, language=""):
    return Transcript(
        platform=Platform.BILIBILI,
        video_id="BVtest",
        title="Test Video",
        author="tester",
        language=language,
        duration=120.0,
        segments=segments,
        source="audio",
        audio_path="/tmp/dummy.wav",
    )


def test_transcribe_sets_language_for_english():
    """音频转录后，英文内容应标记 language='en'。"""
    import core.transcriber as tr

    fake_segments = [
        Segment(start=0.0, end=2.0, text="Hello, this is an English video."),
        Segment(start=2.0, end=4.0, text="We talk about technology and life."),
    ]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF\x00\x00\x00\x00WAVEfmt \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00data\x00\x00\x00\x00")
        path = f.name
    try:
        with mock.patch.object(tr, "_transcribe_api", return_value=fake_segments):
            inp = _make_transcript([])
            inp.audio_path = path
            # WhisperConfig 默认 mode='api'，但这里 mock 掉 api 调用
            out = tr.transcribe(inp, WhisperConfig(mode="api", api_key="sk-test"))
            assert out.language == "en", f"英文转录应标记 en，实际 {out.language!r}"
            assert out.source == "transcript"
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def test_transcribe_sets_language_for_chinese():
    """音频转录后，中文内容应标记 language='zh'。"""
    import core.transcriber as tr

    fake_segments = [
        Segment(start=0.0, end=2.0, text="大家好，今天我们来讨论人工智能。"),
        Segment(start=2.0, end=4.0, text="这是一个非常重要的技术话题。"),
    ]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF\x00\x00\x00\x00WAVEfmt \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00data\x00\x00\x00\x00")
        path = f.name
    try:
        with mock.patch.object(tr, "_transcribe_api", return_value=fake_segments):
            inp = _make_transcript([])
            inp.audio_path = path
            out = tr.transcribe(inp, WhisperConfig(mode="api", api_key="sk-test"))
            assert out.language == "zh", f"中文转录应标记 zh，实际 {out.language!r}"
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def test_generate_summary_defends_empty_language():
    """transcript.language 为空时，generate_summary 应自动检测为 en 并传入英文指令。"""
    from core import summarizer

    en_segments = [
        Segment(start=0.0, end=2.0, text="This is an English video about technology."),
        Segment(start=2.0, end=4.0, text="We explore the future of AI."),
    ]
    transcript = _make_transcript(en_segments, language="")

    captured = {}

    def fake_call_llm(client, ai, system_prompt, user_msg, max_tokens, proxy="", rate_limiter=None, retries=3):
        captured["system_prompt"] = system_prompt
        return {
            "content_overview": "An English overview.",
            "detailed": [{"timestamp": "00:00", "point": "Intro", "content": "English content."}],
            "golden_quotes": [{"timestamp": "00:00", "text": "English quote."}],
        }

    with mock.patch.object(summarizer, "_client_for") as m_client, \
         mock.patch.object(summarizer, "_call_llm", side_effect=fake_call_llm), \
         mock.patch.object(summarizer.RateLimiter, "wait_before_call"):
        from core.config import AIConfig
        summarizer.generate_summary(transcript, AIConfig(provider="siliconflow", api_key="sk-test"))

    sys_p = captured.get("system_prompt", "")
    assert "English" in sys_p, f"空 language 时应自动检测为英文并传入 English 指令，实际 prompt: {sys_p[:200]}"
    assert "简体中文" not in sys_p, f"英文视频不应出现简体中文指令，实际 prompt: {sys_p[:200]}"


if __name__ == "__main__":
    test_transcribe_sets_language_for_english()
    test_transcribe_sets_language_for_chinese()
    test_generate_summary_defends_empty_language()
    print("ALL LANGUAGE CONSISTENCY TESTS PASSED")
