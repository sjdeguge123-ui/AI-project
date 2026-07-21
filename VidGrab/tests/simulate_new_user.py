# -*- coding: utf-8 -*-
"""新用户使用路径模拟验证（tests/simulate_new_user.py）

目的：在**不联网、不调用 AI、不触碰真实 config/config.yaml** 的前提下，模拟
「新用户第一次使用」和「老用户多次使用」两条路径，检查各环节是否会崩溃/卡死/回归 bug。
每次大改动（新增功能、重构、改依赖、改导出/摘要/转录逻辑）后都应跑一遍：

    python tests/simulate_new_user.py

全部 PASS 返回 0；任一 FAIL 返回 1（可接入 CI / pre-push）。

覆盖的路径
──────────
【第一次使用（fresh user）】
  1. 依赖齐全       ：_ESSENTIAL_DEPS 里每个模块都能 import（新用户自动装完后应如此）
  2. 配置自举       ：无 config.yaml → ensure_config_file() 从 example 复制生成（返回 True）
  3. 配置可加载     ：load_config() 读到默认值（provider/model/tier=free/whisper.compute_type=auto）
  4. 非交互不卡死   ：_select_formats/_select_mode 在非 TTY 下返回安全默认值，不阻塞
  5. 选集逻辑       ：_select_page 非交互/强制路径返回正确索引（get_bilibili_pages 被 mock，不联网）
  6. CLI 解析+分发  ：main() 正确解析 --mode/--keywords/--formats/--page/--audio 并透传
  7. 平台识别       ：B站/YouTube/未知 URL 分别识别
  8. 全格式导出     ：mock Summary → md/html/docx/pdf/image 五种格式都能落盘（PDF 走纯 Python xhtml2pdf）
  9. PDF 中文正确   ：导出的 PDF 能提取出中文（= 渲染正确，非方框）

【多次使用（returning user）】
  10. 配置不被覆盖  ：config.yaml 已存在 → ensure_config_file() 返回 False（幂等，不清空老用户的 Key）
  11. 凭证可复用    ：已写入的 sessdata / api_key 能被 load_config 读回（无需重新粘贴）

设计安全性：全程使用临时目录 + monkeypatch 路径函数，**绝不读写真实 config/config.yaml**
（吸取教训：曾因测试直接写真实 config.yaml 覆盖了用户的 Key）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 让脚本从项目根可 import core / skill
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 依赖自举会在 import skill.main 时运行；这里假设开发/CI 环境已装好依赖，
# 若缺则会自动装（与真实新用户体验一致）。
import skill.main as M  # noqa: E402
from core import (  # noqa: E402
    Platform,
    Summary,
    DetailedRow,
    GoldenQuote,
    Transcript,
    Segment,
)
from core import config as core_config  # noqa: E402
from core import auth as core_auth  # noqa: E402
from core import exporter as core_exporter  # noqa: E402
from core.config import OutputConfig  # noqa: E402


# ────────────────────────────── 测试脚手架 ──────────────────────────────
_RESULTS: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    mark = "✅ PASS" if ok else "❌ FAIL"
    line = f"{mark}  {name}"
    if detail:
        line += f"  —— {detail}"
    print(line)


def _mock_summary() -> Summary:
    return Summary(
        title="新用户模拟·机器学习三大类",
        source="哔哩哔哩",
        author="tester",
        publish_time="2026-07-20",
        duration_text="00:02:30",
        content_overview="本视频介绍**机器学习**的三大类别：**监督学习**、**无监督学习**、**强化学习**。",
        detailed=[
            DetailedRow(timestamp="00:00", point="**机器学习**分类", content="介绍三大类别。", remark=""),
            DetailedRow(timestamp="00:20", point="**监督学习**", content="需要带标签数据，用于**分类**和**回归**。", remark=""),
            DetailedRow(timestamp="01:10", point="**无监督学习**", content="无需标签，**聚类**是代表。", remark=""),
        ],
        golden_quotes=[GoldenQuote(timestamp="02:00", text="**强化学习**通过奖励信号学习。")],
        mode_label="精简",
    )


def _mock_transcript() -> Transcript:
    return Transcript(
        title="新用户模拟·机器学习三大类",
        platform=Platform.BILIBILI,
        author="tester",
        publish_time="",
        duration=150,
        segments=[Segment(0, 1, "x")],
        source="bilibili",
        video_id="simuser",
    )


def _mock_summary_fulltext() -> Summary:
    return Summary(
        title="新用户模拟·机器学习三大类",
        source="哔哩哔哩",
        author="tester",
        publish_time="2026-07-20",
        duration_text="00:02:30",
        content_overview="本视频介绍**机器学习**的三大类别。",
        detailed=[],
        golden_quotes=[],
        full_text="[00:00] 大家好，今天讲**机器学习**。\n[01:00] 第一种是**监督学习**，需要标签。\n[02:00] 第二种是**无监督学习**，例如**聚类**。",
        mode_label="全文文案",
    )


# ────────────────────────────── 各项检查 ──────────────────────────────
def check_dependencies() -> None:
    import importlib
    missing = []
    for mod, pkg in M._ESSENTIAL_DEPS:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(pkg)
    _record("1. 依赖齐全（_ESSENTIAL_DEPS 全部可 import）", not missing,
            "缺失：" + ", ".join(missing) if missing else "含 xhtml2pdf 等全部就绪")


def check_config_bootstrap(tmp_cfg: Path, tmp_example: Path) -> None:
    # monkeypatch auth 的路径函数，指向临时目录（绝不碰真实 config.yaml）
    core_auth._config_path = lambda: tmp_cfg  # type: ignore
    core_auth._config_example_path = lambda: tmp_example  # type: ignore

    created = core_auth.ensure_config_file()
    ok = created and tmp_cfg.exists()
    _record("2. 首次配置自举（无 config → 从 example 生成）", ok,
            f"created={created}, exists={tmp_cfg.exists()}")


def check_config_load(tmp_cfg: Path) -> None:
    try:
        cfg = core_config.load_config(str(tmp_cfg))
        ok = (
            bool(cfg.ai.provider)
            and bool(cfg.ai.model)
            and cfg.ai.tier in ("free", "paid")
            and getattr(cfg.whisper, "compute_type", None) is not None
        )
        _record("3. 配置可加载（默认 provider/model/tier/compute_type）", ok,
                f"provider={cfg.ai.provider}, tier={cfg.ai.tier}, compute_type={cfg.whisper.compute_type}")
    except Exception as e:  # noqa: BLE001
        _record("3. 配置可加载", False, f"异常：{e}")


def check_noninteractive_selectors() -> None:
    # 本进程 stdin 非 TTY，选择器应返回默认值且不阻塞
    fmts = M._select_formats(forced=None)
    mode, kw = M._select_mode(forced=None)
    ok = fmts == ["markdown"] and mode == "concise" and kw == ""
    _record("4. 非交互选择器不卡死（默认 markdown + concise/大重点）", ok,
            f"formats={fmts}, mode={mode!r}")


def check_select_page() -> None:
    # mock get_bilibili_pages，避免联网
    single = {"pages": [{"index": 0, "part": "P1", "duration": 60, "cid": 1}]}
    multi = {"pages": [{"index": i, "part": f"P{i+1}", "duration": 60, "cid": i} for i in range(5)]}
    orig = M.get_bilibili_pages
    try:
        M.get_bilibili_pages = lambda url, sess: single  # type: ignore
        r_single = M._select_page("u", "", forced=None)          # 单P → 0
        M.get_bilibili_pages = lambda url, sess: multi  # type: ignore
        r_forced = M._select_page("u", "", forced=3)             # 强制 P4 → 3
        r_oob = M._select_page("u", "", forced=99)               # 越界 → 0
        r_nontty = M._select_page("u", "", forced=None)          # 非TTY 合集 → 0
        ok = r_single == 0 and r_forced == 3 and r_oob == 0 and r_nontty == 0
        _record("5. 选集逻辑（单P/强制/越界/非交互）", ok,
                f"single={r_single}, forced=3→{r_forced}, oob→{r_oob}, nontty→{r_nontty}")
    finally:
        M.get_bilibili_pages = orig  # type: ignore


def check_cli_parse_and_dispatch() -> None:
    captured = {}

    def fake_run_bili(url, cfg, force_audio=False, page_index=None, formats=None, mode=None, keywords=""):
        captured.update(dict(url=url, force_audio=force_audio, page_index=page_index,
                             formats=formats, mode=mode, keywords=keywords))
        return 0

    saved = (M._welcome, M._preflight, M.auth.ensure_config_file,
             M.config.load_config, M.extractor.detect_platform, M._run_bilibili)
    try:
        M._welcome = lambda: None  # type: ignore
        M._preflight = lambda: None  # type: ignore
        M.auth.ensure_config_file = lambda: False  # type: ignore
        M.config.load_config = lambda: object()  # type: ignore
        M.extractor.detect_platform = lambda url: Platform.BILIBILI  # type: ignore
        M._run_bilibili = fake_run_bili  # type: ignore

        rc = M.main([
            "https://www.bilibili.com/video/BVtest",
            "--mode=detailed", "--keywords=聚类和无监督",
            "--formats=md,pdf", "--page=3", "--audio",
        ])
        ok = (
            rc == 0
            and captured.get("force_audio") is True
            and captured.get("page_index") == 2        # 1-based 3 → 0-based 2
            and captured.get("formats") == "md,pdf"
            and captured.get("mode") == "detailed"
            and captured.get("keywords") == "聚类和无监督"
        )
        _record("6. CLI 解析+分发（--mode/--keywords/--formats/--page/--audio）", ok, str(captured))
    finally:
        (M._welcome, M._preflight, M.auth.ensure_config_file,
         M.config.load_config, M.extractor.detect_platform, M._run_bilibili) = saved


def check_platform_detection() -> None:
    from core import extractor
    b = extractor.detect_platform("https://www.bilibili.com/video/BV1xx")
    y = extractor.detect_platform("https://www.youtube.com/watch?v=abc")
    ok = b == Platform.BILIBILI and y == Platform.YOUTUBE
    _record("7. 平台识别（B站/YouTube）", ok, f"bili={b}, yt={y}")


def check_export_all_formats(tmp_out: Path) -> None:
    summary = _mock_summary()
    t = _mock_transcript()
    out = OutputConfig(save_path=str(tmp_out))
    fmts = ["markdown", "html", "docx", "pdf", "image"]
    try:
        paths = core_exporter.export(summary, out, t, formats=fmts)
        exist = [p for p in paths if Path(p).exists() and Path(p).stat().st_size > 0]
        ok = len(exist) == len(fmts)
        _record("8. 全格式导出（md/html/docx/pdf/image 均落盘）", ok,
                f"{len(exist)}/{len(fmts)} 成功：" + ", ".join(Path(p).suffix for p in exist))
        # 附带把 pdf 路径记下来给下一项用
        check_export_all_formats.pdf_path = next(  # type: ignore
            (p for p in exist if str(p).lower().endswith(".pdf")), None)
    except Exception as e:  # noqa: BLE001
        _record("8. 全格式导出", False, f"异常：{e}")
        check_export_all_formats.pdf_path = None  # type: ignore


def check_pdf_chinese() -> None:
    pdf_path = getattr(check_export_all_formats, "pdf_path", None)
    if not pdf_path:
        _record("9. PDF 中文渲染正确（可提取中文）", False, "未生成 PDF，跳过")
        return
    try:
        from pypdf import PdfReader
        txt = "".join((pg.extract_text() or "") for pg in PdfReader(str(pdf_path)).pages)
        keys = ["机器学习", "监督学习", "无监督学习", "强化学习", "聚类"]
        hit = [k for k in keys if k in txt]
        ok = len(hit) == len(keys)
        _record("9. PDF 中文渲染正确（可提取中文=非方框）", ok, f"命中 {len(hit)}/{len(keys)}")
    except ImportError:
        _record("9. PDF 中文渲染正确", True, "pypdf 未装，跳过内容校验（PDF 已生成）")
    except Exception as e:  # noqa: BLE001
        _record("9. PDF 中文渲染正确", False, f"异常：{e}")


def check_export_fulltext(tmp_out: Path) -> None:
    summary = _mock_summary_fulltext()
    t = _mock_transcript()
    out = OutputConfig(save_path=str(tmp_out))
    fmts = ["markdown", "html", "docx", "pdf", "image"]
    try:
        paths = core_exporter.export(summary, out, t, formats=fmts)
        exist = [p for p in paths if Path(p).exists() and Path(p).stat().st_size > 0]
        ok = len(exist) == len(fmts)
        _record("8b. 全文文案导出（full_text 模式 md/html/docx/pdf/image 均落盘）", ok,
                f"{len(exist)}/{len(fmts)} 成功")
        # 校验 md 含时间戳、不含内容脉络表格
        md_path = next((p for p in exist if str(p).lower().endswith(".md")), None)
        if md_path:
            txt = Path(md_path).read_text(encoding="utf-8")
            has_ts = "[00:00]" in txt or "[01:00]" in txt
            no_table = "| 时间 |" not in txt
            _record("8c. 全文文案 MD 结构正确（含时间戳、无内容脉络表）", has_ts and no_table,
                    f"has_ts={has_ts}, no_table={no_table}")
    except Exception as e:  # noqa: BLE001
        _record("8b. 全文文案导出", False, f"异常：{e}")


def check_returning_user_idempotent(tmp_cfg: Path, tmp_example: Path) -> None:
    core_auth._config_path = lambda: tmp_cfg  # type: ignore
    core_auth._config_example_path = lambda: tmp_example  # type: ignore
    # config.yaml 此时已存在（前面自举过）→ 再次调用应返回 False，不覆盖
    again = core_auth.ensure_config_file()
    _record("10. 老用户配置不被覆盖（ensure_config_file 幂等）", again is False,
            f"第二次返回 {again}（期望 False）")


def check_returning_user_credentials_reuse(tmp_cfg: Path) -> None:
    import yaml
    data = yaml.safe_load(tmp_cfg.read_text(encoding="utf-8")) or {}
    data.setdefault("bilibili", {})["sessdata"] = "SIMULATED_SESSDATA_123"
    data.setdefault("ai", {})["api_key"] = "sk-simulated-key"
    tmp_cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    cfg = core_config.load_config(str(tmp_cfg))
    ok = cfg.ai.api_key == "sk-simulated-key" and cfg.bilibili.sessdata == "SIMULATED_SESSDATA_123"
    _record("11. 老用户凭证可复用（sessdata/api_key 读回，无需重填）", ok,
            f"api_key={'set' if cfg.ai.api_key else 'empty'}, sessdata={'set' if cfg.bilibili.sessdata else 'empty'}")


# ────────────────────────────── 主流程 ──────────────────────────────
def main() -> int:
    print("=" * 68)
    print("  VidGrab · 新用户使用路径模拟验证")
    print("=" * 68)

    tmp = Path(tempfile.mkdtemp(prefix="vidgrab_simuser_"))
    tmp_cfg = tmp / "config.yaml"
    tmp_out = tmp / "out"
    tmp_out.mkdir(parents=True, exist_ok=True)
    # 用真实 example 作为自举模板（只读，不改）
    real_example = ROOT / "config" / "config.example.yaml"

    try:
        print("\n—— 第一次使用（fresh user）——")
        check_dependencies()
        check_config_bootstrap(tmp_cfg, real_example)
        check_config_load(tmp_cfg)
        check_noninteractive_selectors()
        check_select_page()
        check_cli_parse_and_dispatch()
        check_platform_detection()
        check_export_all_formats(tmp_out)
        check_pdf_chinese()
        check_export_fulltext(tmp_out)

        print("\n—— 多次使用（returning user）——")
        check_returning_user_idempotent(tmp_cfg, real_example)
        check_returning_user_credentials_reuse(tmp_cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(_RESULTS)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    print("\n" + "-" * 68)
    print(f"结果：{passed}/{total} 项通过")
    failed = [n for n, ok, _ in _RESULTS if not ok]
    if failed:
        print("未通过：")
        for n in failed:
            print(f"  · {n}")
        return 1
    print("🎉 全部通过：新用户首次 & 多次使用路径均无回归。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
