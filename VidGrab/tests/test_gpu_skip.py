"""GPU 稳定性修复（2026-07-21 二次反馈）单测：

覆盖两个加固点：
1. core.transcribe_worker._preload_ctranslate2_cudnn —— 增强 cudnn 预加载（整组 CUDA/cudnn DLL + DLL 搜索路径）。
2. core.transcriber._gpu_confirmed_unstable / _mark_gpu_cuda_crashed —— 会话级 GPU 失败跳过。

注意：core.transcribe_worker 在 win32 下会在模块导入时重写 sys.stdout/stderr，
本受限沙箱（win32）导入即破坏进程内 stdout 状态。因此对预加载相关测试，
统一在「先切到 linux 平台再导入模块」的前提下进行，避免触发导入期 win32 分支。
"""

import importlib
import sys

import pytest


def _import_worker_under_linux(monkeypatch):
    """在非 win32 平台下导入 transcribe_worker，规避导入期 win32 stdout 重写。"""
    monkeypatch.setattr(sys, "platform", "linux")
    import core.transcribe_worker as w

    return w


# ───────────────────────── 1. cudnn 预加载 ─────────────────────────

def test_preload_cudnn_non_win32_noop(monkeypatch):
    w = _import_worker_under_linux(monkeypatch)
    # 非 Windows 下应直接返回、无任何副作用、不抛
    w._preload_ctranslate2_cudnn()


def test_preload_cudnn_win32_missing_dll_no_raise(monkeypatch):
    w = _import_worker_under_linux(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    # 伪造 find_spec 返回有 origin 的包，但目录下无任何 cudnn/cublas DLL
    class _FakeSpec:
        origin = "/fake/ctranslate2/__init__.py"

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: _FakeSpec() if name == "ctranslate2" else None
    )
    monkeypatch.setattr("glob.glob", lambda pat: [])
    monkeypatch.setattr("os.add_dll_directory", lambda p: None)
    # 不应抛
    w._preload_ctranslate2_cudnn()


def test_preload_cudnn_win32_loads_existing(monkeypatch):
    w = _import_worker_under_linux(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    loaded = []
    # 伪造 find_spec 返回有 origin 的包，且目录下存在一条 cudnn DLL
    class _FakeSpec:
        origin = "/fake/ctranslate2/__init__.py"

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: _FakeSpec() if name == "ctranslate2" else None
    )
    monkeypatch.setattr("glob.glob", lambda pat: ["/fake/ctranslate2/cudnn64_9.dll"])
    fake_kernel32 = type("K", (), {"LoadLibraryW": staticmethod(lambda p: loaded.append(p) or 1)})()
    fake_windll = type("W", (), {"kernel32": fake_kernel32})()
    monkeypatch.setattr("ctypes.windll", fake_windll)
    monkeypatch.setattr("os.add_dll_directory", lambda p: None)
    w._preload_ctranslate2_cudnn()
    # 每类 CUDA/cudnn 运行时 DLL 模式都应触发一次 LoadLibraryW（含 cudnn64_9.dll）
    assert "/fake/ctranslate2/cudnn64_9.dll" in loaded
    assert len(loaded) >= 1


# ───────────────────────── 2. 会话级 GPU 失败跳过 ─────────────────────────

def test_session_gpu_skip_set_on_fastfail():
    import core.transcriber as t

    t._GPU_CUDA_CRASHED = False
    try:
        assert t._gpu_confirmed_unstable() is False
        t._mark_gpu_cuda_crashed()
        assert t._gpu_confirmed_unstable() is True
    finally:
        t._GPU_CUDA_CRASHED = False


def test_session_gpu_skip_not_triggered_by_oom():
    import core.transcriber as t

    t._GPU_CUDA_CRASHED = False
    try:
        reason = t._classify_worker_exit(
            None, "Error loading cublas64_12.dll WinError 1455 页面文件太小"
        )
        assert "页面文件" in reason
        assert t._gpu_confirmed_unstable() is False
    finally:
        t._GPU_CUDA_CRASHED = False


def test_gpu_crash_classification_3221226505():
    import core.transcriber as t

    reason = t._classify_worker_exit(3221226505, "")
    assert "cudnn" in reason or "fast-fail" in reason or "STATUS_STACK_BUFFER_OVERRUN" in reason


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
