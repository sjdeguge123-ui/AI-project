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
import atexit
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


def _title_prefix(title: str) -> str:
    """交互提示前缀：有标题时带《标题》，便于连续处理多个视频时区分上下文。"""
    return f"关于《{title}》：" if title else ""


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


def _parent_alive_win() -> bool:
    """Windows 下判断「启动者（agent / 终端）」是否还活着。

    VidGrab 被 agent / 自动化 detached 启动后，Windows 不会在父进程死亡时级联杀子进程。
    返回 False = 父进程已死，本进程已是被遗留的孤儿，应主动清理退出。
    （Linux 下子进程会被 reparent 到 init，不存在此问题，调用方仅在 win32 时调用本函数。）
    """
    try:
        import ctypes
        from ctypes import wintypes

        ppid = os.getppid()
        if ppid <= 0:
            return True  # 无法判定时保守认为活着
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, ppid)
        if not h:
            return False  # 父进程不存在 = 已死
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return code.value == 259  # 259 = STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return True


def _start_parent_watchdog() -> None:
    """仅 Windows + 非交互（agent / 管道）模式启动：父进程死亡则自清理并退出。

    根因修复：本机曾出现 13 个 VidGrab 主进程被 agent 会话遗留成孤儿，一直占内存。
    看门狗每 5 秒检测父进程存活，父死即杀掉转录 worker 子进程并退出，从源头杜绝孤儿。
    交互终端模式（stdin 是 tty）不启用——关闭终端会触发控制台事件杀进程，且避免误杀。
    """
    if sys.platform != "win32":
        return
    if sys.stdin.isatty():
        return
    import threading
    import time

    def _loop() -> None:
        while True:
            try:
                if not _parent_alive_win():
                    print("[VidGrab] 检测到启动进程已退出，自动清理转录子进程并结束。", flush=True)
                    try:
                        transcriber._kill_active_worker()
                    except Exception:  # noqa: BLE001
                        pass
                    os._exit(0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)

    threading.Thread(target=_loop, daemon=True).start()


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


