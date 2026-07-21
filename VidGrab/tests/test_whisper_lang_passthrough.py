"""验证 faster-whisper 的 info.language 权威语种被透传，不被文本启发式覆盖（产品评审 P0）。

不依赖真实 faster-whisper / openai；用 Mock 模型返回带 .language 的 info 对象，
断言：
- _transcribe_chunked 把首块检测到的 info.language 透传为元组第二元素；
- 纯汉字日语（"ja"）/韩语（"ko"）不会被误判成中文；
- _read_worker_lang 能正确读取 worker 写的 <output-json>.lang side-file。
"""
from __future__ import annotations

import os
import sys
import wave
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.transcriber import Segment, _transcribe_chunked, _read_worker_lang


class _FakeInfo:
    def __init__(self, language: str):
        self.language = language


class _FakeModelLang:
    """Mock faster-whisper 模型：transcribe 返回 (seg_gen, info)，info.language 为指定语种。"""

    def __init__(self, language: str):
        self.language = language
        self.calls = 0

    def transcribe(self, audio_chunk, **kwargs):
        self.calls += 1
        seg = Segment(start=0.0, end=1.0, text="x")
        return (iter([seg]), _FakeInfo(self.language))


def _make_wav(path: Path, seconds: int = 20, sr: int = 16000) -> None:
    nframes = seconds * sr
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * nframes)


def test_chunked_passthrough_japanese():
    """纯汉字日语视频：faster-whisper 检测为 ja，应透传 ja 而非误判 zh。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wav = d / "a.wav"
        _make_wav(wav, seconds=20)  # 2 块（chunk_sec=10）
        m = _FakeModelLang("ja")
        segs, lang = _transcribe_chunked(m, wav, chunk_sec=10, resume_sec=0.0)
        assert lang == "ja", f"应透传 ja，实际 {lang!r}"
        assert len(segs) == 2, segs
        # 后续块应沿用锁定语种，不再重检测（transcribe 调用次数=块数）
        assert m.calls == 2, m.calls


def test_chunked_passthrough_korean():
    """纯汉字韩语视频：faster-whisper 检测为 ko，应透传 ko 而非误判 en。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wav = d / "a.wav"
        _make_wav(wav, seconds=20)
        m = _FakeModelLang("ko")
        segs, lang = _transcribe_chunked(m, wav, chunk_sec=10, resume_sec=0.0)
        assert lang == "ko", f"应透传 ko，实际 {lang!r}"


def test_chunked_passthrough_english():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wav = d / "a.wav"
        _make_wav(wav, seconds=20)
        m = _FakeModelLang("en")
        segs, lang = _transcribe_chunked(m, wav, chunk_sec=10, resume_sec=0.0)
        assert lang == "en", f"应透传 en，实际 {lang!r}"


def test_read_worker_lang_sidefile():
    """_read_worker_lang 读取 worker 写的 <output-json>.lang。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        json_path = d / "x.segments.json"
        json_path.write_text("[]", encoding="utf-8")
        (d / "x.segments.json.lang").write_text("ja", encoding="utf-8")
        assert _read_worker_lang(json_path) == "ja"

        # 缺少 side-file 时返回 None（调用方退回文本启发式兜底）
        orphan = d / "y.segments.json"
        orphan.write_text("[]", encoding="utf-8")
        assert _read_worker_lang(orphan) is None


if __name__ == "__main__":
    test_chunked_passthrough_japanese()
    test_chunked_passthrough_korean()
    test_chunked_passthrough_english()
    test_read_worker_lang_sidefile()
    print("ALL WHISPER LANG PASSTHROUGH TESTS PASSED")
