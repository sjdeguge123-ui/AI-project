# VidGrab 语种信号透传修复 — 独立验证收口报告

**日期**：2026-07-21
**场景**：调试复盘 + QA 独立验证（多语种一致性 P0 修复）
**参与成员**：产品评审员（gstack-product-reviewer）+ 质量门神（gstack-qa-lead）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **通过（无功能回归）**
- 阻塞项数量：**0**（唯一已知失败为预存在环境性问题，与本次改动无关）
- 核心修复：commit `f3d25ea`「透传权威语种信号」——删除了 `transcriber.py` 用文本启发式覆盖 whisper/B站权威语种的逻辑，改为从入口透传 `info.language` / `lan`，根治纯汉字日语/韩语视频被误判为 zh/en。
- 验证：独立 QA 重跑编译 + 4 项新增/既有功能测试 + `simulate_new_user` 全绿（13/13）；**第二独立验证（`-s` 关闭 capture）下全套 `pytest` 31 passed / 0 failed 全绿**；默认 capture 下的崩溃是**预存在环境性副作用**（与本次改动无关，父提交同样复现）。
- 下一步：本地 commit 收口 → 23:00 提醒 push 确认 → 用户本机真实环境验证日韩/繁体视频 + 重启清理历史僵尸进程。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（可发布，附带 1 条 P1 健壮性跟进） |
| 严重度分布 | 🔴 0 / 🟠 0 / 🟡 1（预存在环境失败，非回归）/ 🟢 通过 |
| 关键行动项 | 4 条（见下方行动清单） |
| 建议负责人 | 主理人（收口/commit）+ 用户（真实环境验证/重启） |

---

## 1. 各成员核心结论

### 🔍 产品官（产品评审）
- 核心判断：原实现在 `transcriber.py` 把 whisper / B站返回的权威语种用文本启发式 `_detect_language` **覆盖**掉了，导致「纯汉字日语/韩语」视频被误判为 zh/en，违反用户硬性要求「视频内容语种必须跟随原视频」。
- 关键建议（P0）：信任权威信号——删掉覆盖逻辑，把 `faster-whisper info.language` 与 B站字幕 `lan` 从入口一路透传到 `summarizer`，仅在信号缺失时回退文本判定。该 P0 已在 `f3d25ea` 完整落实。

### ✅ 质量门神（QA 测试与发布）
- 核心判断：**`f3d25ea` 未引入任何功能回归**。编译全过；`test_whisper_lang_passthrough` 4/4（ja/ko/en 透传 + `.lang` side-file 读取）、`test_resume` 1/1、`test_language_consistency` 3/3、`test_chinese_normalization` 5/5、`simulate_new_user` 13/13 全绿。
- 关键建议：全套 `pytest` 在**默认 capture 下**于 teardown 崩（`lost sys.stderr` / I/O on closed file），根因是 `transcribe_worker.py` 在**模块导入时**重写 `sys.stdout/stderr`，与受限沙箱 pytest 捕获冲突；但加 `-s`（关闭 capture）后**全套 31 passed / 0 failed 全绿**。该崩在父提交 `39ab41b`（核心源码回退）同样复现，属**预存在环境副作用**（非回归），且除 `test_language_auto.py` 外还拖垮 `test_issue_fixes::test_select_formats_all`。建议为导入期 stdout/stderr 重写加护栏（仅非 pytest/子进程模式执行，或 `try-except` 包裹）。

> 本场景未调度安全官 / 设计师 / 排障手，故不列。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🟡 | 测试环境/健壮性 | `core/transcribe_worker.py` L28–33（导入期） | 导入时无条件重写 `sys.stdout/stderr`，导致本沙箱**默认 capture 下**全套 `pytest` teardown 崩溃，并令 `test_language_auto.py` 与 `test_issue_fixes::test_select_formats_all` 失败；加 `-s` 后全套 **31 passed / 0 failed 全绿**。该崩在父提交 `39ab41b`（核心源码回退）同样复现，**非本次回归**。 | 加护栏：仅当非 pytest 且为子进程模式时重写，或用 `try-except` 包裹，恢复默认 capture 下全套可跑性。 | 质量门神 |
| 2 | 🟢 | 功能（已修复） | `core/transcriber.py` / `core/platforms/bilibili.py` / `core/summarizer.py` | 纯汉字日/韩视频语种被文本启发式误判。已通过透传 `info.language` / `lan` 修复，新增 `test_whisper_lang_passthrough` 覆盖 ja/ko/en 三路径。 | 无需动作；真实环境复跑确认即可。 | 产品官 + 质量门神 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 本地 commit 收口：本报告落盘 + 关联改动已提交 `f3d25ea`；待 23:00 提醒统一 push 确认。 | 主理人 / 用户 | P0 | 本日内 |
| 2 | 为 `transcribe_worker.py` 导入期 `sys.stdout/stderr` 重写加护栏，恢复本沙箱全套 `pytest` 可跑性（同时解锁 `test_language_auto.py` / `test_issue_fixes` 的 CI 价值）。 | 主理人 | P1 | 下个开发会话 |
| 3 | 真实环境验证：用「纯汉字日语 / 韩语」视频 + 繁体中文视频各跑一次完整链路，确认摘要/全文语种跟随正确、中文为简体+合理标点。 | 用户（本机） | P2 | 有空即验 |
| 4 | 重启清理历史「PM=0 僵尸进程」；确认代码化根治（atexit+看门狗）后不再产生新孤儿。 | 用户（本机） | P2 | 有空即验 |

---

## ⚠️ 待完善 / 已知局限

- **本沙箱默认 capture 下无法整体跑全套 pytest**（teardown 崩，环境副作用）；但加 `-s` 可整体跑且 **31 passed / 0 failed 全绿**，已实质确认无回归。真实长音频续传路径仍建议用户在真实环境最终确认。
- **API 路径（云端 Whisper）仍用文本启发式 `_detect_language`** 判定语种——仅 local 路径信任透传信号。云端不返回 `info.language` 时启发式是合理 fallback，且 `test_language_consistency` 已覆盖；但与「信任权威信号」原则在 API 路径上未完全对齐，后续若云端支持语种返回可进一步统一。
- 日文/韩文字幕的标点恢复（`_restore_punctuation`）在 `is_zh` 守卫下不会对 ja/ko 套用中文标点，符合预期。
- `test_language_auto.py` / `test_issue_fixes::test_select_formats_all` 的失败**不代表代码缺陷**，仅沙箱导入副作用；修复项 2 落地后即恢复。

---

## 📚 成员产出索引

- gstack-product-reviewer（产品官）原始产出：P0 评估「信任真实语种信号」——识别 `transcriber.py:169` 覆盖逻辑为根因，要求透传 `lan`/`info.language`（上轮会话产出）。
- gstack-qa-lead（质量门神）原始产出：本次独立验证报告（见上方 teammate-message 全文）——编译通过、功能测试全绿、全套崩溃为预存在环境失败（已 checkout 父提交复现确认）。
- gstack-qa-lead-3（质量门神·复核）：第二独立验证，结论与 qa-lead-2 一致（**NO regression, GO**）；并实测 `-s` 下全套 `pytest` **31 passed / 0 failed 全绿**，且预存在崩在父提交核心源码回退后同样复现。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
