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

import os
import shutil
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


def _transcribe_local(audio_path: Path, model_size: str, device: str = "auto", compute_type: str = "auto") -> List[Segment]:
    """本地 faster-whisper 推理。默认自动检测 GPU（优先 GPU），无 GPU 回退 CPU 并提醒。

    device: auto（自动检测）| cpu | cuda
    compute_type: auto（GPU 默认 int8_float16，CPU 默认 int8）| float16 | int8 | int8_float16
    """

    from faster_whisper import WhisperModel
    import ctranslate2

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

    if local_dir.exists():
        print(f"   📂 使用本地模型：{local_dir}")
    else:
        print(f"   🌐 首次运行需从 HuggingFace 下载 faster-whisper-{model_size} 模型，请保持网络畅通...")

    print("   ⏳ 正在加载 faster-whisper 模型...")
    try:
        model = WhisperModel(model_path, device=use_device, compute_type=use_compute_type)
    except Exception as exc:  # noqa: BLE001
        # GPU 加载失败（OOM、CUDA 版本不匹配、驱动问题等）时，尝试 CPU 兜底
        if use_device == "cuda":
            print(f"   ⚠️ GPU 加载失败：{exc}")
            print("   🔄 自动回退到 CPU 转录（可在 config.yaml 将 whisper.device 设为 cpu 永久使用）...")
            use_device, use_compute_type = "cpu", "int8"
            model = WhisperModel(model_path, device=use_device, compute_type=use_compute_type)
        else:
            raise RuntimeError(
                f"加载 faster-whisper 模型失败：{exc}\n"
                "常见原因：① 模型文件损坏/缺失 ② 网络问题导致下载失败 ③ 内存/显存不足。\n"
                "建议：检查网络、确认模型目录完整，或改用云端模式（whisper.mode: api）。"
            ) from exc

    # 预先把音频转成 16kHz mono WAV，避免 faster-whisper 内部读取/重采样时触发 GPU 端崩溃
    wav_path = _ensure_wav_16k_mono(audio_path)

    print("   🎙️ 开始转录，请稍候（无字幕视频需整段本地推理，期间会显示进度）...")
    try:
        segments_gen, _info = model.transcribe(str(wav_path), beam_size=5, word_timestamps=False)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"faster-whisper 转录过程出错：{exc}\n"
            "常见原因：① 音频文件损坏 ② GPU 显存不足/驱动异常 ③ 模型与硬件不匹配。\n"
            "建议：尝试在 config.yaml 设置 whisper.device: cpu 或 whisper.compute_type: int8，\n"
            "      或改用 whisper.mode: api 云端转录。"
        ) from exc

    segments: List[Segment] = []
    _duration = getattr(_info, "duration", None)
    _last_log = _time.time()
    _count = 0
    for seg in segments_gen:
        text = (seg.text or "").strip()
        if text:
            segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
        _count += 1
        # 周期性打印进度，避免长视频转录时「静默假死」让用户误以为卡住
        _now = _time.time()
        if _now - _last_log >= 15:
            _last_log = _now
            if _duration:
                _pct = int((seg.end or 0) / _duration * 100) if seg.end else 0
                _pct = max(0, min(100, _pct))
                print(f"   ⏳ 转录进度：{_fmt_ts(seg.end)} / {_fmt_ts(_duration)}（{_pct}%，已 {_count} 段）")
            else:
                print(f"   ⏳ 转录中… 已 {_count} 段")
    print(f"   ✅ 转录完成：{len(segments)} 段文字")
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
