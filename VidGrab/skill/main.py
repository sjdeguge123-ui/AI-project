# CodeBuddy Skill 入口（skill/main.py）
# 运行方式：
#   交互式：python -m skill            （会提示输入链接）
#   带参  ：python -m skill "<视频链接>" [--audio] [--page=N] [--formats=md,html,docx,pdf,image]
#                                      [--mode=concise|detailed|query] [--keywords="关键词或一段话"]
#          agent / 非交互调用示例（不阻塞、不弹交互提示）：
#            python -m skill "https://www.bilibili.com/video/BVxxx" --page=92 --formats=markdown
#
# 完整流程（B站）：
#   识别平台 → 配置自举（config.yaml 缺失则从模板生成）
#   → 交互式获取登录凭证（Get cookies.txt 扩展导出粘贴，自动抽 SESSDATA）
#   → 首次运行引导配置 AI 服务商与 Key（每个人用自己的 Key，开发者不介入）
#   → 提取字幕/音频 → 无字幕则本地 faster-whisper 转录（免 OpenAI Key）
#   → AI（硅基流动/DeepSeek/OpenAI/本地 Ollama，可配置）生成结构化摘要
#   → 导出多格式文档（Markdown/HTML/Word/PDF/图片）→ 推送微信进度
#
# 多用户：项目只提交 config.example.yaml（无 Key 模板）。任何人 git clone 后直接运行，
#         工具自动生成自己的 config.yaml 并引导填入自己的 Key / Cookie，全程开发者不介入。
#
# YouTube：当前仅预留接口（bot 校验不稳定），待 B站跑通后扩展。

from __future__ import annotations

import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

# 让 skill 目录也能 import core（无论从哪运行都能找到）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------------
# 依赖自举：运行前自动装好缺失的第三方包，用户无需手动 pip
# ----------------------------------------------------------------------------
# 导入名 -> pip 包名（覆盖 B站全流程 + 四种导出格式所需）
_ESSENTIAL_DEPS = [
    ("yaml", "PyYAML"),
    ("yt_dlp", "yt-dlp"),
    ("bilibili_api", "bilibili-api-python"),
    ("openai", "openai"),
    ("faster_whisper", "faster-whisper"),
    ("requests", "requests"),
    ("curl_cffi", "curl_cffi"),
    ("docx", "python-docx"),
    ("markdown", "markdown"),
    ("PIL", "Pillow"),         # 图片（PNG）摘要导出
    ("xhtml2pdf", "xhtml2pdf"),  # PDF 导出（纯 Python，无系统依赖，跨平台开箱即用）
    ("imageio_ffmpeg", "imageio-ffmpeg"),  # 内置 ffmpeg 二进制，无字幕流程无需用户手动安装系统 ffmpeg
]
# 可选依赖（不自动安装）：WeasyPrint CSS 保真更好，但依赖系统库（Windows 需 GTK），
# 装不全会 import 失败。默认 PDF 引擎已改用纯 Python 的 xhtml2pdf，无需 WeasyPrint。
# 高级用户若已配好 GTK，exporter 会自动优先用 WeasyPrint 提升排版质量。
_OPTIONAL_DEPS = {
    "weasyprint": "WeasyPrint",  # 可选：更高保真 PDF（需系统库；未装则用 xhtml2pdf）
}


