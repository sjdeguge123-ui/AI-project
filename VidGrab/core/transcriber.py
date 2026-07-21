# 语音转录模块（无字幕视频：音频 -> 带时间戳文字）
# Phase 0 — 双模式：云端 Whisper API / 本地 faster-whisper；含引导文案生成器
"""把无字幕视频的音频转成带时间戳的 Segment 列表。

入口：transcribe(transcript, whisper_config) -> Transcript
  - transcript.audio_path 必须存在（由 extractor 下载音频得到）
  - 返回新的 Transcript，source="transcript"，segments 为识别出的文字段落

两种模式（由 config.whisper.mode 决定）：
  - "api"   : 调 OpenAI Whisper API（需 OpenAI Key，按量收费）
  - "local" : 本地 faster-whisper 推理（免费，首次自动下载模型，需 ffmpeg）

无字幕且未配置转录时，build_whisper_guide() 会输出完整引导，告诉用户怎么选、怎么配。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time as _time
from pathlib import Path
from typing import List, Optional, Tuple

from . import Segment, Transcript
from .config import WhisperConfig
from .lang import _detect_language

try:
    import psutil  # 可选依赖：缺失时跳过内存预检，不阻塞转录
except ImportError:
    psutil = None

import atexit

# ----------------------------------------------------------------------------
# 转录子进程生命周期管理
# ----------------------------------------------------------------------------
# 根因：Windows 不会在父进程死亡时级联杀掉子进程。VidGrab 的转录跑在独立 worker 子进程
# （core/transcribe_worker）里，若主进程被外部杀掉（agent 会话结束 / 终端关闭 / 崩溃），
# worker 会遗留成孤儿、一直占着内存。下面用「模块级记录 + atexit + 控制台事件」三重兜底，
# 保证主进程无论以何种方式退出，worker 都会被清掉。
_ACTIVE_WORKER = None


def _kill_active_worker() -> None:
    """强制结束当前正在运行的转录 worker 子进程（若有）。"""
    global _ACTIVE_WORKER
    proc = _ACTIVE_WORKER
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()  # Windows TerminateProcess / POSIX SIGKILL，确保立即退出
        except Exception:  # noqa: BLE001
            pass
    _ACTIVE_WORKER = None


# 正常退出 / 未捕获异常 / Ctrl+C(KeyboardInterrupt 收尾) 都会走到 atexit。
atexit.register(_kill_active_worker)


if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        _kernel32 = ctypes.windll.kernel32

        def _console_ctrl_handler(ctrl_type: int) -> int:
            # 2=CLOSE / 5=LOGOFF / 6=SHUTDOWN：先杀 worker，再交回「已处理」让 Windows 终止本进程
            if ctrl_type in (2, 5, 6):
                _kill_active_worker()
                return 1
            # 0=CTRL_C 等其他信号：交回默认处理，保留既有 KeyboardInterrupt 行为
            return 0

        _HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        _kernel32.SetConsoleCtrlHandler(_HANDLER(_console_ctrl_handler), True)

        # ── Windows Job Object：父进程死亡时自动杀子进程 ──
        # 根因：atexit/控制台事件/线程看门狗都可能因 agent 强杀父进程或 GIL 被 C 扩展占住而失效。
        # Job Object 是 OS 级保证：job handle 随父进程关闭，内部所有进程被系统强制终止。
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        _JobObjectExtendedLimitInformation = 9

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", wintypes.ULARGE_INTEGER),
                ("WriteOperationCount", wintypes.ULARGE_INTEGER),
                ("OtherOperationCount", wintypes.ULARGE_INTEGER),
                ("ReadTransferCount", wintypes.ULARGE_INTEGER),
                ("WriteTransferCount", wintypes.ULARGE_INTEGER),
                ("OtherTransferCount", wintypes.ULARGE_INTEGER),
            ]

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_ulonglong),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        def _create_kill_job() -> Optional[int]:
            """创建 job object；父进程 handle 关闭时自动 kill 内部所有进程。"""
            job = _kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = _kernel32.SetInformationJobObject(
                job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                _kernel32.CloseHandle(job)
                return None
            return job

        def _assign_to_job(job: int, pid: int) -> bool:
            h = _kernel32.OpenProcess(0x001F0FFF, False, pid)  # PROCESS_ALL_ACCESS
            if not h:
                return False
            try:
                return bool(_kernel32.AssignProcessToJobObject(job, h))
            finally:
                _kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass


def _fmt_ts(sec) -> str:
    """把秒数格式化成「X分Y秒」，用于转录进度展示。"""
    try:
        sec = int(sec or 0)
        return f"{sec // 60}分{sec % 60}秒"
    except Exception:  # noqa: BLE001
        return str(sec)


def _preflight_memory(threshold_gb: float = 2.5) -> None:
    """转录前的系统内存/页面文件预检（P0 最高杠杆）。

    转录（尤其本地 faster-whisper）加载 torch/CTranslate2 时要向系统提交数百 MB 连续
    虚拟内存。Windows 下若页面文件太小、或物理内存+页面文件余量不足，会在模型加载期
    直接抛 WinError 1455 / mkl_malloc / ArrayMemoryError，表现为「时好时坏、刚跑通又崩」。

    本函数：尽量用 psutil 估算「可用物理内存 + 页面文件余量」，低于阈值则提前打印清晰
    告警与处置建议（切换 whisper.mode: api 云端、或换更小模型、或增大页面文件）。
    注意：仅为「告警增强」——不阻断转录（仍继续尝试），把最终失败/回退交给 OOM 分类逻辑。

    psutil 未安装时直接跳过（不阻塞）：把可用内存预检作为「有则增强」的防护，而非硬依赖。
    """
    if psutil is None:
        return  # psutil 缺失：跳过预检，不阻塞转录

    try:
        vm = psutil.virtual_memory()
        avail = getattr(vm, "available", 0) or 0
        # 页面文件余量近似：Windows 上 swap_memory().free 即页面文件可用空间
        swap_free = 0
        try:
            swap = psutil.swap_memory()
            swap_free = getattr(swap, "free", 0) or 0
        except Exception:  # noqa: BLE001
            swap_free = 0
        total_avail = avail + swap_free
        gb = total_avail / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return  # 取不到内存信息：跳过预检

    if gb < threshold_gb:
        print(
            f"   ⚠️ 系统可用内存/页面文件偏低（约 {gb:.1f} GB，阈值 {threshold_gb:.1f} GB），"
            "本地转录可能不稳定（易触发 WinError 1455 / mkl_malloc / ArrayMemoryError）。\n"
            "   建议：① 改用云端模式（config.yaml 设 whisper.mode: api）；"
            "② 使用更小的本地模型（如 tiny/base）；"
            "③ 增大 Windows 虚拟内存（页面文件）到物理内存的 1.5–2 倍；"
            "④ 关闭其他重型程序后重试。\n"
            "   （预检仅为告警，转录仍将继续尝试；若失败将自动触发 GPU→CPU→api 回退。）"
        )
        # 仅告警，不阻断：仍继续尝试转录


def transcribe(transcript: Transcript, config: Optional[WhisperConfig] = None) -> Transcript:
    """把 transcript.audio_path 转录成带时间戳的 Segment，返回 source='transcript' 的新 Transcript。"""

    if not transcript.audio_path:
        raise ValueError(
            "transcribe 需要 audio_path（无字幕视频的音频文件）。\n"
            "请确认 extractor 在 download_audio=True 时成功下载了音频（B站 / YouTube 均支持）。\n"
            "若音频下载失败，通常是缺少 ffmpeg：请确认已安装 ffmpeg 并已加入系统 PATH，"
            "或在 config.yaml 的 whisper 段配置 ffmpeg_location 指向 ffmpeg.exe 所在目录。"
        )

    config = config or WhisperConfig()
    audio_path = Path(transcript.audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在：{audio_path}")

    if config.mode == "api":
        # API 路径：OpenAI 不返回权威语种，沿用文本启发式判定（兼容 API 测试）
        segments = _transcribe_api(audio_path, config.api_key, language=getattr(config, "language", None))
        detected_language = _detect_language(segments)
    elif config.mode == "local":
        # 本地路径：优先信任 faster-whisper 透传的 info.language（权威语种信号），
        # 仅当透传为空（极少数情况）才退回文本启发式兜底——
        # 避免纯汉字日语/韩语被误判成中文（产品评审 P0）
        segments, detected_language = _transcribe_local(
            audio_path,
            config.local_model,
            device=getattr(config, "device", "auto"),
            compute_type=getattr(config, "compute_type", "auto"),
            language=getattr(config, "language", None),
        )
        if not detected_language:
            detected_language = _detect_language(segments)
    else:
        raise ValueError(
            f"未知的 whisper.mode：{config.mode!r}（应为 'api' 或 'local'）。\n{build_whisper_guide()}"
        )

    # 根据实际转录结果判定语种，确保后续摘要/全文文案跟随音频真实语种。
    # detected_language 已优先采用权威信号（faster-whisper info.language / B站 lan），
    # 此处仅对中文做繁简规范，不再用文本启发式覆盖权威语种。
    # 中文音频转录结果统一规范为简体中文（覆盖繁体口音/台湾用字）
    if detected_language == "zh":
        from .lang import _normalize_chinese

        segments = [
            Segment(start=s.start, end=s.end, text=_normalize_chinese(s.text or ""))
            for s in segments
        ]
    return Transcript(
        platform=transcript.platform,
        video_id=transcript.video_id,
        title=transcript.title,
        author=transcript.author,
        publish_time=transcript.publish_time,
        language=detected_language,
        duration=transcript.duration,
        segments=segments,
        source="transcript",
        audio_path=transcript.audio_path,
    )


def _transcribe_api(audio_path: Path, api_key: str, language: Optional[str] = None) -> List[Segment]:
    """云端 Whisper API（OpenAI）。需 api_key。language=None 时由云端自动检测。"""

    if not api_key:
        raise ValueError(
            "云端 Whisper 需要 OpenAI API Key（config.whisper.api_key 为空）。\n" + build_whisper_guide()
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            language=language,
        )

    segments: List[Segment] = []
    for s in getattr(resp, "segments", []) or []:
        text = (s.text or "").strip()
        if text:
            segments.append(Segment(start=float(s.start), end=float(s.end), text=text))
    if not segments:
        # 兜底：有些返回只有整段 text 无 segments，按整段处理
        full = (getattr(resp, "text", "") or "").strip()
        if full:
            segments.append(Segment(start=0.0, end=transcript_duration_hint(audio_path), text=full))
    return segments


def _load_whisper_model(model_path, device, compute_type, max_attempts: int = 5):
    """加载 WhisperModel，对瞬时内存分配失败（mkl_malloc / CUDA OOM）做有限重试。

    Windows 下 MKL / CUDA 的瞬时分配失败具有偶发性（空闲内存充足也会因碎片化失败），
    重试通常能成功，避免「时好时坏、刚修好又崩」。每次重试前 gc.collect() 降低内存碎片。
    返回模型对象；若全部重试失败则返回最后一个异常对象（调用方据此判断是否需要回退）。
    """

    import gc
    from faster_whisper import WhisperModel

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return WhisperModel(model_path, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_attempts:
                gc.collect()
                print(f"   ⚠️ 模型加载失败（第 {attempt}/{max_attempts} 次，疑似瞬时内存分配失败），3 秒后重试...")
                _time.sleep(3)
    return last_exc


def _run_local_transcription(
    audio_path: Path,
    model_size: str,
    device: str = "auto",
    compute_type: str = "auto",
    chunk_sec: int = 120,
    resume_sec: float = 0.0,
    language: Optional[str] = None,
) -> Tuple[List[Segment], Optional[str]]:
    """本地 faster-whisper 推理（实际执行体，运行在隔离子进程内）。

    device: auto（自动检测）| cpu | cuda
    compute_type: auto（GPU 默认 int8_float16，CPU 默认 int8）| float16 | int8 | int8_float16
    chunk_sec: 每块转录秒数。GPU/CPU 统一用 120 秒——固定块长保证断点续传时分块边界一致。
    resume_sec: 断点续传起点（秒）。>0 时跳过已转录的块，并从进度文件合并已有 segments。
    language: 语种锁定（None=自动检测），透传给 faster-whisper / Whisper API。

    注意：此函数被 core/transcribe_worker.py 在独立子进程中调用，目的是把 GPU/CUDA
    状态完全隔离，避免 Windows 下 CTranslate2 模型析构时硬崩溃拖垮主流程。
    """

    from faster_whisper import WhisperModel
    import ctranslate2

    audio_path = Path(audio_path)

    # 优先用本地模型目录（绕过 HuggingFace 下载，适合代理 SSL 受限环境）
    local_dir = Path(__file__).parent.parent / "models" / f"faster-whisper-{model_size}"
    model_path = str(local_dir) if local_dir.exists() else model_size

    # GPU 检测与设备选择
    cuda_count = ctranslate2.get_cuda_device_count()
    if device == "auto":
        if cuda_count > 0:
            use_device = "cuda"
            print(f"🚀 检测到 GPU（CUDA 设备数={cuda_count}），使用 GPU 加速转录")
        else:
            use_device = "cpu"
            print("⚠️ 未检测到 GPU，使用 CPU 转录（速度较慢）。如需加速：")
            print("   · 确认已安装 NVIDIA 显卡驱动")
            print("   · 或改 config.yaml 的 whisper.mode 为 api 用云端转录")
    elif device == "cuda":
        if cuda_count > 0:
            use_device = "cuda"
            print(f"🚀 使用 GPU 加速转录（CUDA 设备数={cuda_count}）")
        else:
            use_device = "cpu"
            print("⚠️ 指定了 cuda 但未检测到 GPU，回退到 CPU 转录。")
    else:
        use_device = "cpu"
        print("🖥️ 使用 CPU 转录（compute_type=int8）")

    # compute_type 选择：auto 时 GPU 用 int8_float16（比 float16 稳且快），CPU 用 int8
    if compute_type == "auto":
        use_compute_type = "int8_float16" if use_device == "cuda" else "int8"
    else:
        use_compute_type = compute_type
    print(f"   ⚙️ 计算类型：{use_compute_type}（device={use_device}）")
    print(f"   🧩 分块长度：{chunk_sec} 秒/块（{'GPU' if use_device == 'cuda' else 'CPU'} 模式）")

    if local_dir.exists():
        print(f"   📂 使用本地模型：{local_dir}")
    else:
        print(f"   🌐 首次运行需从 HuggingFace 下载 faster-whisper-{model_size} 模型，请保持网络畅通...")

    import gc
    gc.collect()
    print("   ⏳ 正在加载 faster-whisper 模型...")
    model = _load_whisper_model(model_path, use_device, use_compute_type, max_attempts=5)
    if isinstance(model, Exception):
        # 主设备（cuda）加载失败 → 回退 CPU 再重试
        if use_device == "cuda":
            print(f"   ⚠️ GPU 加载失败：{model}")
            print("   🔄 自动回退到 CPU 转录（可在 config.yaml 将 whisper.device 设为 cpu 永久使用）...")
            use_device, use_compute_type = "cpu", "int8"
            model = _load_whisper_model(model_path, use_device, use_compute_type)
        if isinstance(model, Exception):
            raise RuntimeError(
                f"加载 faster-whisper 模型失败：{model}\n"
                "常见原因：① 模型文件损坏/缺失 ② 网络问题导致下载失败 ③ 内存/显存不足（瞬时分配失败已重试）。\n"
                "建议：检查网络、确认模型目录完整，或改用云端模式（whisper.mode: api）。"
            ) from model

    # 预先把音频转成 16kHz mono WAV，避免 faster-whisper 内部读取/重采样时触发端崩溃
    wav_path = _ensure_wav_16k_mono(audio_path)

    # 断点续传：进度文件与音频同名（key 为原始 audio_path，父进程与子进程用同一公式推导，
    # 不受「音频是否已为 16k wav」影响），保证边界一致、不会串音。
    progress_path = Path(str(audio_path) + ".progress.json")
    existing_segments: List[dict] = []
    if resume_sec and resume_sec > 0 and progress_path.exists():
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                prog = json.load(f)
            existing_segments = list(prog.get("segments", []) or [])
            print(f"   🔄 检测到断点进度（已完成 {resume_sec:.0f}s，已有 {len(existing_segments)} 段），将从断点续传...")
        except Exception:  # noqa: BLE001
            existing_segments = []

    # 关键：分块转录。直接把整段文件路径丢给 model.transcribe() 会让 faster-whisper
    # 一次性解码 + 全量 STFT（feature_extractor 对整段音频建帧数组 + rfft 复数数组），
    # 长视频（30min+）峰值需要数 GB 连续主机内存、且要求一次性分配，空闲内存波动时
    # 会间歇性抛 numpy ArrayMemoryError（表现为「时好时坏、转录后自己结束」）。
    # 改为：先解码成 16kHz mono numpy（33min 约 128MB），再按 CHUNK_SEC 秒切片逐块转录，
    # 每块特征数组只有几分钟大小（峰值降两个数量级），并按块起点偏移拼接时间戳。
    # 每块成功后原子写 progress.json，崩溃可续传，避免反复从头重跑吃满页面文件。
    print("   🎙️ 开始转录，请稍候（长音频自动分块，期间会显示进度）...")
    segments, detected_language = _transcribe_chunked(
        model,
        wav_path,
        chunk_sec=chunk_sec,
        resume_sec=resume_sec,
        progress_path=progress_path,
        existing_segments=existing_segments,
        language=language,
    )
    return segments, detected_language


def _transcribe_local(
    audio_path: Path,
    model_size: str,
    device: str = "auto",
    compute_type: str = "auto",
    language: Optional[str] = None,
) -> Tuple[List[Segment], Optional[str]]:
    """本地 faster-whisper 转录（子进程隔离版）。

    把 GPU/CUDA 重活放进独立子进程（core/transcribe_worker.py），子进程退出时 CUDA 状态
    随之一并销毁，主流程继续 AI 摘要，彻底规避 Windows 下 CTranslate2 模型析构时的硬崩溃
    （表现为「转录完成却忽然回到提示符、无任何报错」）。

    回退策略：
      第 1 次：按 config 的 device（auto 则优先 GPU）
      第 2 次：若 GPU 崩，强制 CPU + int8
      第 3 次：CPU 仍崩（非内存类）→ 再次 CPU 重试；但若是内存/页面文件类（OOM）失败，
              连续 2 次即提前终止，避免反复全量重启把页面文件吃满（见 OOM 早停）。

    断点续传：每次启动子进程前检测进度文件，存在则把已完成秒数通过 --resume 传给 worker，
    从断点续跑而非从头重来。GPU→CPU 回退时固定 chunk_sec=120，保证分块边界一致。
    """

    audio_path = Path(audio_path)
    json_path = audio_path.with_suffix(".segments.json")
    progress_path = Path(str(audio_path) + ".progress.json")
    project_root = Path(__file__).parent.parent

    # P0 内存预检：可用内存/页面文件不足时提前给清晰建议，避免硬闯后被 OOM 打挂再反复重启
    _preflight_memory()

    max_runs = 3
    last_err: Optional[Exception] = None
    oom_streak = 0  # 连续 OOM 类失败计数，达到 2 即早停
    use_device = device
    use_compute_type = compute_type
    use_chunk_sec = 120  # GPU/CPU 统一 120 秒固定块长（续传边界一致；小块不缓解 MKL 加载期分配）
    for run_i in range(1, max_runs + 1):
        if run_i > 1:
            print(f"   🔁 重试本地转录子进程（第 {run_i}/{max_runs} 次）...")
            # 若前一次走 GPU 路径硬崩溃，重试强制 CPU（牺牲速度换稳定）
            if run_i == 2 and use_device in ("auto", "cuda"):
                print("   🖥️ GPU 路径已崩溃，重试改用 CPU（更稳定，速度较慢）...")
                use_device = "cpu"
                use_compute_type = "int8"
                use_chunk_sec = 120

        # 断点续传：读进度文件取已完成秒数（崩溃残留则续跑，成功则已被本函数删除）
        resume_sec = 0.0
        if progress_path.exists():
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    resume_sec = float(json.load(f).get("completed_until_sec", 0) or 0)
            except Exception:  # noqa: BLE001
                resume_sec = 0.0

        # 子进程环境变量：
        #  - CT2_CUDA_ALLOCATOR=cuda_malloc_async：根治 Windows 下 CUDA 默认分配器的
        #    显存碎片化，避免长视频 GPU 转录中途「Unable to allocate ... MiB」与进程退出时
        #    硬崩溃（STATUS_STACK_BUFFER_OVERRUN, exit 3221226505）。实测 40min 视频稳定通过。
        #  - CPU 路径再降 MKL/OpenMP 线程数，减少瞬时内存分配与线程冲突。
        env = os.environ.copy()
        # GPU 修复：根治 Windows 下 CUDA 默认分配器显存碎片化（长视频中途/进程退出时硬崩）
        env["CT2_CUDA_ALLOCATOR"] = "cuda_malloc_async"
        # CPU 路径加固：强制 MKL/OpenMP 单线程 + 允许重复库 + 关闭 MKL 动态线程，
        # 降低 mkl_malloc 瞬时分配失败概率。无条件设置（对 GPU 模式无害，且能压制
        # GPU 模式下 MKL 的零星分配，避免「时好时坏」）。
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        env["MKL_DYNAMIC"] = "FALSE"
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        # GPU 修复（二）：NVIDIA 懒加载 CUDA 模块（CUDA 11.7+），大幅降低加载
        # cublas64_12.dll 等时对 Windows 页面文件（提交内存）的峰值占用，
        # 根治 WinError 1455「页面文件太小，无法完成操作」。CPU 模式下此变量无害。
        env["CUDA_MODULE_LOADING"] = "LAZY"

        cmd = [
            sys.executable, "-u", "-m", "core.transcribe_worker",
            "--audio-path", str(audio_path),
            "--model-size", str(model_size),
            "--device", str(use_device),
            "--compute-type", str(use_compute_type),
            "--chunk-sec", str(use_chunk_sec),
            "--output-json", str(json_path),
            "--resume", str(resume_sec),
            # 注意：config 默认 whisper.language="auto"，而 faster-whisper 不接受字符串 "auto"
            # （只接受 None 或具体语种码如 "zh"）。这里把 "auto" 归一为空串，worker 侧再转 None。
            "--language", str(language) if (language and language != "auto") else "",
            # 让 worker 能检测 skill 主进程是否还活着：主进程若异常退出，worker 自清理，避免残留。
            "--parent-pid", str(os.getpid()),
        ]
        print("   🔧 启动本地转录子进程（隔离 GPU 状态，避免崩溃拖垮主流程）...")
        # Windows：用 job object 保证父进程死亡时 worker 被系统强制回收（比线程看门狗更可靠）
        kill_job = None
        creationflags = 0
        if sys.platform == "win32":
            try:
                kill_job = _create_kill_job()
                if kill_job:
                    creationflags = subprocess.CREATE_BREAKAWAY_FROM_JOB
            except Exception:  # noqa: BLE001
                kill_job = None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            cwd=str(project_root),
            env=env,
            creationflags=creationflags,
        )
        if kill_job:
            try:
                if not _assign_to_job(kill_job, proc.pid):
                    _kernel32.CloseHandle(kill_job)
                    kill_job = None
            except Exception:  # noqa: BLE001
                try:
                    _kernel32.CloseHandle(kill_job)
                except Exception:  # noqa: BLE001
                    pass
                kill_job = None
        # 记录活跃 worker，供 atexit / 控制台事件 / 看门狗在退出时强制清理，避免遗留孤儿进程
        _ACTIVE_WORKER = proc
        # 实时转发子进程进度输出（含分块进度），并缓冲用于失败根因分类
        assert proc.stdout is not None
        child_log: list[str] = []
        for line in proc.stdout:
            s = line.rstrip("\n")
            print(s)
            child_log.append(s)
        rc = proc.wait()
        # 本轮 worker 已结束（成功或失败），清空记录，避免 atexit 误杀已退出的进程
        _ACTIVE_WORKER = None
        # 关闭 job object；正常退出时 worker 已结束，关闭无害；异常退出时 handle 关闭会触发系统杀 worker
        if kill_job:
            try:
                _kernel32.CloseHandle(kill_job)
            except Exception:  # noqa: BLE001
                pass

        if rc == 0 and json_path.exists():
            detected_language: Optional[str] = None
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                segments = [
                    Segment(start=float(d["start"]), end=float(d["end"]), text=str(d["text"]))
                    for d in raw
                ]
                # 读取 worker 透传的语种（faster-whisper 的 info.language），优先信任，
                # 避免纯汉字日语/韩语被文本启发式误判成中文（产品评审 P0）
                detected_language = _read_worker_lang(json_path)
            finally:
                try:
                    json_path.unlink()
                except Exception:  # noqa: BLE001
                    pass
                # 清理透传语种 side-file（成功路径，避免残留）
                try:
                    Path(str(json_path) + ".lang").unlink()
                except Exception:  # noqa: BLE001
                    pass
                # 成功：删除断点进度文件（避免下次误续传旧音频残留）
                try:
                    progress_path.unlink()
                except Exception:  # noqa: BLE001
                    pass
            return segments, detected_language

        # 失败：识别 OOM 类信号（页面文件/显存/内存分配），连续 2 次则早停，避免反复重启吃满页面文件
        child_text = "\n".join(child_log)
        is_oom = _is_oom_failure(child_text)
        if is_oom:
            oom_streak += 1
        else:
            oom_streak = 0
        # 针对「页面文件太小」(WinError 1455) 给出更精准的可执行提示
        if "页面文件太小" in child_text or "WinError 1455" in child_text:
            hint = (
                "GPU 加载 CUDA 库时触发 Windows「页面文件太小」(WinError 1455)：\n"
                "已自动注入 CUDA_MODULE_LOADING=LAZY 降低页面文件占用；若仍报此错，"
                "请到「系统→高级系统设置→性能→高级→虚拟内存」把页面文件调到物理内存的 1.5–2 倍并重启，"
                "或直接在 config.yaml 设 whisper.mode: api 走云端转录，彻底规避本地显存/内存限制。"
            )
        else:
            hint = (
                "常见原因：① 模型加载/GPU 崩溃（瞬时内存分配失败）② 音频文件损坏 ③ 依赖缺失。\n"
                "建议：在 config.yaml 设置 whisper.device: cpu 或 whisper.compute_type: int8，"
                "或改用 whisper.mode: api 云端转录。"
            )
        last_err = RuntimeError(
            f"本地转录子进程异常退出（exit code={rc}）。\n" + hint
        )
        # 清理可能残留的 json（进度文件保留用于续传）
        try:
            json_path.unlink()
        except Exception:  # noqa: BLE001
            pass

        if oom_streak >= 2:
            raise RuntimeError(
                "转录因系统内存/页面文件不足反复失败（已连续 2 次报内存分配类错误），"
                "不再盲目重试以免把虚拟内存吃满。\n"
                "建议：① 增大 Windows 虚拟内存（页面文件）到物理内存的 1.5–2 倍；"
                "② 改用云端模式（config.yaml 设 whisper.mode: api）；"
                "③ 关闭其他重型程序后重试。"
                "（若仍想本地尝试，可在 config.yaml 把 whisper.device 设为 cpu 并换更小模型。）"
            )
        _time.sleep(2)

    # 全部重试失败（非 OOM 类真崩溃，已尽回退）
    raise last_err


# OOM / 内存-页面文件类失败信号：命中任一即视为「系统资源不足」，用于早停。
# 注意：STATUS_STACK_BUFFER_OVERRUN / 3221226505 是 Windows fast-fail 原生中止，
# 常见诱因是 cudnn/CUDA 库版本冲突，不是内存不足，反复重试无意义，故不列入 OOM。
_OOM_SIGNALS = (
    "WinError 1455",
    "页面文件太小",
    "mkl_malloc",
    "ArrayMemoryError",       # 规范指定：numpy 内存分配失败
    "_ArrayMemoryError",       # numpy 异常类全名中的子串
    "cuBLAS_NOT_SUPPORTED",    # 规范指定：cuBLAS 不支持（显存/算力不足）
    "cuBLAS_STATUS_NOT_SUPPORTED",
)


def _is_oom_failure(text: str) -> bool:
    """判断子进程失败输出是否属于内存/页面文件不足类（OOM）。"""
    return any(sig in text for sig in _OOM_SIGNALS)


def _read_worker_lang(json_path: Path) -> Optional[str]:
    """读取 worker 子进程透传的语种 side-file（<output-json>.lang）。

    worker 把 faster-whisper 检测到的 info.language 写入该 side-file（不破坏 segments JSON 契约）。
    文件不存在或读取失败返回 None，由调用方退回文本启发式兜底。
    """
    lang_path = Path(str(json_path) + ".lang")
    if not lang_path.exists():
        return None
    try:
        v = lang_path.read_text(encoding="utf-8").strip()
        return v or None
    except Exception:  # noqa: BLE001
        return None


# 单块采样率（固定 16kHz mono）
_SAMPLE_RATE = 16000

# 固定块长：GPU/CPU 统一 120 秒。固定块长保证断点续传时分块边界一致（resume 不串音），
# 且「小块缓解 MKL 加载期分配」是误区——MKL 加载在分块之前就完成，小块只会增开销。


def _transcribe_chunked(
    model,
    wav_path: Path,
    chunk_sec: int = 120,
    resume_sec: float = 0.0,
    progress_path: Optional[Path] = None,
    existing_segments: Optional[List[dict]] = None,
    language: Optional[str] = None,
) -> Tuple[List[Segment], Optional[str]]:
    """按固定时长用标准库 wave 分块读取 PCM 并逐块转录，拼接时间戳（支持断点续传）。

    为什么必须按块「流式」读取，而不是 decode_audio 整段解码：
    faster-whisper 的 feature_extractor 会对「喂进来的整段音频」一次性做 STFT；而
    faster_whisper.audio.decode_audio 又先把整段音频解码成一个连续大数组。长视频(30min+)
    下，这两个「整段」操作都需要上百 MB 的【连续】主机内存，空闲内存波动/碎片化时会
    间歇性抛 numpy ArrayMemoryError（表现为「时好时坏、转录后自己结束」）。

    改为：直接用标准库 wave 按块读取 16k mono PCM（每块仅约几 MB），转 float32 后
    逐块喂给 model.transcribe()，每次只处理一小块，峰值内存恒定在几十 MB，无论视频多长都稳定。

    断点续传：resume_sec>0 时跳过 offset<resume_sec 的已完成块（resume_sec 必为整块边界）；
    已有 segments 先并入结果，新块完成后原子写 progress.json（写临时文件再 os.replace），
    子进程崩溃后该文件仍留存，父进程下次重试用 --resume 从断点续跑，避免反复从头重跑。
    """

    import gc
    import numpy as np
    import wave

    existing_segments = existing_segments or []
    # 合并已有 segments：existing 来自进度文件，offset 已是全局时间戳，直接并入
    segments: List[Segment] = [
        Segment(start=float(d["start"]), end=float(d["end"]), text=str(d["text"]))
        for d in existing_segments
    ]
    _done_existing = len(segments)

    # 透传语种：显式锁定语种（config 指定）直接采用；否则首块自动检测后填充
    detected_language: Optional[str] = language

    with wave.open(str(wav_path), "rb") as wf:
        fr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        nframes = wf.getnframes()
        if fr != _SAMPLE_RATE or nch != 1 or sw != 2:
            raise RuntimeError(
                f"音频不是预期的 16k mono 16bit PCM（fr={fr}, nch={nch}, sw={sw}），"
                "请检查 _ensure_wav_16k_mono 是否生效，或改用 whisper.mode: api 云端转录。"
            )

        chunk_frames = chunk_sec * fr
        total_dur = nframes / fr
        n_chunks = max(1, (nframes + chunk_frames - 1) // chunk_frames)

        _last_log = _time.time()
        _count = 0
        for ci in range(n_chunks):
            s0 = ci * chunk_frames
            s1 = min(nframes, s0 + chunk_frames)
            offset = s0 / fr  # 该块在整段音频中的起始秒数，用于还原全局时间戳
            # 断点续传：跳过已完成块（resume_sec 必等于某块结束边界）
            if resume_sec and offset < resume_sec - 1e-6:
                continue
            raw = wf.readframes(s1 - s0)
            if not raw:
                break
            # int16 PCM -> float32 in [-1, 1]（只读这一小块，约几 MB，绝不整段分配）
            audio_chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            try:
                seg_gen, _info = model.transcribe(
                    audio_chunk,
                    beam_size=5,
                    word_timestamps=False,
                    condition_on_previous_text=False,  # 避免缓存/复用前一段文本，降低内存与错位风险
                    language=language,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"faster-whisper 转录过程出错（第 {ci + 1}/{n_chunks} 块）：{exc}\n"
                    "常见原因：① 音频文件损坏 ② 内存/显存异常 ③ 模型与硬件不匹配。\n"
                    "建议：尝试在 config.yaml 设置 whisper.device: cpu 或 whisper.compute_type: int8，\n"
                    "      或改用 whisper.mode: api 云端转录。"
                ) from exc

            # 首次自动检测到的语种：faster-whisper 的权威判定，优先信任
            # （避免纯汉字日语/韩语被文本启发式误判成中文）。一旦检测到即锁定，
            # 后续块沿用同一语种，避免逐块重检测导致漂移。
            if language is None and _info is not None:
                _dl = getattr(_info, "language", None)
                if _dl:
                    language = _dl
                    detected_language = _dl

            for seg in seg_gen:
                text = (seg.text or "").strip()
                if text:
                    segments.append(
                        Segment(start=float(seg.start) + offset, end=float(seg.end) + offset, text=text)
                    )
                _count += 1
                _now = _time.time()
                if _now - _last_log >= 15:
                    _last_log = _now
                    cur = (seg.end or 0) + offset
                    _pct = int(cur / total_dur * 100) if total_dur else 0
                    _pct = max(0, min(100, _pct))
                    print(f"   ⏳ 转录进度：{_fmt_ts(cur)} / {_fmt_ts(total_dur)}（{_pct}%，已 {_count} 段，块 {ci + 1}/{n_chunks}）")

            # 每块结束打印一次，让用户看到分块推进
            done_dur = s1 / fr
            _dpct = int(done_dur / total_dur * 100) if total_dur else 100
            print(f"   ✅ 已完成第 {ci + 1}/{n_chunks} 块（累计 {_fmt_ts(done_dur)} / {_fmt_ts(total_dur)}，{_dpct}%）")

            # 断点续传：每完成一块即原子写进度（含已合并的累计 segments + 本块结束边界）。
            # 写临时文件再 os.replace，保证幂等、崩溃时仍留上一块的有效进度。
            if progress_path is not None:
                _write_progress_atomic(progress_path, done_dur, segments)

            # 主动 GC，降低长音频多段情况下的内存碎片
            gc.collect()

    return segments, detected_language


def _write_progress_atomic(progress_path: Path, completed_until_sec: float, segments: List[Segment]) -> None:
    """原子写断点续传进度文件（写临时文件 -> os.replace），避免半截写损坏。"""
    import tempfile

    data = {
        "completed_until_sec": float(completed_until_sec),
        "segments": [
            {"start": float(s.start), "end": float(s.end), "text": str(s.text)}
            for s in segments
        ],
    }
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(progress_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, str(progress_path))
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        # 进度写失败不应影响转录本身（下一块重试时还会再写）
        pass


def _ensure_wav_16k_mono(audio_path: Path) -> Path:
    """用 ffmpeg 把任意音频转成 16kHz mono WAV，faster-whisper 最稳的输入格式。

    若已经是符合要求的 WAV，直接返回原路径；否则生成临时 .wav。
    """

    import shutil
    import subprocess
    import wave

    # 先检查是否已经是 16kHz mono WAV
    try:
        with wave.open(str(audio_path), "rb") as w:
            if w.getframerate() == 16000 and w.getnchannels() == 1:
                return audio_path
    except Exception:  # noqa: BLE001
        pass

    ffmpeg_cmd = "ffmpeg"
    if not shutil.which("ffmpeg"):
        # 系统无 ffmpeg 时，尝试用 imageio-ffmpeg 的自带二进制兜底（零配置开箱即用）
        try:
            import imageio_ffmpeg
            ffmpeg_bin = Path(imageio_ffmpeg.get_ffmpeg_exe())
            ffmpeg_cmd = str(ffmpeg_bin)
            print(f"   ℹ️ 未检测到系统 ffmpeg，使用内置 ffmpeg：{ffmpeg_bin}")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "需要 ffmpeg 把音频转成 faster-whisper 适用的 WAV 格式，但系统 PATH 中未找到 ffmpeg，"
                f"且自动使用 imageio-ffmpeg 兜底失败：{exc}。\n"
                "请先安装 ffmpeg 并加入 PATH，再重试。"
            ) from exc

    wav_path = audio_path.with_suffix(".wav")
    print(f"   🔄 用 ffmpeg 把音频转成 16kHz mono WAV：{wav_path.name}")
    proc = subprocess.run(
        [
            ffmpeg_cmd,
            "-y",                      # 覆盖
            "-i", str(audio_path),     # 输入
            "-ar", "16000",            # 采样率
            "-ac", "1",                # 单声道
            "-c:a", "pcm_s16le",       # 16bit 小端 PCM
            str(wav_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="ignore")[-400:]
        raise RuntimeError(f"ffmpeg 音频转换失败：{err}")
    return wav_path


def transcript_duration_hint(audio_path: Path) -> float:
    """转录兜底时用的时长（仅用于整段兜底，尽量从文件名推断，失败给 0）。"""
    try:
        import wave

        with wave.open(str(audio_path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:  # noqa: BLE001
        return 0.0


def build_whisper_guide() -> str:
    """无字幕视频的转录引导文案（中文）。"""

    return """\