def _select_formats(forced: str = None, title: str = "") -> list:
    """选择输出格式（可多选）。返回格式列表。

    - forced 不为 None：解析 --formats 指定的逗号列表（如 "markdown,html"），不阻塞；
    - 非交互环境：默认 Markdown；
    - 交互环境：列出菜单让用户选。
    """
    fmt_map = {"1": "markdown", "2": "html", "3": "docx", "4": "pdf", "5": "image"}
    valid = ("md", "markdown", "html", "docx", "word", "pdf", "image", "png", "jpg", "jpeg")

    if forced:
        f = (forced or "").strip().lower()
        if f in ("all", "全选", "全部", "0"):
            return ["markdown", "html", "docx", "pdf", "image"]
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

    if title:
        print(f"\n【步骤 ⑥】关于《{title}》，选择输出格式")
    else:
        print("\n【步骤 ⑥】选择输出格式")
    print("   支持的格式：")
    print("     0. 全选（一次性导出全部：Markdown / HTML / Word / PDF / 图片）")
    print("     1. Markdown (.md)  —— 通用，推荐")
    print("     2. HTML (.html)    —— 可在浏览器打开，样式美观")
    print("     3. Word (.docx)    —— 可在 Word/WPS 编辑")
    print("     4. PDF (.pdf)      —— 适合打印/分享")
    print("     5. 图片 (.png)     —— 信息图，便于分享/预览")
    print("   可多选，用逗号分隔（如 1,3）；输入 0 全选；回车默认 Markdown")
    try:
        choice = input("请选择：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if not choice:
        return ["markdown"]
    if choice in ("0", "all", "全选", "全部"):
        return ["markdown", "html", "docx", "pdf", "image"]

    formats = []
    for c in choice.replace("，", ",").split(","):
        c = c.strip()
        if c in fmt_map:
            formats.append(fmt_map[c])
        elif c.lower() in valid:
            formats.append(c.lower())
    return formats or ["markdown"]


def _select_mode(forced: str = None, keywords: str = "", title: str = "") -> tuple:
    """选择内容输出模式。返回 (mode, keywords)。

    三种模式（用户需求）：
      - concise （默认，模式一）：只提炼核心大重点；
      - detailed（模式二）：大重点 + 次重点，内容脉络更丰满；
      - query   （模式三）：用户自定义关键词/问题，只输出相关重点。

    - forced 不为 None：直接解析 --mode（concise/detailed/query），不阻塞；
    - 非交互环境（stdin 不是终端）：默认 concise；
    - 交互环境：列出三种模式让用户选，选「自定义」则进一步询问关键词。
    """

    valid = {"concise", "detailed", "query", "fulltext"}

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
                    kw = input(_title_prefix(title) + "请输入你想关注的关键词或一段话（回车放弃→退回精简（默认））：").strip()
                except (EOFError, KeyboardInterrupt):
                    kw = ""
                keywords = kw
                if not keywords:
                    mode = "concise"
        return mode, keywords

    # 非交互环境默认 concise
    if not sys.stdin.isatty():
        return "concise", ""

    if title:
        print(f"\n【步骤 ⑤-1】关于《{title}》视频，选择内容输出模式")
    else:
        print("\n【步骤 ⑤-1】选择内容输出模式")
    print("   1. 精简（默认）：只提炼核心大重点，内容脉络简洁")
    print("   2. 详细：在大重点基础上，把次重点也提取出来，内容更丰满")
    print("   3. 自定义：你输入关键词/一段话，只输出与关注点相关的重点")
    print("   4. 全文文案：保留完整转录原文，按时间标注 [MM:SS]，仅做轻量概述")
    try:
        choice = input("请选择（1/2/3/4，回车默认 1）：").strip()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if choice in ("", "1"):
        return "concise", ""
    if choice == "2":
        return "detailed", ""
    if choice == "4":
        return "fulltext", ""

    # 模式三：自定义关键词
    if choice == "3":
        try:
            kw = input(_title_prefix(title) + "请输入你想关注的关键词或一段话（回车放弃→退回精简（默认））：").strip()
        except (EOFError, KeyboardInterrupt):
            kw = ""
        if not kw:
            return "concise", ""
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


def _timed_input(prompt: str, timeout: float = 100) -> str:
    """带超时 + 倒计时显示的输入：超过 timeout 秒未输入则抛 TimeoutError。

    用于交互式询问：用户迟迟不回复时自动退出并清理，避免进程一直挂着。
    倒计时每秒刷新一行（用 \\r 回车覆盖），让用户清楚还剩多少时间。
    """

    import threading
    import time

    print(prompt)
    box: dict = {}

    def _get() -> None:
        try:
            box["v"] = input()
        except (EOFError, KeyboardInterrupt):
            box["v"] = ""

    th = threading.Thread(target=_get, daemon=True)
    th.start()
    deadline = time.time() + timeout
    while th.is_alive() and time.time() < deadline:
        remain = int(deadline - time.time())
        print(f"\r   ⏳ 剩余 {remain:03d}s（输入任意内容即生效，超时自动退出）", end="", flush=True)
        th.join(1)
    print()  # 倒计时行收尾
    if th.is_alive():
        # 超时：丢弃这个等待中的 input 线程（daemon 会随进程退出）
        raise TimeoutError()
    return (box.get("v") or "").strip()


def _export_one(t, cfg, proxy: str, formats: list, mode: str, keywords: str = "") -> list:
    """生成指定模式的摘要并导出，返回生成的文件路径列表。

    供「首个版本」与「交互式追加其他版本」复用，避免重复下载/转录。
    """

    _MODE_LABEL = {
        "concise": "精简（核心大重点）",
        "detailed": "详细（大重点+次重点）",
        "query": f"自定义（关注：{keywords}）",
        "fulltext": "全文文案（带时间戳全文）",
    }
    mode_label = _short_mode_label(mode, keywords)
    print(f"\n   内容模式：{_MODE_LABEL.get(mode, mode)}")

    # ⏱️ AI 摘要预估时间（转录已完成，这里只估算 AI 摘要部分，提前告知用户）
    if mode != "fulltext":
        eta_ai = _estimate_ai_time(t.duration, cfg.ai.tier)
        print(f"   ⏱️ 预计 AI 摘要耗时：{eta_ai}")

    summary = summarizer.generate_summary(t, cfg.ai, proxy=proxy, mode=mode, query=keywords)

    print(f"\n   正在导出：{', '.join(formats)} ...")
    paths = exporter.export(summary, cfg.output, t, formats=formats, mode_label=mode_label)
    print(f"\n🎉 完成！共导出 {len(paths)} 个文件：")
    for p in paths:
        print(f"   {p}")
    if cfg.notify:
        notify.notify(
            f"VidGrab 完成：{t.title}\n摘要文档：{paths[0]}", cfg.notify
        )
    return paths


def _offer_other_versions(t, cfg, proxy: str, formats: list, first_mode: str, first_keywords: str, title: str = "") -> None:
    """导出成功后（仅 TTY 交互环境）询问是否还要其他版本。

    用户明确不需要 / 超时未回复 → 退出（中间文件由主流程 finally 清理）。
    用户选择其他版本 → 重新生成并导出，可继续追问。
    """

    tried = {first_mode}
    while True:
        head = f"《{title}》" if title else ""
        print(f"\n💡 {head}已导出一版，是否还要其他版本？")
        print("   1. 精简   2. 详细   3. 自定义（关键词）   4. 全文文案   5. 不需要了，退出")
        try:
            # 标题已在上方 💡 行展示，输入提示不再重复，避免冗余
            choice = _timed_input("请选择（1-5，默认 5 退出；100 秒无操作自动退出）：", timeout=100)
        except TimeoutError:
            print("\n⏰ 100 秒未操作，自动退出。中间临时文件将在程序结束时清理。")
            return
        except (EOFError, KeyboardInterrupt):
            print("\n👋 已退出。")
            return

        if choice in ("", "5"):
            print("👋 已退出，无需其他版本。")
            return
        if choice == "1":
            m, kw = "concise", ""
        elif choice == "2":
            m, kw = "detailed", ""
        elif choice == "3":
            try:
                kw = input("   " + _title_prefix(title) + "请输入你想关注的关键词或一段话：").strip()
            except (EOFError, KeyboardInterrupt):
                kw = ""
            if not kw:
                continue
            m = "query"
        elif choice == "4":
            m, kw = "fulltext", ""
        else:
            print("   ⚠️ 无效选择，请重试。")
            continue

        if m in tried:
            print(f"   （{m} 已生成过，重新生成一次）")
        tried.add(m)
        try:
            _export_one(t, cfg, proxy, formats, m, kw)
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 生成失败：{exc}")


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
        mode, keywords = _select_mode(forced=mode, keywords=keywords, title=t.title)
        if mode not in ("concise", "detailed", "query", "fulltext"):
            mode = "concise"
        print(f"   内容模式：{mode}")

        # ⑥ 导出格式（仅选一次，所有版本复用，避免重复询问）
        formats = _select_formats(forced=formats, title=t.title)
        if not formats:
            formats = ["markdown"]

        # 首个版本：生成摘要 + 导出
        _export_one(t, cfg, proxy, formats, mode, keywords)

        # ⑦ 交互式询问是否还要其他版本（仅 TTY；非 TTY 直接结束，由 finally 清理中间文件）
        if sys.stdin.isatty():
            _offer_other_versions(t, cfg, proxy, formats, mode, keywords, title=t.title)

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
    return {"concise": "精简", "detailed": "详细", "query": "自定义", "fulltext": "全文文案"}.get(mode, mode)


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

    # 启动「父进程死亡看门狗」：被 agent/自动化 detached 启动时，若启动者退出则自清理，
    # 杜绝孤儿进程（仅 Windows + 非交互模式生效，详见 _start_parent_watchdog）。
    _start_parent_watchdog()

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