def _ensure_dependencies() -> None:
    """检测并自动安装缺失的第三方依赖，让用户拿到「开箱即用」的版本。

    关键：必须在 `from core import ...` 之前运行——因为 core 模块加载时就需要 yaml / yt_dlp。
    其余第三方包（bilibili_api / openai / docx / markdown 等）在功能函数内是懒加载，
    这里一并预装好，确保 B站全流程 + 四种导出格式都能直接用。
    """
    missing = []
    for mod, _pkg in _ESSENTIAL_DEPS:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append((mod, _pkg))

    if not missing:
        return

    print("🔧 首次运行：检测到以下依赖未安装，正在自动安装（只需一次）...")
    failed: list[str] = []
    for mod, pkg in missing:
        print(f"   📦 安装 {pkg} ...")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # 打印 pip 的错误尾部，便于排查（如网络 / 权限问题）
                tail = (proc.stderr or proc.stdout or "")[-600:]
                print(f"   ⚠️ 自动安装 {pkg} 失败：\n{tail}")
                failed.append(pkg)
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️ 自动安装 {pkg} 失败：{e}")
            failed.append(pkg)

    if not failed:
        print("✅ 依赖已全部就绪。\n")
        return

    # 复检 + 友好兜底
    print("❌ 以下依赖自动安装后仍缺失，请手动安装后重试：")
    print("   pip install " + " ".join(failed))
    print("   或一次性装齐（项目根目录下有 requirements.txt）：")
    print("   pip install -r requirements.txt")
    # 仅当「关键」依赖缺失才中断：PyYAML / yt-dlp 缺失会让整工具起不来
    critical = {"PyYAML", "yt-dlp"}
    if critical & set(failed):
        raise SystemExit(1)


# 在任何 core 导入之前，先把依赖补齐
_ensure_dependencies()

# Windows 默认终端编码常为 GBK，emoji/特殊符号会触发 UnicodeEncodeError 导致进程异常退出。
# 在加载日志 tee 前强制 stdout/stderr 用 UTF-8（无法编码时回退为 ?），避免终端编码问题中断流程。
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from core import config, extractor, auth, transcriber, summarizer, exporter, notify, Platform, format_duration  # noqa: E402
from core.platforms import get_bilibili_pages  # noqa: E402
from core import log  # noqa: E402

log.setup_logging(ROOT)


def _proxy_from_config(cfg) -> str:
    if cfg.proxy and (cfg.proxy.https or cfg.proxy.http):
        return cfg.proxy.https or cfg.proxy.http
    return ""


def _set_proxy_env(proxy: str) -> None:
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy


def _welcome() -> None:
    """新手开场白：一句话说清产出格式，不展示详细流程。"""
    print("=" * 60)
    print("  VidGrab · 把视频链接变成「带时间线的要点笔记」")
    print("=" * 60)
    print("产出：Markdown / HTML / Word / PDF / 图片（含时间线脉络与金句）")
    print("-" * 60)


def _preflight() -> None:
    """环境自检：让用户对依赖状态心里有数，不阻塞。"""
    print("🔧 环境自检：依赖已自动就绪（含内置 ffmpeg），可直接处理有字幕/无字幕视频。\n")


def _select_page(url: str, sessdata: str, forced: int = None) -> int:
    """检测合集/分P，返回选中的 page_index（0-based）。

    - forced 不为 None：直接用 --page 指定的集（校验范围），agent / 非交互首选；
    - 非交互环境（stdin 不是终端）：默认第 1 集，不阻塞；
    - 交互环境：列出分P让用户选。
    """

    try:
        info = get_bilibili_pages(url, sessdata)
    except Exception as e:
        print(f"   （查询分P信息失败：{e}，按单P处理）")
        return 0

    pages = info.get("pages", [])
    if len(pages) <= 1:
        return 0

    if forced is not None:
        if 0 <= forced < len(pages):
            print(f"   已选（--page）：P{forced + 1} {pages[forced]['part']}")
            return forced
        print(f"   ⚠️ --page={forced + 1} 超出范围（共 {len(pages)} 集），按第1集处理")
        return 0

    if not sys.stdin.isatty():
        print(f"   （非交互环境，默认选第1集；可用 --page=N 指定，共 {len(pages)} 集）")
        return 0

    # 合集：列出分P让用户选
    print(f"\n📋 检测到合集视频，共 {len(pages)} 集：")
    for p in pages:
        dur = p.get("duration", 0)
        print(f"   P{p['index']+1}: {p['part']} ({dur//60}分{dur%60}秒)")
    print()
    while True:
        try:
            choice = input(f"请选择要提取哪一集（输入 1-{len(pages)}，回车默认第1集）：").strip()
            if not choice:
                return 0
            idx = int(choice) - 1
            if 0 <= idx < len(pages):
                print(f"   已选择：P{idx+1} {pages[idx]['part']}")
                return idx
            print(f"   ⚠️ 请输入 1-{len(pages)} 之间的数字")
        except (ValueError, EOFError, KeyboardInterrupt):
            return 0


