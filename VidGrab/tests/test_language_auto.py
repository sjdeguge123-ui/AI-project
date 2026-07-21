# 验证 whisper.language="auto" 不会以非法字符串 "auto" 透传给 faster-whisper。
#
# 复现用户报错：ValueError: 'auto' is not a valid language code
# 根因：config 默认 whisper.language="auto"，原透传链把 "auto" 直接喂给
#       faster-whisper 的 model.transcribe(language="auto")，而它只接受 None 或具体语种码。
#
# 修复边界：
#   core/transcriber.py 启动子进程时把 "auto" 归一为空串（--language ""）；
#   core/transcribe_worker.py 把 "auto"/""/None 归一为 None（自动检测）。
#
# 本测试不依赖 faster-whisper / openai，直接 mock 子进程内真正调用 faster-whisper 的函数，
# 断言透传给它的 language 参数符合预期。

from __future__ import annotations

import os
import sys
import tempfile

# 让仓库根在 sys.path 中，便于 import core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock  # noqa: E402


def _run_worker_with_language(lang_value: str):
    """模拟子进程 CLI 以 --language <lang_value> 启动，捕获透传给 faster-whisper 的 language。"""
    import core.transcribe_worker as worker
    import core.transcriber  # 确保模块已加载，便于 mock

    captured = {}

    def fake_run(audio_path, model_size, device="auto", compute_type="auto",
                 chunk_sec=120, resume_sec=0.0, language=None):
        captured["language"] = language
        return []

    out_json = tempfile.mktemp(suffix=".json")
    old_argv = sys.argv
    sys.argv = [
        "core.transcribe_worker",
        "--audio-path", "dummy.wav",
        "--model-size", "base",
        "--language", lang_value,
        "--output-json", out_json,
    ]
    try:
        with mock.patch.object(worker.os, "_exit") as m_exit, \
             mock.patch.object(core.transcriber, "_run_local_transcription", side_effect=fake_run):
            try:
                worker.main()
            except SystemExit:
                pass
        return captured.get("language"), (m_exit.call_args[0][0] if m_exit.called else None)
    finally:
        sys.argv = old_argv
        try:
            os.unlink(out_json)
        except Exception:
            pass


def test_auto_normalized_to_none():
    language, exit_code = _run_worker_with_language("auto")
    assert exit_code == 0, f"子进程应成功退出（exit 0），实际 {exit_code}"
    assert language is None, f"language='auto' 应归一为 None，实际 {language!r}"


def test_explicit_zh_preserved():
    language, exit_code = _run_worker_with_language("zh")
    assert exit_code == 0, f"子进程应成功退出（exit 0），实际 {exit_code}"
    assert language == "zh", f"显式 language='zh' 应保留，实际 {language!r}"


def test_explicit_en_preserved():
    language, exit_code = _run_worker_with_language("en")
    assert exit_code == 0, f"子进程应成功退出（exit 0），实际 {exit_code}"
    assert language == "en", f"显式 language='en' 应保留，实际 {language!r}"


if __name__ == "__main__":
    fails = []
    for name in ("test_auto_normalized_to_none", "test_explicit_zh_preserved", "test_explicit_en_preserved"):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001
            fails.append((name, repr(e)))
            print(f"FAIL {name}: {e}")
    if fails:
        print(f"\n{len(fails)} test(s) failed")
        sys.exit(1)
    print("\nAll passed")
