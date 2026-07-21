"""视频链接解析统一入口（core/extractor.py）

职责很薄：① 识别平台 ② 把链接分发给对应平台的提取器。
具体逻辑在 core/platforms/ 下，字幕解析在 core/subtitles/ 下。

对外主要用：
  detect_platform(url)                             -> Platform
  extract(url, workdir, download_audio, sessdata, proxy) -> Transcript
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from . import Platform, Transcript
from .platforms import extract_bilibili, extract_youtube


# 平台识别正则（按优先级从上往下匹配）
_BILIBILI_RE = re.compile(r"(bilibili\.com|B23\.tv)", re.I)
_YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)", re.I)
_DOUYIN_RE = re.compile(r"(douyin\.com|tiktok\.com)", re.I)
_KUAISHOU_RE = re.compile(r"(kuaishou\.com|gifshow\.com)", re.I)
_TWITTER_RE = re.compile(r"(twitter\.com|x\.com)", re.I)


def detect_platform(url: str) -> Platform:
    """根据链接判断是哪个平台，返回 Platform 枚举。"""

    if _BILIBILI_RE.search(url):
        return Platform.BILIBILI
    if _YOUTUBE_RE.search(url):
        return Platform.YOUTUBE
    if _DOUYIN_RE.search(url):
        return Platform.DOUYIN
    if _KUAISHOU_RE.search(url):
        return Platform.KUAISHOU
    if _TWITTER_RE.search(url):
        return Platform.TWITTER
    return Platform.UNKNOWN


def extract(
    url: str,
    workdir: Optional[Path] = None,
    download_audio: bool = False,
    sessdata: str = "",
    proxy: str = "",
    cookies_from_browser: str = "",
    cookies_file: str = "",
    force_audio: bool = False,
    page_index: int = 0,
    lang_source: str = "original",
) -> Transcript:
    """统一提取入口：识别平台后分发给对应提取器。

    sessdata：B站登录 cookie（可选，取真字幕需要）
    proxy：    代理地址（可选，访问 YouTube 需要）
    cookies_from_browser：浏览器名（chrome/edge/firefox…），让 yt-dlp 直接读该浏览器 Cookie
    cookies_file：Netscape 格式 cookies.txt 路径（最稳，不受 DPAPI/锁库影响）
    force_audio：强制走音频转录路径（忽略字幕，直接下音频），用于测试无字幕流程 / 覆盖劣质字幕
    page_index：合集视频的选集（0-based），默认 0（第一集/单P视频）
    遇到平台限制时，对应提取器会抛出带「用户引导」的报错，而不是干巴巴的堆栈。
    """

    # 代理：设置到环境变量，yt-dlp 和 requests 都会自动读取
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy

    # workdir 兼容字符串传入：下游会对它调 .mkdir()，必须是 Path 对象
    if isinstance(workdir, str):
        workdir = Path(workdir)

    platform = detect_platform(url)
    if platform == Platform.BILIBILI:
        return extract_bilibili(
            url, workdir=workdir, download_audio=download_audio,
            sessdata=sessdata, force_audio=force_audio, page_index=page_index,
            lang_source=lang_source,
        )
    if platform == Platform.YOUTUBE:
        return extract_youtube(
            url,
            workdir=workdir,
            download_audio=download_audio,
            proxy=proxy,
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
        )
    raise ValueError(f"暂不支持的平台：{url}（当前 Phase 0 仅支持 B站 / YouTube）")