def _select_formats(forced: str = None) -> list:
    """选择输出格式（可多选）。返回格式列表。

    - forced 不为 None：解析 --formats 指定的逗号列表（如 "markdown,html"），不阻塞；
    - 非交互环境：默认 Markdown；
    - 交互环境：列出菜单让用户选。
    """
    fmt_map = {"1": "markdown", "2": "html", "3": "docx", "4": "pdf", "5": "image"}
    valid = ("md", "markdown", "html", "docx", "word", "pdf", "image", "png", "jpg", "jpeg")

    if forced:
        formats = []
        for c in forced.replace("，", ",").split(","):
            c = c.strip().lower()
            if c in fmt_map:
                formats.append(fmt_map[c])
            elif c in valid:
                formats.append(c)
        return formats or ["markdown"]

    if not sys.stdin.isatty():
        return ["markdown"]

    print("\n【步骤 ⑥】选择输出格式")
    print("   支持的格式：")
    print("     1. Markdown (.md)  —— 通用，推荐")
    print("     2. HTML (.html)    —— 可在浏览器打开，样式美观")
    print("     3. Word (.docx)    —— 可在 Word/WPS 编辑")
    print("     4. PDF (.pdf)      —— 适合打印/分享")
    print("     5. 图片 (.png)     —— 信息图，便于分享/预览")
    print("   可多选，用逗号分隔（如 1,3）；回车默认 Markdown")
    try:
        choice = input("请选择：").strip()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if not choice:
        return ["markdown"]

    formats = []
    for c in choice.replace("，", ",").split(","):
        c = c.strip()
        if c in fmt_map:
            formats.append(fmt_map[c])
        elif c.lower() in valid:
            formats.append(c.lower())
    return formats or ["markdown"]


def _select_mode(forced: str = None, keywords: str = "") -> tuple:
    """选择内容输出模式。返回 (mode, keywords)。

    三种模式（用户需求）：
      - concise （默认，模式一）：只提炼核心大重点；
      - detailed（模式二）：大重点 + 次重点，内容脉络更丰满；
      - query   （模式三）：用户自定义关键词/问题，只输出相关重点。

    - forced 不为 None：直接解析 --mode（concise/detailed/query），不阻塞；
    - 非交互环境（stdin 不是终端）：默认 concise；
    - 交互环境：列出三种模式让用户选，选「自定义」则进一步询问关键词。
    """

    valid = {"concise", "detailed", "query"}

    # CLI 直接指定了模式
    if forced:
        mode = (forced or "concise").lower().strip()
        if mode not in valid:
            mode = "concise"
        # query 模式但没给关键词：交互环境则追问，非交互则退化为 detailed
        if mode == "query" and not keywords:
            if not sys.stdin.isatty():
                mode = "detailed"
            else:
                try:
                    kw = input("请输入你想关注的关键词或一段话（回车放弃→退回详细）：").strip()
                except (EOFError, KeyboardInterrupt):
                    kw = ""
                keywords = kw
                if not keywords:
                    mode = "detailed"
        return mode, keywords

    # 非交互环境默认 concise
    if not sys.stdin.isatty():
        return "concise", ""

    print("\n【步骤 ⑤-1】选择内容输出模式")
    print("   1. 精简（默认）：只提炼核心大重点，内容脉络简洁")
    print("   2. 详细：在大重点基础上，把次重点也提取出来，内容更丰满")
    print("   3. 自定义：你输入关键词/一段话，只输出与关注点相关的重点")
    try:
        choice = input("请选择（1/2/3，回车默认 1）：").strip()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if choice in ("", "1"):
        return "concise", ""
    if choice == "2":
        return "detailed", ""

    # 模式三：自定义关键词
    if choice == "3":
        try:
            kw = input("请输入你想关注的关键词或一段话（回车放弃→退回详细）：").strip()
        except (EOFError, KeyboardInterrupt):
            kw = ""
        if not kw:
            return "detailed", ""
        return "query", kw

    # 非法输入退回 concise
    return "concise", ""


