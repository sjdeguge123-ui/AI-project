"""yt-dlp 公共工具（core/platforms/_ytdlp.py）

B站和 YouTube 都靠 yt-dlp 兜底 / 提取字幕，把公共逻辑放这里，避免重复。

关键点：必须用 extract_info(download=True) + skip_download=True，
字幕文件才会真正落盘（extract_info(download=False) 不会写盘）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple, Union

from ..subtitles.parsers import parse_vtt, parse_srt


def _ydl_extract_subtitles(
    url: str,
    workdir: Optional[Path],
    video_id: str,
    opts_extra: Optional[dict] = None,
    proxy: str = "",
    cookies_from_browser: str = "",
    cookies_file: str = "",
    referer: str = "https://www.bilibili.com",
) -> Tuple[dict, List]:
    """用 yt-dlp 取视频信息并把字幕落盘，返回 (info_dict, 字幕 Segment 列表)。

    - skip_download=True：只下字幕、不下视频本体
    - extract_info(download=True)：触发字幕后处理，文件才会写盘
    - proxy：代理地址（如 http://127.0.0.1:7890），用于访问 YouTube 等被墙站点
    - cookies_from_browser：浏览器名（chrome/edge/firefox…），让 yt-dlp 直接读该浏览器 Cookie
    - cookies_file：Netscape 格式 cookies.txt 路径（最稳，不受 DPAPI/锁库影响）
    - referer：某些站点（如 B站）需要带 Referer 才能下载字幕
    """

    from yt_dlp import YoutubeDL

    workdir = workdir or Path(tempfile.gettempdir()) / "vidgrab"
    workdir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en", "all"],
        "subtitlesformat": "vtt/srt",
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(workdir / video_id),
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": referer,
        },
    }
    if proxy:
        ydl_opts["proxy"] = proxy
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    if opts_extra:
        ydl_opts.update(opts_extra)

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    segments = _collect_subtitle_files(workdir, video_id)
    return info, segments


def _collect_subtitle_files(workdir: Path, video_id: str) -> List:
    """收集 yt-dlp 落地的字幕文件，优先中文，解析为 Segment。"""

    files = list(workdir.glob(f"{video_id}*.vtt")) + list(workdir.glob(f"{video_id}*.srt"))
    if not files:
        return []
    zh = [f for f in files if "zh" in f.name.lower()]
    pick = (zh or files)[0]
    return parse_vtt(pick) if pick.suffix == ".vtt" else parse_srt(pick)


def _resolve_ffmpeg_location() -> Optional[str]:
    """解析可用的 ffmpeg 路径：优先系统 PATH，缺失则用 imageio-ffmpeg 自带二进制（按需安装）。

    这样无字幕/音频路径在用户没装系统 ffmpeg 时也能自动跑通，无需手动安装。
    返回 None 表示用系统 PATH 里的 ffmpeg 即可（不额外指定）；否则返回二进制路径给 yt-dlp 的 ffmpeg_location。
    """
    if shutil.which("ffmpeg"):
        return None
    # 系统没有 → 尝试 imageio-ffmpeg（自带 ffmpeg 二进制）
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001
        pass
    # 仍未装 → 按需 pip 安装（仅真正走音频路径的用户才会触发，不污染只看字幕的用户）
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "imageio-ffmpeg"],
            check=False, capture_output=True, text=True,
        )
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001
        pass
    return None


def _ydl_download_audio(
    url: str,
    workdir: Optional[Path],
    video_id: str,
    proxy: str = "",
    cookies_from_browser: str = "",
    cookies_file: str = "",
    referer: str = "https://www.bilibili.com",
) -> Path:
    """用 yt-dlp 下载音频（bestaudio）并抽取为 mp3，返回音频文件路径。

    依赖系统已装 ffmpeg（FFmpegExtractAudio 后处理需要）。B站一般不需代理；
    若需要，可经 proxy / cookies_from_browser / cookies_file 传入（未传则用环境变量）。
    """

    from yt_dlp import YoutubeDL

    workdir = workdir or Path(tempfile.gettempdir()) / "vidgrab"
    workdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(workdir / f"{video_id}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        # 网络健壮性：重试 + 超时，避免偶发 TLS/连接中断直接失败
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        # 某些受限/代理网络对 B站音频 CDN 有 TLS 拦截，关闭证书校验可提高连通性
        # （仅用于媒体下载，不涉账号凭证，风险可控）
        "nocheckcertificate": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
        },
    }
    if proxy:
        ydl_opts["proxy"] = proxy
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    # ffmpeg 位置：系统没有就自动用 imageio-ffmpeg 自带的二进制（零配置跑通无字幕/音频路径）
    ffmpeg_location = ydl_opts.get("ffmpeg_location") or _resolve_ffmpeg_location()
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # ffmpeg 缺失是最常见根因，给出可操作的提示
        if "ffmpeg" in msg.lower() or "postprocessor" in msg.lower():
            raise RuntimeError(
                "音频下载失败：未检测到 ffmpeg（yt-dlp 抽取音频必需）。\n"
                "请先安装 ffmpeg 并加入系统 PATH：https://ffmpeg.org/download.html\n"
                f"原始错误：{msg}"
            ) from exc
        # 网络 / TLS 类错误：明确告知是与 B站音频 CDN 的连接问题，给出可执行建议
        if any(k in msg for k in ("SSL", "EOF", "Connection", "Timed", "Network", "reset")):
            raise RuntimeError(
                "音频下载失败：与 B站音频 CDN 的网络/TLS 连接中断。\n"
                "常见原因与对策：\n"
                "  1) 处于受限/代理网络：在 config.yaml 的 proxy 段配置 HTTP 代理后重试；\n"
                "  2) 某些网络对 B站音频 CDN 有 TLS 拦截：尝试关闭代理或更换网络；\n"
                "  3) 偶发抖动：已内置 10 次重试，可稍后重试。\n"
                "（中间音频文件已保留在临时目录，便于排查；成功运行后会自动清理）\n"
                f"原始错误：{msg}"
            ) from exc
        raise

    # 找落地文件（优先 .mp3，其次任意非临时文件）
    candidates = list(workdir.glob(f"{video_id}*.mp3"))
    if not candidates:
        candidates = [
            c
            for c in workdir.glob(f"{video_id}*")
            if c.suffix not in (".part", ".ytdl")
        ]
    if not candidates:
        raise FileNotFoundError(f"音频下载后未找到文件：{workdir / video_id}*")
    return candidates[0]
