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
from typing import List, Optional

from . import Segment, Transcript
from .config import WhisperConfig


def _fmt_ts(sec) -> str:
    """把秒数格式化成「X分Y秒」，用于转录进度展示。"""
    try:
        sec = int(sec or 0)
        return f"{sec // 60}分{sec % 60}秒"
    except Exception:  # noqa: BLE001
        return str(sec)


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
        segments = _transcribe_api(audio_path, config.api_key)
    elif config.mode == "local":
        segments = _transcribe_local(
            audio_path,
            config.local_model,
            device=getattr(config, "device", "auto"),
            compute_type=getattr(config, "compute_type", "auto"),
        )
    else:
        raise ValueError(
            f"未知的 whisper.mode：{config.mode!r}（应为 'api' 或 'local'）。\n{build_whisper_guide()}"
        )

    return Transcript(
        platform=transcript.platform,
        video_id=transcript.video_id,
        title=transcript.title,
        author=transcript.author,
        publish_time=transcript.publish_time,
        duration=transcript.duration,
        segments=segments,
        source="transcript",
        audio_path=transcript.audio_path,
    )


def _transcribe_api(audio_path: Path, api_key: str) -> List[Segment]:
    """云端 Whisper API（OpenAI）。需 api_key。"""

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


def _load_whisper_model(model_path, device, compute_type, max_attempts: int = 3):
    """加载 WhisperModel，对瞬时内存分配失败（mkl_malloc / CUDA OOM）做有限重试。

    Windows 下 MKL / CUDA 的瞬时分配失败具有偶发性（空闲内存充足也会因碎片化失败），
    重试通常能成功，避免「时好时坏、刚修好又崩」。返回模型对象；若全部重试失败则返回
    最后一个异常对象（调用方据此判断是否需要回退 CPU 或报错）。
    """

    from faster_whisper import WhisperModel

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return WhisperModel(model_path, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_attempts:
                print(f"   ⚠️ 模型加载失败（第 {attempt}/{max_attempts} 次，疑似瞬时内存分配失败），3 秒后重试...")
                _time.sleep(3)
    return last_exc


def _run_local_transcription(
    audio_path: Path,
    model_size: str,
    device: str = "auto",
    compute_type: str = "auto",
    chunk_sec: int = 120,
) -> List[Segment]:
    """本地 faster-whisper 推理（实际执行体，运行在隔离子进程内）。

    device: auto（自动检测）| cpu | cuda
    compute_type: auto（GPU 默认 int8_float16，CPU 默认 int8）| float16 | int8 | int8_float16
    chunk_sec: 每块转录秒数。GPU 用 2 分钟求速度；CPU 用 30 秒避免 MKL 大段分配失败。

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

    print("   ⏳ 正在加载 faster-whisper 模型...")
    model = _load_whisper_model(model_path, use_device, use_compute_type)
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

    # 关键：分块转录。直接把整段文件路径丢给 model.transcribe() 会让 faster-whisper
    # 一次性解码 + 全量 STFT（feature_extractor 对整段音频建帧数组 + rfft 复数数组），
    # 长视频（30min+）峰值需要数 GB 连续主机内存、且要求一次性分配，空闲内存波动时
    # 会间歇性抛 numpy ArrayMemoryError（表现为「时好时坏、转录后自己结束」）。
    # 改为：先解码成 16kHz mono numpy（33min 约 128MB），再按 CHUNK_SEC 秒切片逐块转录，
    # 每块特征数组只有几分钟大小（峰值降两个数量级），并按块起点偏移拼接时间戳。
    print("   🎙️ 开始转录，请稍候（长音频自动分块，期间会显示进度）...")
    segments = _transcribe_chunked(model, wav_path, chunk_sec=chunk_sec)
    return segments


def _transcribe_local(audio_path: Path, model_size: str, device: str = "auto", compute_type: str = "auto") -> List[Segment]:
    """本地 faster-whisper 转录（子进程隔离版）。

    把 GPU/CUDA 重活放进独立子进程（core/transcribe_worker.py），子进程退出时 CUDA 状态
    随之一并销毁，主流程继续 AI 摘要，彻底规避 Windows 下 CTranslate2 模型析构时的硬崩溃
    （表现为「转录完成却忽然回到提示符、无任何报错」）。

    回退策略：
      第 1 次：按 config 的 device（auto 则优先 GPU）
      第 2 次：若 GPU 崩，强制 CPU + int8
      第 3 次：若 CPU 也崩，CPU 单线程 + 30 秒小块（进一步降低 MKL 内存压力）
      仍失败：提示用户改 whisper.mode: api
    """

    audio_path = Path(audio_path)
    json_path = audio_path.with_suffix(".segments.json")
    project_root = Path(__file__).parent.parent

    max_runs = 3
    last_err: Optional[Exception] = None
    use_device = device
    use_compute_type = compute_type
    use_chunk_sec = 120  # GPU 或常规 CPU 用 2 分钟块
    for run_i in range(1, max_runs + 1):
        if run_i > 1:
            print(f"   🔁 重试本地转录子进程（第 {run_i}/{max_runs} 次）...")
            # 若前一次走 GPU 路径硬崩溃，重试强制 CPU（牺牲速度换稳定）
            if run_i == 2 and use_device in ("auto", "cuda"):
                print("   🖥️ GPU 路径已崩溃，重试改用 CPU（更稳定，速度较慢）...")
                use_device = "cpu"
                use_compute_type = "int8"
                use_chunk_sec = 120
            # 若 CPU 也崩，进一步降到单线程 + 30 秒小块，避开 MKL 大段分配
            if run_i == 3:
                print("   🐢 CPU 路径仍异常，进一步限制单线程 + 30 秒小块重试...")
                use_device = "cpu"
                use_compute_type = "int8"
                use_chunk_sec = 30

        # 子进程环境变量：
        #  - CT2_CUDA_ALLOCATOR=cuda_malloc_async：根治 Windows 下 CUDA 默认分配器的
        #    显存碎片化，避免长视频 GPU 转录中途「Unable to allocate ... MiB」与进程退出时
        #    硬崩溃（STATUS_STACK_BUFFER_OVERRUN, exit 3221226505）。实测 40min 视频稳定通过。
        #  - CPU 路径再降 MKL/OpenMP 线程数，减少瞬时内存分配与线程冲突。
        env = os.environ.copy()
        env["CT2_CUDA_ALLOCATOR"] = "cuda_malloc_async"
        if use_device == "cpu":
            env["OMP_NUM_THREADS"] = "1"
            env["MKL_NUM_THREADS"] = "1"
            env["KMP_DUPLICATE_LIB_OK"] = "TRUE"

        cmd = [
            sys.executable, "-u", "-m", "core.transcribe_worker",
            "--audio-path", str(audio_path),
            "--model-size", str(model_size),
            "--device", str(use_device),
            "--compute-type", str(use_compute_type),
            "--chunk-sec", str(use_chunk_sec),
            "--output-json", str(json_path),
        ]
        print("   🔧 启动本地转录子进程（隔离 GPU 状态，避免崩溃拖垮主流程）...")
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
        )
        # 实时转发子进程进度输出（含分块进度），保持用户可见
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line.rstrip("\n"))
        rc = proc.wait()

        if rc == 0 and json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                segments = [
                    Segment(start=float(d["start"]), end=float(d["end"]), text=str(d["text"]))
                    for d in raw
                ]
            finally:
                try:
                    json_path.unlink()
                except Exception:  # noqa: BLE001
                    pass
            return segments

        # 失败：记录错误，准备重试
        last_err = RuntimeError(
            f"本地转录子进程异常退出（exit code={rc}）。\n"
            "常见原因：① 模型加载/GPU 崩溃（瞬时内存分配失败）② 音频文件损坏 ③ 依赖缺失。\n"
            "建议：在 config.yaml 设置 whisper.device: cpu 或 whisper.compute_type: int8，"
            "或改用 whisper.mode: api 云端转录。"
        )
        # 清理可能残留的 json
        try:
            json_path.unlink()
        except Exception:  # noqa: BLE001
            pass
        _time.sleep(2)

    # 全部重试失败
    raise last_err


# 单块采样率（固定 16kHz mono）
_SAMPLE_RATE = 16000

# 不再用固定 _CHUNK_SEC：GPU 默认 2 分钟求速度；CPU 回退时由 _transcribe_local 传入 30 秒小块。


def _transcribe_chunked(model, wav_path: Path, chunk_sec: int = 120) -> List[Segment]:
    """按固定时长用标准库 wave 分块读取 PCM 并逐块转录，拼接时间戳。

    为什么必须按块「流式」读取，而不是 decode_audio 整段解码：
    faster-whisper 的 feature_extractor 会对「喂进来的整段音频」一次性做 STFT；而
    faster_whisper.audio.decode_audio 又先把整段音频解码成一个连续大数组。长视频(30min+)
    下，这两个「整段」操作都需要上百 MB 的【连续】主机内存，空闲内存波动/碎片化时会
    间歇性抛 numpy ArrayMemoryError（表现为「时好时坏、转录后自己结束」）。

    改为：直接用标准库 wave 按块读取 16k mono PCM（每块仅约几 MB），转 float32 后
    逐块喂给 model.transcribe()，每次只处理一小块，峰值内存恒定在几十 MB，无论视频多长都稳定。
    """

    import gc
    import numpy as np
    import wave

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

        segments: List[Segment] = []
        _last_log = _time.time()
        _count = 0
        for ci in range(n_chunks):
            s0 = ci * chunk_frames
            s1 = min(nframes, s0 + chunk_frames)
            offset = s0 / fr  # 该块在整段音频中的起始秒数，用于还原全局时间戳
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
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"faster-whisper 转录过程出错（第 {ci + 1}/{n_chunks} 块）：{exc}\n"
                    "常见原因：① 音频文件损坏 ② 内存/显存异常 ③ 模型与硬件不匹配。\n"
                    "建议：尝试在 config.yaml 设置 whisper.device: cpu 或 whisper.compute_type: int8，\n"
                    "      或改用 whisper.mode: api 云端转录。"
                ) from exc

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
            # 主动 GC，降低长音频多段情况下的内存碎片
            gc.collect()

    return segments


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