def _cleanup_workdir(path) -> None:
    """尽力清理临时目录（音频 mp3/wav、字幕 vtt/srt 等中间文件）。失败静默忽略。"""

    try:
        p = Path(path)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _make_workdir() -> Path:
    """本次运行的中间文件目录（音频/字幕），放在项目下 output/.workdir，用户可见且可控。

    为什么不放系统 temp：之前放在系统临时目录，用户既找不到、失败后又被清理，
    无法排查。改到项目下明确路径，并在 UI/日志告知；成功自动清理，失败保留。
    """
    base = ROOT / "output" / ".workdir"
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = base / f"run_{ts}_{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_bilibili(url: str, cfg, force_audio: bool = False, page_index: int | None = None, formats: str | None = None, mode: str | None = None, keywords: str = "") -> int:
    """B站完整链路：选集 → 提取 →（必要时转录）→ 摘要 → 导出 → 推送。

    中间文件（音频/字幕）落在每次运行新建的隔离临时目录，跑完即清理，不给用户留垃圾。
    """

    proxy = _proxy_from_config(cfg)
    _set_proxy_env(proxy)

    print("\n【步骤 ①】准备 B站登录凭证（仅取「真字幕」需要；没有也能先跑，可能少字幕）")
    sessdata = auth.get_bilibili_sessdata()

    print("\n【步骤 ②】配置 AI 摘要 Key（首次需要，用自己的 Key，不会上传）")
    cfg.ai = auth.setup_ai()

    # 输出目录（提前告知用户，避免跑完找不到产物）
    save_dir = exporter._resolve_save_dir(cfg.output)
    print(f"\n📁 输出目录（摘要文档将保存在这里）：{save_dir}")

    # 本次运行的中间文件目录（音频/字幕），放在项目下 output/.workdir，用户可见且可控
    run_workdir = _make_workdir()
    print(f"\n📂 中间文件临时目录：{run_workdir}")
    print("   · 有字幕：仅缓存字幕(json，很小)，处理完即删")
    print("   · 无字幕：会下载音频(.mp3)并转码(.wav)，处理成功后自动删除，失败则保留以便排查")

    # 合集检测 + 选集
    print("\n【步骤 ③】检测视频信息（合集会让你选集）...")
    page_index = _select_page(url, sessdata, forced=page_index)

    if force_audio:
        print(f"\n   强制音频转录模式（--audio）：忽略字幕，直接下载音频转录")
    else:
        print(f"\n   提取视频文字（自动判断：有字幕读字幕 / 无字幕下载音频）...")

    success = False
    try:
        t = extractor.extract(
            url, download_audio=True, sessdata=sessdata, force_audio=force_audio,
            page_index=page_index, workdir=run_workdir,
        )
        print(f"✅ 提取完成：《{t.title}》")
        print(f"   作者：{t.author or '未知'}  时长：{_fmt_dur(t.duration)}  字幕段数：{len(t.segments)}")

        # ④ 无字幕 → 转录（默认 GPU 加速，无 GPU 回退 CPU 并提醒）
        if not t.segments and t.audio_path:
            print("\n【步骤 ④】检测到无字幕，开始转录音频 → 文字")
            if cfg.whisper.mode == "local":
                print("🎙️ 调用本地 faster-whisper 转录（免 API Key）...")
            else:
                print("🎙️ 调用云端 Whisper 转录（按分钟计费）...")
            # ⏱️ 转录预估时间在「真正开始转录前」告知用户
            try:
                print(f"⏱️ 预计转录耗时：{_estimate_transcribe_time(t.duration, cfg.whisper)}")
            except Exception:  # noqa: BLE001
                pass
            try:
                t = transcriber.transcribe(t, cfg.whisper)
            except Exception as exc:  # noqa: BLE001
                print(f"\n❌ 转录失败：{exc}")
                print(traceback.format_exc())
                print("💡 建议排查：")
                print("   1) 长视频已自动分块转录（每 5 分钟一块），若仍报内存分配失败，可调小 core/transcriber.py 的 _CHUNK_SEC")
                print("   2) 若模型下载失败，可设置 HF_HUB_OFFLINE=1 并手动下载模型到 models/faster-whisper-base/")
                print("   3) 不想折腾本地环境，可改 config.yaml whisper.mode: api（需 OpenAI Key）")
                return 1
            print(f"✅ 转录完成：{len(t.segments)} 段文字")
        elif not t.segments:
            print("⚠️ 既无字幕也无法下载音频，无法继续。若是【无字幕】视频，请确认已装 ffmpeg。")
            return 1
        else:
            print("   该视频有字幕，跳过转录。")

        # ⑤ 选择内容模式 + 摘要（AI）
        print(f"\n【步骤 ⑤】选择内容输出模式 + AI 生成结构化摘要（provider={cfg.ai.provider}）...")
        mode, keywords = _select_mode(forced=mode, keywords=keywords)
        _MODE_LABEL = {"concise": "精简（核心大重点）", "detailed": "详细（大重点+次重点）", "query": f"自定义（关注：{keywords}）"}
        print(f"   内容模式：{_MODE_LABEL.get(mode, mode)}")
        mode_label = _short_mode_label(mode, keywords)

        # ⏱️ AI 摘要预估时间（转录已完成，这里只估算 AI 摘要部分，提前告知用户）
        eta_ai = _estimate_ai_time(t.duration, cfg.ai.tier)
        print(f"\n⏱️ 预计 AI 摘要耗时：{eta_ai}")

        summary = summarizer.generate_summary(t, cfg.ai, proxy=proxy, mode=mode, query=keywords)

        # ⑥ 导出（多格式选择）
        formats = _select_formats(forced=formats)
        print(f"\n   正在导出：{', '.join(formats)} ...")
        paths = exporter.export(summary, cfg.output, t, formats=formats, mode_label=mode_label)

        print(f"\n🎉 完成！共导出 {len(paths)} 个文件：")
        for p in paths:
            print(f"   {p}")
        if cfg.notify:
            notify.notify(
                f"VidGrab 完成：{t.title}\n摘要文档：{paths[0]}", cfg.notify
            )
        success = True
        return 0
    except KeyboardInterrupt:
        # Ctrl+C：默认处理器会把 traceback 打到 stderr（现已 tee 进日志），这里再给一句明确提示
        print(f"\n⚠️ 已被你中断（Ctrl+C）。中间文件已保留在：{run_workdir}")
        print("   （若想继续：重跑同一命令即可；无字幕长视频建议改用 whisper.mode: api 云端转录提速）")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ 处理出错：{exc}")
        print(traceback.format_exc())
        return 1
    finally:
        # 成功才清理中间文件；失败保留，便于用户排查根因
        if success:
            _cleanup_workdir(run_workdir)
            _cleanup_workdir(Path(tempfile.gettempdir()) / "vidgrab")
            print(f"\n📁 输出目录：{save_dir}（中间临时文件已清理）")
        else:
            print(f"\n⚠️ 处理未完成，中间文件已保留在：{run_workdir}（便于排查；下次成功运行会自动清理无关旧目录）")


