"""B站提取模块（core/platforms/bilibili.py）

用 bilibili_api 取视频元数据（标题 / UP主 / 发布时间 / 时长）与字幕。
- 真字幕(CC) 现在需要登录：提供 SESSDATA cookie 才能取到；
- 没提供 SESSDATA 时回退 yt-dlp，但只能拿到弹幕、拿不到真字幕；
- 无字幕视频可设 download_audio=True 下载音频（交给转录模块）。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import List, Optional

from .. import Platform, Segment, Transcript
from ..lang import _detect_language
from ..subtitles.parsers import parse_bilibili_json, _ts_to_date
from ..guide import bilibili_login_guide
from ._ytdlp import _ydl_extract_subtitles, _ydl_download_audio

_BVID_RE = re.compile(r"(BV[0-9A-Za-z]+)")


def _check_bilibili_api_installed() -> None:
    """检查 bilibili_api 是否安装；未安装时抛出带引导的 ModuleNotFoundError。"""
    try:
        import bilibili_api  # noqa: F401
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "\n❌ 缺少依赖包 bilibili_api（B站提取需要）。\n"
            "   安装命令（在你的 Python 环境里执行）：\n"
            "     pip install bilibili-api-python\n"
            "   或一次性装齐所有依赖：\n"
            "     pip install -r requirements.txt\n"
            "   （项目根目录下有 requirements.txt，列出了所有依赖）"
        ) from None


def _extract_bvid(url: str) -> str:
    """从链接里提取 BV 号，例如 BV1xx...。"""

    m = _BVID_RE.search(url)
    if not m:
        raise ValueError(f"无法从链接中提取 BV 号：{url}")
    return m.group(1)


def get_bilibili_pages(url: str, sessdata: str = "") -> dict:
    """查询 B站视频的分P信息。返回 {title, author, duration, pages: [{index, cid, part, duration}]}。

    pages 长度=1 表示单P视频；>1 表示合集（多P），需要让用户选哪一集。
    """

    _check_bilibili_api_installed()
    from bilibili_api import Credential, video, sync

    bvid = _extract_bvid(url)
    cred = Credential(sessdata=sessdata) if sessdata else None
    v = video.Video(bvid=bvid, credential=cred)
    info = sync(v.get_info())

    raw_pages = info.get("pages") or []
    pages = []
    for i, p in enumerate(raw_pages):
        pages.append({
            "index": i,  # 0-based
            "cid": p.get("cid"),
            "part": p.get("part", f"P{i+1}"),
            "duration": p.get("duration", 0),
        })

    return {
        "title": info.get("title", ""),
        "author": info.get("owner", {}).get("name", ""),
        "duration": float(info.get("duration", 0) or 0),
        "pages": pages,
    }


def extract_bilibili(
    url: str,
    workdir: Optional[Path] = None,
    download_audio: bool = False,
    sessdata: str = "",
    force_audio: bool = False,
    page_index: int = 0,
) -> Transcript:
    """提取一个 B站视频：返回带字幕的 Transcript。

    sessdata：B站登录 cookie 的 SESSDATA；提供后可取真字幕（CC）。
    拿不到真字幕时，抛出带「登录引导」的 ValueError，方便低技术用户照做。
    force_audio：强制走「音频转录」路径（忽略任何字幕，直接下载音频交给转录模块）。
        用途：① 测试无字幕流程；② 字幕质量太差（如 AI 字幕错乱）时改用音频重转。
    page_index：合集视频的选集（0-based），默认 0（第一集/单P视频）。
    """

    _check_bilibili_api_installed()
    from bilibili_api import Credential, video, sync

    bvid = _extract_bvid(url)
    cred = Credential(sessdata=sessdata) if sessdata else None
    v = video.Video(bvid=bvid, credential=cred)
    info = sync(v.get_info())
    title = info.get("title", "")
    author = info.get("owner", {}).get("name", "")
    publish_time = _ts_to_date(info.get("pubdate"))
    # 合集处理：用选中页的 cid 和 part 名称
    raw_pages = info.get("pages") or []
    # 部分合集在 get_info().pages 里只返回 1 条，需用 get_pages() 复核真实分P数
    if len(raw_pages) <= 1:
        try:
            _pp = sync(v.get_pages()) or []
            if len(_pp) > 1:
                raw_pages = _pp
        except Exception:
            pass
    is_collection = len(raw_pages) > 1
    if raw_pages and len(raw_pages) > 1:
        page_info = raw_pages[page_index]
        cid = page_info.get("cid")
        part_name = page_info.get("part", "")
        duration = float(page_info.get("duration", 0) or 0)
        # 标题加上分P名称，便于区分
        if part_name:
            title = f"{title} - {part_name}"
        # 多P：标题前缀加「P{n} · 」，让导出文档的标题也明确体现是第几集
        title = f"P{page_index + 1} · {title}"
        # 合集的音频下载用带 p 参数的 URL
        audio_url = f"{url.split('?')[0]}?p={page_index + 1}"
    else:
        cid = info.get("cid")
        if not cid:
            try:
                pages = sync(v.get_pages())
                cid = (pages[0] or {}).get("cid") if pages else None
            except Exception:
                cid = None
        duration = float(info.get("duration", 0) or 0)
        audio_url = url

    # 0) 强制音频转录模式：跳过任何字幕，直接下载音频交给转录模块
    if force_audio:
        print("[bilibili] 强制音频转录模式：忽略字幕，直接下载音频用于转录...")
        audio_path = _ydl_download_audio(audio_url, workdir, bvid)
        return Transcript(
            platform=Platform.BILIBILI,
            video_id=bvid,
            title=title,
            author=author,
            publish_time=publish_time,
            duration=duration,
            segments=[],
            source="audio",
            audio_path=str(audio_path),
            page_index=page_index,
            is_collection=is_collection,
            language=_detect_language(segments),
        )

    # 1) 优先用 bilibili_api 取真字幕（需要登录 cookie）
    #    ⚠️ cid 已在上面按「合集选中页 / 单P」算好；这里【不可】再覆盖成整视频的 cid，
    #    否则合集会取到第 1 集字幕（用户选了 P92 却得到 P1 内容）。仅当 cid 缺失时补取。
    segments: List[Segment] = []
    try:
        if not cid:
            try:
                pages = sync(v.get_pages())
                cid = (pages[0] or {}).get("cid") if pages else None
            except Exception:
                cid = None
        if cid:
            sub_info = sync(v.get_subtitle(cid=cid)) or {}
            segments = _fetch_bilibili_subtitle(sub_info, workdir)
    except Exception as exc:  # noqa: BLE001
        print(f"[bilibili] bilibili_api 取字幕失败（{exc}），回退 yt-dlp")

    # 2) 回退：用 yt-dlp 取（免登录，但只能拿弹幕，拿不到真字幕）
    if not segments:
        # 用带 ?p= 的 audio_url，确保合集取到选中页的字幕（而非第 1 集）
        segments = _extract_bilibili_subtitle_via_ytdlp(audio_url, workdir)

    if segments:
        return Transcript(
            platform=Platform.BILIBILI,
            video_id=bvid,
            title=title,
            author=author,
            publish_time=publish_time,
            duration=duration,
            segments=segments,
            source="subtitle",
            page_index=page_index,
            is_collection=is_collection,
            language=_detect_language(segments),
        )

    if download_audio:
        # 无字幕：下载音频，交给转录模块（OpenAI Whisper）转成文字
        print("[bilibili] 未取到字幕，下载音频用于转录...")
        audio_path = _ydl_download_audio(audio_url, workdir, bvid)
        return Transcript(
            platform=Platform.BILIBILI,
            video_id=bvid,
            title=title,
            author=author,
            publish_time=publish_time,
            duration=duration,
            segments=[],
            source="audio",
            audio_path=str(audio_path),
            page_index=page_index,
            is_collection=is_collection,
            language=_detect_language(segments),
        )

    # 3) 两种都取不到真字幕：给出「登录引导」而不是干巴巴的报错
    hint = "" if sessdata else "\n\n" + bilibili_login_guide()
    raise ValueError(
        "这个 B站视频没有取到真字幕（只有弹幕，弹幕不能当字幕用）。"
        "如果需要字幕，请登录 B站并提供 SESSDATA cookie。"
        + hint
    )


def _fetch_bilibili_subtitle(sub_info: dict, workdir: Optional[Path]) -> List[Segment]:
    """从 bilibili_api 返回的 subtitle 结构里，下载并解析首选（优先中文）字幕。"""

    import requests

    subtitles = sub_info.get("subtitles") or sub_info.get("regular_subtitles") or []
    if not subtitles:
        return []
    # 优先中文，其次取第一个
    chosen = next((s for s in subtitles if "zh" in (s.get("lan", "") or "").lower()), subtitles[0])
    url = chosen.get("subtitle_url") or chosen.get("url")
    if not url:
        return []
    if url.startswith("//"):
        url = "https:" + url

    workdir = workdir or Path(tempfile.gettempdir()) / "vidgrab"
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"bili_sub_{chosen.get('lan', 'zh')}.json"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return parse_bilibili_json(path)


def _extract_bilibili_subtitle_via_ytdlp(url: str, workdir: Optional[Path]) -> List[Segment]:
    """yt-dlp 回退：免登录取 B站字幕。注意通常只能拿到弹幕，拿不到真字幕。"""

    bvid = _extract_bvid(url)
    try:
        _, segments = _ydl_extract_subtitles(url, workdir, f"bili_{bvid}")
        return segments
    except Exception as exc:  # noqa: BLE001
        print(f"[bilibili] yt-dlp 回退取字幕也失败：{exc}")
        return []
