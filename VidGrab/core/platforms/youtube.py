"""YouTube 提取模块（core/platforms/youtube.py）

字幕提取优先用「watch 页面 HTML 解析」（core/platforms/_yt_html），绕开 yt-dlp
的 n 签名挑战；当 HTML 解析拿不到字幕时，回退到 yt-dlp。
- 国内访问 YouTube 需要代理：通过 proxy 参数（或从 config 读）；
- YouTube「确认你不是机器人」校验需要 Cookie：通过 cookies_file（Netscape cookies.txt，最稳）
  或 cookies_from_browser（浏览器名）传入；
- 拿不到字幕时抛出带「代理 / Cookie」引导的 ValueError。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from .. import Platform, Transcript
from ..auth import load_cookies_file
from ..guide import youtube_proxy_guide
from ._ytdlp import _ydl_extract_subtitles
from ._yt_html import extract_youtube_html

_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})")


def _extract_yt_id(url: str) -> str:
    """从链接提取 YouTube 视频 ID（11 位）。"""

    m = _YT_ID_RE.search(url)
    if not m:
        raise ValueError(f"无法从链接中提取 YouTube 视频 ID：{url}")
    return m.group(1)


def extract_youtube(
    url: str,
    workdir: Optional[Path] = None,
    download_audio: bool = False,
    proxy: str = "",
    cookies_from_browser: str = "",
    cookies_file: str = "",
) -> Transcript:
    """提取一个 YouTube 视频：返回带字幕的 Transcript。

    proxy：代理地址；国内访问 YouTube 通常需要。
    cookies_from_browser：浏览器名（chrome/edge/firefox…），让 yt-dlp 直接读该浏览器 Cookie。
    cookies_file：Netscape 格式 cookies.txt 路径（最稳，不受 DPAPI/锁库影响）。
    """

    workdir = workdir or Path(tempfile.gettempdir()) / "vidgrab"
    workdir.mkdir(parents=True, exist_ok=True)
    video_id = _extract_yt_id(url)
    cookies = load_cookies_file(cookies_file) if cookies_file else {}

    # ---- 主路径：watch 页面 HTML 解析（绕开 yt-dlp 的 n 挑战）----
    try:
        title, author, duration, publish, segments = extract_youtube_html(
            url, workdir, video_id, proxy, cookies
        )
        if segments:
            return _build(video_id, title, author, publish, duration, segments)
        # segments 为空：要么视频本身无字幕（继续下面处理），要么下载被环境限制（会抛 RuntimeError 进 except）
    except RuntimeError as exc:
        exc_msg = str(exc)
        is_bot = ("机器人" in exc_msg or "LOGIN_REQUIRED" in exc_msg or "bot" in exc_msg.lower())
        if is_bot:
            # 机器人校验是出口 IP 被标记，不是代理没配/cookie 失效，给准确提示，别误导去配代理
            raise ValueError(
                f"YouTube 机器人校验未通过，无法获取字幕：{exc}\n\n"
                "这是当前网络出口 IP 被 YouTube 标记导致的，不是代理没配、也不是 cookie 失效。\n"
                "解法：换一个更干净的代理节点（尽量用住宅/未被标记的 IP），"
                "或在能直连 YouTube 的本机运行；cookie 已就绪，无需重导。"
            ) from exc
        # 其它 RuntimeError（有轨道但下载被环境限制）：先尝试 yt-dlp 兜底下载字幕
        segs = _try_ydlp_subtitles(url, workdir, video_id, proxy, cookies_from_browser, cookies_file)
        if segs:
            info = {}
            return _build(video_id, title or info.get("title", ""), author or info.get("uploader", ""),
                         publish, duration or float(info.get("duration", 0) or 0), segs)
        # yt-dlp 也失败：把环境限制原因告诉用户
        raise ValueError(
            f"YouTube 字幕下载失败：{exc}\n\n" + youtube_proxy_guide()
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # 其他网络/代理类错误：进入 yt-dlp 兜底
        segs = _try_ydlp_subtitles(url, workdir, video_id, proxy, cookies_from_browser, cookies_file)
        if segs:
            return _build(video_id, "", "", "", 0, segs)

    # ---- 到这里：HTML 解析未拿到字幕 ----
    # 情况 A：视频本身无字幕，但用户要音频转录
    if download_audio:
        audio_path = _download_youtube_audio(
            url, workdir, proxy=proxy, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file
        )
        return Transcript(
            platform=Platform.YOUTUBE,
            video_id=video_id,
            title="",
            author="",
            publish_time="",
            duration=0,
            segments=[],
            source="audio",
            audio_path=str(audio_path),
        )

    # 情况 B：视频无字幕，也没开转录
    raise ValueError(
        "这个 YouTube 视频没有可用的字幕轨道（页面里 captionTracks 为空）。\n"
        "可能原因：\n"
        "  1) 该视频确实没有字幕（你测试的 4gciWspBVHw 就是这种情况）；\n"
        "  2) 需要登录 Cookie 才能看到字幕——请在已登录 YouTube 的页面用\n"
        "     「Get cookies.txt LOCALLY」扩展导出 cookies.txt 并粘贴给本工具。\n\n"
        "若要转录无字幕视频，请开启 download_audio=True（需 ffmpeg + Whisper）。"
    )


def _try_ydlp_subtitles(url, workdir, video_id, proxy, cookies_from_browser, cookies_file):
    """yt-dlp 兜底：尝试下载字幕。失败返回空列表（不抛错）。"""
    try:
        _, segs = _ydl_extract_subtitles(
            url, workdir, f"yt_{video_id}",
            proxy=proxy, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file,
        )
        return segs
    except Exception:  # noqa: BLE001
        return []


def _build(video_id, title, author, publish, duration, segments) -> Transcript:
    return Transcript(
        platform=Platform.YOUTUBE,
        video_id=video_id,
        title=title or "",
        author=author or "",
        publish_time=publish or "",
        duration=float(duration or 0),
        segments=segments,
        source="subtitle",
    )


def _download_youtube_audio(url: str, workdir: Path, proxy: str = "", cookies_from_browser: str = "", cookies_file: str = "") -> Path:
    """用 yt-dlp 下载音频（bestaudio）到 workdir，返回路径。需要 ffmpeg。"""

    from yt_dlp import YoutubeDL

    opts = {
        "format": "bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "outtmpl": str(workdir / "yt_audio_%(id)s.%(ext)s"),
        "writesubtitles": False,
        "writeautomaticsub": False,
        "quiet": True,
        "no_warnings": True,
    }
    if proxy:
        opts["proxy"] = proxy
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    audios = list(workdir.glob("yt_audio_*.mp3"))
    if not audios:
        raise RuntimeError("音频下载失败，请检查 ffmpeg 是否安装。")
    return audios[0]