def _short_mode_label(mode: str, keywords: str = "") -> str:
    """返回文件名后缀用的模式标识（精简/详细/自定义）。"""
    if mode == "query" and keywords:
        kw = keywords.strip().replace(" ", "_").replace("，", "_").replace(",", "_")
        if len(kw) > 15:
            kw = kw[:15] + "…"
        return f"自定义-{kw}"
    return {"concise": "精简", "detailed": "详细"}.get(mode, mode)


def _estimate_transcribe_time(duration: float, whisper_cfg=None) -> str:
    """仅估算转录耗时，在「真正开始转录前」告知用户（无字幕路径）。"""

    mode = getattr(whisper_cfg, "mode", "local") if whisper_cfg else "local"
    device = getattr(whisper_cfg, "device", "auto") if whisper_cfg else "auto"
    if mode == "api":
        t_low, t_high = duration * 0.15, duration * 0.35
        note = "云端 Whisper 转录（较快）"
    elif device == "cpu":
        t_low, t_high = duration * 0.5, duration * 1.0
        note = "本地 CPU 转录"
    elif device == "gpu":
        t_low, t_high = duration / 6, duration / 4
        note = "本地 GPU 转录"
    else:  # auto：默认按 GPU 估算，并提示本机无 GPU 会更慢
        t_low, t_high = duration / 6, duration * 0.6
        note = "本地转录（默认按 GPU 估算；若本机无 GPU 实际走 CPU 会更慢）"
    low, high = int(t_low), int(t_high)
    if high < 60:
        return f"约 {low}-{high} 秒（{note}）"
    return f"约 {low // 60}分{low % 60}秒 - {high // 60}分{high % 60}秒（{note}）"