🎙️ VidGrab 无字幕视频转录引导
==================================================
你的视频没有字幕，需要先把音频转成文字，才能生成带时间戳的摘要。
VidGrab 支持两种转录方式，二选一（改 config.yaml 的 whisper.mode 即可切换）：

【方式一：云端 Whisper API —— 最简单，推荐先试】
  · 原理：把音频上传到 OpenAI 的 Whisper 接口识别
  · 收费：约 $0.006 / 分钟（≈ 1 小时视频 ¥2.5 左右），按音频时长计费
  · 需要：一个 OpenAI API Key（国内访问需梯子）
  · 申请：https://platform.openai.com/api-keys
  · 配置 config/config.yaml：
      whisper:
        mode: api
        api_key: "sk-你的OpenAIKey"

【方式二：本地 faster-whisper —— 免费，但首次较重】
  · 原理：在你自己电脑上用模型离线识别，不花 API 钱（但吃 CPU/GPU）
  · 收费：免费
  · 需要：① 装 ffmpeg（音频处理依赖）② 首次运行自动下载模型文件
  · ffmpeg 下载：https://ffmpeg.org/download.html
    （Windows：装好把 ffmpeg.exe 所在目录加入系统 PATH）
  · 模型大小（下载体积 / 精度权衡，建议 base 起步）：
      tiny   ~75 MB    最快最省，识别一般
      base   ~145 MB   推荐起步，性价比好
      small  ~460 MB   更准
      medium ~1.5 GB   准但慢
      large  ~3 GB     最准，很慢很吃资源
  · 配置 config/config.yaml：
      whisper:
        mode: local
        local_model: "base"   # 可选 tiny / base / small / medium / large

【说明】
  · 两种方式产出的文字都会按时间戳聚合成章节，摘要模板不变。
  · B站 / YouTube 的音频下载均已支持（本地模式依赖 ffmpeg，请先装好并加入 PATH）。
  · 配置后重跑即可，无需改代码。
=================================================="""
