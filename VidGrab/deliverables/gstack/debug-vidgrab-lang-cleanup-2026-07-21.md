# VidGrab 调试复盘 · 中文繁体标点 + 僵尸进程 + 多语种一致性

**日期**：2026-07-21
**场景**：调试复盘 / 代码修复 / QA 回归
**参与成员**：gstack-investigator（调查员）+ gstack-qa-lead（QA）+ 主理人（产品/工程收口）

---

## 📌 TL;DR
- 整体结论：🟢 已修复并验证（中文繁体标点、僵尸进程、多语种检测扩展）
- 阻塞项数量：1（当前本机 PM=0 僵尸进程需重启才能彻底清理；新代码可防止新增）
- 下一步：用户重启电脑清理残留僵尸；真实环境重跑繁体/日文/韩文视频验证；每日 23:00 确认是否 push

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（代码已修复、测试全绿） |
| 严重度分布 | 🔴 0 / 🟠 1（僵尸需重启清） / 🟡 0 / 🟢 其余 |
| 关键行动项 | 4 条 |
| 建议负责人 | 主理人（已收口） |

---

## 1. 各成员核心结论

### 🔍 调查员（gstack-investigator）
- **核心判断**：
  - 中文全文文案「繁体、无标点」根因 = 全链路无繁→简归一化，且唯一标点恢复 `_restore_punctuation` 仅在 fulltext 模式触发、提示禁止改字、失败静默回退。截图视频字幕源为繁体+无标点，被原样暴露。
  - 僵尸进程根因 = Windows 不级联杀子进程；VidGrab 原有 atexit/控制台事件/父进程看门狗都假设 skill 主进程存活或干净退出，无法抵御「主进程被外部强杀/终止」；且看门狗原先用 `sys.stdin.isatty()` 判断，WorkBuddy agent 启动时 stdin 也可能是 tty，导致未启用。
- **关键建议**：
  - 在 pipeline 入口（字幕解析后、转录返回前）统一做繁→简归一化（opencc）。
  - 用 Windows Job Object（KillOnJobClose）兜底子进程生命周期，并把 worker 跟踪从单槽改集合。

### ✅ QA（gstack-qa-lead）
- **核心判断**：
  - 问题 1 必须先落地确定性繁简转换层，才能写硬断言；已有标点不应重复加标点；失败应兜底不中断主流程。
  - 问题 2 应在单测覆盖 atexit/看门狗/记录清空，并新增端到端进程扫描脚本 `check_no_orphan_workers.py`。
  - 问题 3 当前 `_detect_language` 仅 zh/en，ja/ko 被误判为 en；目标状态应扩展为字符脚本判定（假名→ja、Hangul→ko），并补齐 `_language_instruction` 的 ja/ko 分支。
- **关键建议**：
  - 新增 `tests/test_fulltext_norm.py`、`tests/test_multilingual.py`、`tests/check_no_orphan_workers.py`。
  - T2S 必须仅 `language=="zh"` 触发，同批用例需同时断言「繁体 zh→简体」正例与「ja/ko 不被误简化」负例。

### 🎯 主理人（产品/工程收口）
- **核心判断**：
  - 用户「视频内容跟随听到的语种」诉求合理，且对国内 B站用户常见的中/英/日/韩视频都应成立。
  - 本次优先落地确定性工程修复（繁简转换前置 + 看门狗无条件启用 + worker 父进程死亡自退出 + ja/ko 检测/指令），Windows Job Object 作为更强兜底可后续评估。

---

## 2. 综合审查发现（按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🟠 | 稳定性 | `skill/main.py` 看门狗原 `sys.stdin.isatty()` 判断 | agent 启动时 stdin 可能为 tty，看门狗未启用，主进程被杀后 worker 残留 | Windows 上无条件启用看门狗；worker 自带父进程死亡检测 | investigator + 主理人 |
| 2 | 🟡 | 功能 | `core/summarizer.py` `_restore_punctuation` | 仅 fulltext 触发、提示禁止改字、失败静默回退；无繁简转换 | 在 pipeline 入口用 opencc 做繁简归一化，并保留本地兜底标点恢复 | investigator |
| 3 | 🟡 | 功能 | `core/lang.py` `_detect_language` | 仅支持 zh/en，ja/ko 视频语种不一致 | 扩展为字符脚本判定（假名→ja、Hangul→ko）并补齐语言指令 | qa-lead + 主理人 |
| 4 | 🟢 | 可观测性 | `_restore_punctuation` / worker 清理 | 失败/清理路径无日志 | 后续增加一行日志/告警，便于排查 | investigator + qa-lead |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 重启用户电脑，清理当前 PM=0 僵尸进程 | 用户 | P0 | 立即 |
| 2 | 真实环境重跑繁体无标点子字幕视频，确认输出简体+标点 | 主理人/用户 | P0 | 重启后 |
| 3 | 真实环境测试日文/韩文视频，确认摘要=全文=原语种 | 主理人/用户 | P1 | 重启后 |
| 4 | 评估 Windows Job Object（KillOnJobClose）作为更强进程生命周期兜底 | 主理人 | P2 | 后续迭代 |
| 5 | 每日 23:00 确认当日改动并决定是否 push | 自动化+用户 | P1 | 当日 23:00 |

---

## ⚠️ 待完善 / 已知局限

- **当前本机僵尸**：已用 `Stop-Process` 尝试清理，但 PM=0 的僵尸对象仍留在进程表中（父进程已不存在，Windows 尚未 reaping）。它们不占用内存，但需要**重启电脑**才能从进程表中彻底消失。
- **日文/韩文标点恢复**：当前 `_restore_punctuation` 仅对 `language=="zh"` 触发，ja/ko 全文文案暂不做自动标点恢复。属 P1 待完善。
- **Windows Job Object**：investigator 建议的「抗 SIGKILL」最强方案尚未实现；当前方案（看门狗无条件启用 + worker 父进程死亡自退出）已能覆盖绝大多数 agent/detached 场景。
- **远端 push**：按 Git 工作流，本次所有 commit 仍在本地，需等每日 23:00 自动化提醒用户确认后 push。

---

## 📚 成员产出索引

- gstack-investigator 原始产出：`deliverables/gstack/investigation-lang-cleanup-zombies-2026-07-21.md`
- gstack-qa-lead 原始产出：`deliverables/gstack/qa-regression-plan-3issues.md`
- gstack-product-reviewer：任务「评估 VidGrab 语种策略（日/韩/多语种）」已派发并完成评估结论，结论已内化到本次实现（`core/lang.py` 字符脚本判定 + `core/summarizer.py` ja/ko 分支），未单独落盘文件。

---

## 交付清单

- 代码变更：
  - `core/lang.py`：`_detect_language` 扩展 ja/ko；`_normalize_chinese` 繁简转换。
  - `core/platforms/bilibili.py`：字幕解析后繁简转换。
  - `core/transcriber.py`：转录返回前繁简转换；worker 启动传 `--parent-pid`。
  - `core/transcribe_worker.py`：父进程死亡自退出。
  - `core/summarizer.py`：`_build_full_text`/`_build_chunk_text`/`_restore_punctuation` 繁简规范化；`_language_instruction` 补齐 ja/ko。
  - `skill/main.py`：自动安装 opencc；看门狗无条件启用。
  - `tests/test_chinese_normalization.py`、`tests/test_multilingual.py`、`tests/test_issue_fixes.py`。
- 测试：py_compile OK；7 个测试文件合计 25 passed；`simulate_new_user` 13/13。
- 提交：`736c9fa` + `39ab41b`。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