def _estimate_ai_time(duration: float, tier: str) -> str:
    """仅估算 AI 摘要耗时，在「转录完成后、开始摘要前」告知用户。"""

    minutes = max(duration / 60.0, 0.1)
    ai_calls = max(1, int(round(minutes / 10)) or 1)
    ai_sec = ai_calls * 8
    if tier == "free":
        ai_sec += (ai_calls - 1) * 14  # free 档工具主动加 ~14s 调用间隔
    low = max(5, int(ai_sec * 0.7))
    high = int(ai_sec * 1.5) + 5
    if high < 60:
        return f"约 {low}-{high} 秒（AI 摘要）"
    return f"约 {low}秒 - {high // 60}分{high % 60}秒（AI 摘要）"


def _estimate_time(duration: float, has_subtitle: bool, tier: str, whisper_cfg=None) -> str:
    """基于视频时长粗估处理时间，返回可读字符串（提前告知用户）。

    论证（可行性）：
      - 有字幕：主要耗时是 AI 摘要。长视频按 ~10 分钟/次调用切块，每次调用约 5-10s；
        free 档工具会主动加 ~14s 调用间隔，paid 档不主动限速。
      - 无字幕：额外要转录。区间按 whisper 配置收窄（见下），避免「GPU 下限 ~ CPU 上限」
        跨度过大让用户误以为要等很久。这是「预估」不是精确值，用于让用户心里有数。
    """
    minutes = max(duration / 60.0, 0.1)
    ai_calls = max(1, int(round(minutes / 10)) or 1)
    ai_sec = ai_calls * 8
    if tier == "free":
        ai_sec += (ai_calls - 1) * 14  # free 档主动间隔

    if has_subtitle:
        low = max(5, int(ai_sec * 0.7))
        high = int(ai_sec * 1.5) + 5
        if high < 60:
            return f"约 {low}-{high} 秒（有字幕，主要是 AI 摘要）"
        return f"约 {low}秒 - {high // 60}分{high % 60}秒（有字幕，主要是 AI 摘要）"

    # 无字幕：需转录 + 摘要。按 whisper 配置收窄区间，避免 GPU~CPU 跨度过大。
    mode = getattr(whisper_cfg, "mode", "local") if whisper_cfg else "local"
    device = getattr(whisper_cfg, "device", "auto") if whisper_cfg else "auto"
    if mode == "api":
        # 云端 API 转录很快（按音频时长计费、远快于实时）
        t_low, t_high = duration * 0.15, duration * 0.35
    elif device == "cpu":
        t_low, t_high = duration * 0.5, duration * 1.0
    elif device == "gpu":
        t_low, t_high = duration / 6, duration / 4
    else:  # auto：默认按 GPU 估算（本工具 GPU 优先），并说明实际可能更慢
        t_low, t_high = duration / 6, duration * 0.6
    est_low = int(t_low + ai_sec)
    est_high = int(t_high + ai_sec)
    note = "无字幕：转录 + AI 摘要"
    if mode == "api":
        note += "（云端转录，较快）"
    elif device in ("gpu", "auto"):
        note += "（默认按 GPU 加速估算；若本机无 GPU 实际走 CPU 会更慢）"
    else:
        note += "（CPU 转录）"
    if est_high < 60:
        return f"约 {est_low}-{est_high} 秒（{note}）"
    return (f"约 {est_low // 60}分{est_low % 60}秒 - {est_high // 60}分{est_high % 60}秒"
            f"（{note}）")


