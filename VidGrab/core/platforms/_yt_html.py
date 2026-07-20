"""YouTube 字幕提取（基于 watch 页面 HTML 解析，core/platforms/_yt_html.py）

为什么需要这套独立实现：
yt-dlp 在某些环境（沙箱 / 无法联网下载 challenge solver 脚本）会因为解不开
YouTube 的 n 参数签名挑战，拿不到视频格式，进而在选格式时报
「Requested format is not available」。

但字幕轨道其实就在 watch 页面的 ytInitialPlayerResponse JSON 里，字幕文件也有
独立直链，完全不需要视频格式。所以这里直接：
  1) 一次请求 watch 页面（带代理 + Cookie）→ 解析出标题/UP主/时长 + captionTracks
  2) 用 requests 下载所选语言的 VTT 文件
  3) parse_vtt 解析成 Segment 列表

这样既绕开了 yt-dlp 的 n 挑战，也避免了对视频格式的任何依赖。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..subtitles.parsers import parse_vtt

try:
    import requests
except ImportError:  # requests 未安装时，本模块整体不可用，由上层回退到 yt-dlp
    requests = None  # type: ignore


# 语言优先级：优先中文，其次英文
_LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "en", "en-US", "en-GB"]


def _parse_player_response(html: str) -> dict:
    """从 watch 页面 HTML 里提取 ytInitialPlayerResponse 的 JSON 对象。

    关键修复：早期版本用手写「括号配平」从第一个 '{' 往后扫，遇到字符串里
    的转义序列会提前误判顶层 '}'，导致只截到 responseContext 开头、拿不到
    videoDetails/captionTracks（YouTube 的 A/B 页面字符串转义更频繁，更易触发）。

    现在改用标准库 json.JSONDecoder().raw_decode —— 它是完整正确的 JSON 解析器，
    能正确处理所有转义/嵌套，从 '{' 位置一路解析到对象真正结束。手写配平仅作兜底。
    """
    idx = html.find("ytInitialPlayerResponse")
    if idx < 0:
        return {}
    # 锚定到赋值 '=' 之后的第一个 '{'，避免抓到关键字前面夹带的其它 '{...}'
    eq = html.find("=", idx)
    if eq < 0:
        return {}
    start = html.find("{", eq)
    if start < 0:
        return {}

    # 主路径：标准库正确解析
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj
    except json.JSONDecodeError:
        pass

    # 兜底：手写括号配平（处理极端降级页）
    depth = 0
    end = None
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end is None:
        return {}
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return {}


def _http_get(url: str, proxy: str, cookies: Dict[str, str], headers: Dict[str, str], timeout: int) -> str:
    """统一的 GET 请求：优先用 curl_cffi 模拟 Chrome 的 TLS 握手，绕过 YouTube 对
    Python requests 非浏览器指纹的「确认你不是机器人」校验；未安装 curl_cffi 时
    回退到 requests（此时在严苛网络下可能仍被 bot 校验挡住）。
    """
    try:
        from curl_cffi import requests as cffi
    except Exception:  # noqa: BLE001
        cffi = None
    if cffi is not None:
        try:
            resp = cffi.get(
                url,
                proxy=proxy or None,
                cookies=cookies or None,
                impersonate="chrome",
                headers=headers,
                timeout=timeout,
            )
            return resp.text
        except Exception:  # noqa: BLE001
            pass
    # 回退 requests
    if requests is None:
        return ""
    s = requests.Session()
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    s.headers.update(headers)
    if cookies:
        s.cookies.update(cookies)
    return s.get(url, timeout=timeout).text


def fetch_player_response(url: str, proxy: str, cookies: Dict[str, str]) -> dict:
    """请求 watch 页面并返回解析后的 player response（含元数据与字幕轨道）。"""
    # 用完整、像真实 Chrome 的浏览器头，降低被 YouTube 判为「非浏览器/机器人」的概率
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    html = _http_get(url, proxy, cookies, headers, 40)
    return _parse_player_response(html)


def _extract_meta(player: dict) -> Tuple[str, str, int, str]:
    """从 player response 取 (标题, UP主, 时长秒, 发布时间文本)。"""
    title = author = ""
    duration = 0
    publish = ""
    vd = player.get("videoDetails") or {}
    title = vd.get("title", "") or ""
    author = vd.get("author", "") or ""
    try:
        duration = int(vd.get("lengthSeconds", 0) or 0)
    except (TypeError, ValueError):
        duration = 0
    mf = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not title:
        title = mf.get("title", {}).get("simpleText", "") or ""
    if not author:
        author = mf.get("ownerChannelName", "") or ""
    publish = mf.get("publishDate", "") or ""
    return title, author, duration, publish


def _download_subtitle(vtt_url: str, proxy: str, cookies: Dict[str, str], referer: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    }
    return _http_get(vtt_url, proxy, cookies, headers, 30)


def extract_youtube_html(
    url: str,
    workdir: Optional[Path],
    video_id: str,
    proxy: str,
    cookies: Dict[str, str],
) -> Tuple[str, str, int, str, List]:
    """解析 watch 页面拿字幕轨道并下载 VTT。

    返回 (title, author, duration, publish, segments)。
    - 页面无任何字幕轨道 → segments 为空列表（视频本身无字幕）。
    - 有轨道但下载内容为空（常见于经代理时 YouTube timedtext 对来源 IP 的校验）
      → 抛 RuntimeError，由上层回退或提示。
    """
    player = fetch_player_response(url, proxy, cookies)
    # 守卫：若解析不到 videoDetails，说明拿到的是 YouTube 限流/降级页（或 Cookie/代理失效），
    # 抛异常让上层回退 yt-dlp，而不是误报「视频没有字幕」。
    if not player.get("videoDetails"):
        pb = player.get("playabilityStatus") or {}
        status = pb.get("status")
        reason = pb.get("reason") or ""
        if status == "LOGIN_REQUIRED" or "机器人" in reason or "bot" in (reason or "").lower():
            raise RuntimeError(
                "YouTube 返回「确认你不是机器人」校验（playabilityStatus=%s，原因：%s）。"
                "通常是当前网络出口 IP 被 YouTube 标记。请换一个更干净的代理节点，"
                "或在能直连 YouTube 的本机运行；cookie 已就绪无需重导。"
                % (status, reason)
            )
        raise RuntimeError(
            "watch 页面未返回 videoDetails（很可能是 YouTube 限流/降级页，"
            "或 Cookie/代理失效）。将尝试其它方式获取。"
        )
    title, author, duration, publish = _extract_meta(player)
    tracks = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])

    if not tracks:
        return title, author, duration, publish, []

    # 选语言
    target = None
    for lang in _LANG_PRIORITY:
        for t in tracks:
            if t.get("languageCode") == lang:
                target = t
                break
        if target:
            break
    if not target:
        target = tracks[0]

    vtt_url = target["baseUrl"]
    if "fmt=" not in vtt_url:
        vtt_url = vtt_url + ("&" if "?" in vtt_url else "?") + "fmt=vtt"
    referer = "https://www.youtube.com/watch?v=" + video_id
    content = _download_subtitle(vtt_url, proxy, cookies, referer)
    if not content.strip():
        raise RuntimeError(
            "已找到 %d 条字幕轨道，但下载字幕内容失败（很可能是当前网络/代理环境限制；"
            "YouTube 的 timedtext 接口会校验来源 IP，请在能直连 YouTube 的本机运行）。" % len(tracks)
        )

    workdir = workdir or Path(tempfile.gettempdir()) / "vidgrab"
    workdir.mkdir(parents=True, exist_ok=True)
    lang_code = target.get("languageCode", "sub")
    out = workdir / f"{video_id}.{lang_code}.vtt"
    out.write_text(content, encoding="utf-8")
    segments = parse_vtt(out)
    return title, author, duration, publish, segments
