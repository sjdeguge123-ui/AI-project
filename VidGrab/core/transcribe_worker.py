# 本地 faster-whisper 转录子进程（隔离 GPU/CUDA 状态，避免主流程被崩溃拖垮）
"""独立子进程：本地 faster-whisper 转录。

为什么放进子进程：faster-whisper / CTranslate2 在 GPU 模式下，模型析构和 CUDA 上下文
释放时，在 Windows 上会偶发硬崩溃（进程被系统直接杀掉，Python 层抓不到异常，
表现为「转录完成却忽然回到提示符」）。把转录隔离在子进程里，子进程退出时 CUDA 状态
随之一并销毁，主进程继续 AI 摘要，完全不受 GPU 清理影响。

入口：
    python -m core.transcribe_worker \
        --audio-path X.wav --model-size base \
        --device auto --compute-type auto --output-json Y.json
成功退出 0 并把 segments 写入 output-json；失败退出非 0 并把 traceback 打到 stderr。
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from pathlib import Path


# 在 Windows PowerShell/CMD 下子进程 stdout 默认可能是 GBK，无法输出 emoji 等字符。
# 强制重定向为 UTF-8，避免转录进度里的 🚀 等符号把 worker 自己崩掉。
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


# 确保能 import core（作为脚本直接运行时也兜底）
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args():
    import argparse

    p = argparse.ArgumentParser(description="本地 faster-whisper 转录子进程（隔离 GPU 状态）")
    p.add_argument("--audio-path", required=True)
    p.add_argument("--model-size", required=True)
    p.add_argument("--device", default="auto")
    p.add_argument("--compute-type", default="auto")
    p.add_argument("--chunk-sec", type=int, default=120)
    p.add_argument("--resume", type=float, default=0.0,
                   help="断点续传起点（秒）。>0 时跳过已转录的块，从进度文件合并已有 segments。")
    p.add_argument("--language", default="auto",
                   help="语种锁定（auto=自动检测）。透传给 faster-whisper。")
    p.add_argument("--parent-pid", type=int, default=None,
                   help="skill 主进程 PID；若主进程死亡，worker 自动退出，避免残留。")
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def _start_parent_watchdog(parent_pid: int) -> None:
    """worker 子进程守护：若 skill 主进程死亡，则 worker 立即自清理退出。

    防止 skill 主进程被 agent/终端杀死后，worker 继续占用 GPU/内存跑完才退出，
    造成用户看到「项目结束却还有 python 进程残留」的观感问题。
    """
    if sys.platform != "win32" or parent_pid <= 0:
        return
    import ctypes
    import threading
    import time
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _alive(pid: int) -> bool:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE

    def _loop() -> None:
        while True:
            if not _alive(parent_pid):
                # 父进程已死，立即退出；不跑完、不写 JSON，因为主流程已经不存在
                os._exit(2)
            time.sleep(5)

    threading.Thread(target=_loop, daemon=True).start()


def _preload_ctranslate2_cudnn() -> None:
    """Windows 下优先加载 ctranslate2 自带的 cudnn64_9.dll，避免与 torch 的 cudnn 版本冲突。

    根因：ctranslate2 与 torch 都自带 cudnn64_9.dll 且版本/体积不同；
    若 torch 的 cudnn 先被加载，ctranslate2 会拿到不兼容版本，触发 STATUS_STACK_BUFFER_OVERRUN
    （0xC0000409 / 3221226505）原生 fast-fail 中止。通过显式预加载 ctranslate2 的 cudnn，
    让后续 torch/ctranslate2 使用同一份兼容 DLL。
    """

    if sys.platform != "win32":
        return
    try:
        import ctypes
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("ctranslate2")
        if not spec or not spec.origin:
            return
        cudnn_path = Path(spec.origin).parent / "cudnn64_9.dll"
        if cudnn_path.exists():
            ctypes.windll.kernel32.LoadLibraryW(str(cudnn_path))
    except Exception:  # noqa: BLE001
        # 预加载失败不阻塞主流程，让正常 import 自己处理
        pass


def main() -> int:
    args = _parse_args()
    if args.parent_pid:
        _start_parent_watchdog(args.parent_pid)
    try:
        # 在 import ctranslate2 / torch 之前，先预加载 ctranslate2 的 cudnn，
        # 避免 Windows 因多份 cudnn64_9.dll 版本冲突触发 STATUS_STACK_BUFFER_OVERRUN。
        _preload_ctranslate2_cudnn()
        # 延迟导入，避免子进程启动就加载重依赖（仅在成功路径需要）
        from core.transcriber import _run_local_transcription
        from core import Segment

        segments, detected_language = _run_local_transcription(
            Path(args.audio_path),
            args.model_size,
            device=args.device,
            compute_type=args.compute_type,
            chunk_sec=args.chunk_sec,
            resume_sec=args.resume or 0.0,
            # faster-whisper 不接受字符串 "auto" 作为 language（仅接受 None 或具体语种码如 "zh"），
            # 而 config 默认 whisper.language="auto"。这里把 "auto"/空串/None 都归一为 None（自动检测），
            # 避免 model.transcribe(language="auto") 抛 ValueError: 'auto' is not a valid language code。
            language=None if (args.language in (None, "", "auto")) else args.language,
        )
        data = [
            {"start": float(s.start), "end": float(s.end), "text": s.text}
            for s in segments
        ]
        # 成功路径严格顺序：写 JSON（关闭落盘）→ flush → os._exit(0)，避免把成功当失败
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        # 透传 faster-whisper 检测到的权威语种（info.language），供主流程信任，
        # 避免纯汉字日语/韩语被文本启发式误判成中文（产品评审 P0）。
        # 用独立 side-file，不污染 segments JSON 契约。
        if detected_language:
            try:
                with open(str(args.output_json) + ".lang", "w", encoding="utf-8") as lf:
                    lf.write(detected_language)
                    lf.flush()
                    os.fsync(lf.fileno())
            except Exception:  # noqa: BLE001
                pass
        print(f"_WORKER_DONE segments={len(segments)}")
        sys.stdout.flush()
        sys.stderr.flush()
        # 关键：直接退出，绕过 WhisperModel/CTranslate2 析构（Windows 下偶发硬崩/挂起）。
        # 子进程死亡后 OS 回收全部内存与 CUDA 上下文，主流程不受影响。
        # 注意：进度文件（<audio>.progress.json）由父进程在最终成功后删除，此处不删，
        # 以便子进程崩溃时父进程能从断点续传。
        os._exit(0)
    except Exception:  # noqa: BLE001
        # 把 traceback 打到 stderr，父进程会捕获并分类（OOM vs 真崩溃）
        sys.stderr.write(traceback.format_exc())
        sys.stderr.flush()
        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