def _fmt_dur(seconds) -> str:
    try:
        return format_duration(float(seconds or 0))
    except Exception:  # noqa: BLE001
        return str(seconds)


def _run_youtube_reserved(url: str, cfg) -> int:
    """YouTube 当前仅预留接口：尝试提取字幕，但不做摘要/导出（bot 校验不稳定）。"""

    print("📺 YouTube 完整链路（转录→摘要→导出）预留中，待 B站跑通后扩展。")
    print("    当前仅尝试提取字幕（可能因 YouTube 机器人校验而不稳定）：")
    proxy = _proxy_from_config(cfg)
    _set_proxy_env(proxy)
    cookies_file = auth.get_youtube_cookies_file() or (cfg.youtube.cookies_file if cfg.youtube else "")
    try:
        t = extractor.extract(url, proxy=proxy, cookies_file=cookies_file)
        print(f"    标题：{t.title}  字幕段数：{len(t.segments)}")
    except ValueError as e:
        print("    提取失败：\n" + str(e))
    print("\n    💡 想体验完整摘要流程，先用 B站链接运行本工具。")
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # 解析可选参数（链接以外的开关）
    force_audio = False
    page_index = None
    formats_arg = None
    mode_arg = None
    keywords_arg = ""
    rest = []
    for a in argv:
        if a in ("--audio", "--force-audio", "-a"):
            force_audio = True
        elif a.startswith("--page="):
            try:
                page_index = int(a.split("=", 1)[1]) - 1  # 用户给的是 1-based
            except ValueError:
                print(f"⚠️ --page 参数无效：{a}，按第1集处理")
        elif a.startswith("--formats="):
            formats_arg = a.split("=", 1)[1]
        elif a.startswith("--mode="):
            mode_arg = a.split("=", 1)[1]
        elif a.startswith("--keywords="):
            keywords_arg = a.split("=", 1)[1]
        else:
            rest.append(a)
    argv = rest

    # 1) 取链接
    if argv:
        url = argv[0].strip()
    else:
        try:
            url = input("请输入视频链接：").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not url:
        print("❌ 未提供视频链接")
        return 1

    _welcome()
    if force_audio:
        print("⚙️ 已开启 --audio：忽略字幕，强制走「音频转录」路径（用于测试无字幕流程 / 覆盖劣质字幕）")
    _preflight()

    # 配置自举：config.yaml 缺失时从模板生成，别人下载即用
    auth.ensure_config_file()
    cfg = config.load_config()
    platform = extractor.detect_platform(url)

    if platform == Platform.BILIBILI:
        return _run_bilibili(
            url, cfg, force_audio=force_audio, page_index=page_index,
            formats=formats_arg, mode=mode_arg, keywords=keywords_arg,
        )
    if platform == Platform.YOUTUBE:
        return _run_youtube_reserved(url, cfg)

    print(f"❌ 暂不支持的平台：{url}（当前完整流程仅支持 B站；YouTube 预留中）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
