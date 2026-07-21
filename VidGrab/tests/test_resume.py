"""断点续传单元测试：验证 GPU→CPU 回退时不会从头重跑。

用真实临时 WAV（16k mono）+ Mock 模型，验证 core/transcriber.py 的分块续传逻辑：
- resume_sec=0 时转录全部块；
- 给定已完成进度（completed_until_sec=某块边界）后，只转录 offset>=resume_sec 的块，
  已完成块不再调用 model.transcribe（即 CPU 不从头开始）。
"""
import os
import sys
import json
import wave
import tempfile
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.transcriber import Segment, _transcribe_chunked, _write_progress_atomic


def _make_wav(path: Path, seconds: int, sr: int = 16000):
    nframes = seconds * sr
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        # 静音 PCM（int16 零），仅用于驱动分块逻辑
        w.writeframes(b"\x00\x00" * nframes)


class _FakeModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_chunk, **kwargs):
        self.calls += 1
        seg = Segment(start=0.0, end=1.0, text="x")
        return (iter([seg]), {"text": ""})


def test_resume_skips_completed_chunks():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wav = d / "a.wav"
        _make_wav(wav, seconds=30)  # 30s，chunk_sec=10 -> 3 块
        prog = wav.with_suffix(".progress.json")

        # 第 1 次：从头转录，应转录全部 3 块
        m1 = _FakeModel()
        segs1 = _transcribe_chunked(m1, wav, chunk_sec=10, resume_sec=0.0, progress_path=prog)
        assert len(segs1) == 3, segs1
        assert m1.calls == 3, m1.calls

        # 模拟「GPU 崩在中途」：进度只写到第 2 块结束（20s），保留已完成 segments
        _write_progress_atomic(prog, 20.0, segs1[:2])

        # 第 2 次：CPU 回退续跑（worker 会读进度文件里的 completed_until_sec + segments 传入）
        with open(prog, "r", encoding="utf-8") as f:
            prog_data = json.load(f)
        resume = float(prog_data["completed_until_sec"])
        existing = prog_data["segments"]
        m2 = _FakeModel()
        segs2 = _transcribe_chunked(m2, wav, chunk_sec=10, resume_sec=resume, existing_segments=existing)
        # 已完成 2 段 + 新 1 段 = 3 段；model.transcribe 仅被调用 1 次（不从头）
        assert len(segs2) == 3, segs2
        assert m2.calls == 1, m2.calls
        print("✅ 断点续传：GPU→CPU 仅续跑未完成块（transcribe 调用 1/3），不从头")


if __name__ == "__main__":
    test_resume_skips_completed_chunks()
    print("ALL RESUME TESTS PASSED")
