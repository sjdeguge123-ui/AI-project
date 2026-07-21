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
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        # 延迟导入，避免子进程启动就加载重依赖（仅在成功路径需要）
        from core.transcriber import _run_local_transcription
        from core import Segment

        segments = _run_local_transcription(
            Path(args.audio_path),
            args.model_size,
            device=args.device,
            compute_type=args.compute_type,
            chunk_sec=args.chunk_sec,
            resume_sec=args.resume or 0.0,
            language=args.language or None,
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
