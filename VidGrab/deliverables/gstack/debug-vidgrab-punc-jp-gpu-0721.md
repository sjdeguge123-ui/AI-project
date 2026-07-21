# VidGrab 三问题回归收口报告（标点 / GPU 日志 / 日语语种）

**日期**：2026-07-21
**场景**：调试复盘（多成员协作：根因调查 + 主理人加固）
**参与成员**：排障手（gstack-investigator）+ 主理人（SoftwareWorkshop CEO，负责加固编码与验证）

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟢 Go（三项用户反馈均已缓解，残留风险已登记为后续项）
- **阻塞项数量**：0（无阻塞；用户本机复测为验证动作，非阻塞）
- **核心修复**：commit `65bbe5c`（本地已提交，待 23:00 push 确认）
  1. 中文标点：LLM 稀疏标点（密度 <0.003）不再被误判为"已恢复"，强制走确定性规则兜底。
  2. GPU 崩溃：CPU 兜底前打印子进程退出码 + 分类原因（cudnn 冲突），可一眼识别"真错误 vs 误报"。
  3. 日语视频：确认 `ai-zh` 来自 SESSDATA cookie 的中文机翻，降级分支已提示用 `--audio` 强制音频转录拿真实语种。
- **下一步**：用户本机复测两条视频；23:00 确认 push（含 `65bbe5c` + 前序 `2b0008c`/`80a5a83` + 本报告）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go |
| 严重度分布 | 🔴 0 / 🟠 2 / 🟡 2 / 🟢 0 |
| 关键行动项 | 4 条（2 条用户复测 P0、1 条 push P1、1 条代码 follow-up P2） |
| 建议负责人 | 用户（复测）、主理人（follow-up 前置密度闸门） |

---

## 1. 各成员核心结论

### 🔧 排障手（gstack-investigator，根因调查）
- **问题1（中文标点）定性**：属于"校验逻辑缺陷"，不是 LLM 行为问题。旧验收闸门 `if result and not _needs_punctuation(result)` 只要结果含任意 1 个 CJK 标点就放行，LLM 仅补 25 个标点（13,778 字，密度 0.00181）也被当"已恢复"，长文本仍无标点。密度闸门 `0.003` 已锁死该行为。
- **问题2（GPU 崩溃）定性**：是"真错误"（fast-fail），不是打印错误。退出码 `3221226505 / 0xC0000409`（STATUS_STACK_BUFFER_OVERRUN），根因是 ctranslate2 与 torch 自带 `cudnn64_9.dll` 版本冲突（依赖/ABI 冲突，非 VidGrab 代码 bug）。CPU 兜底属预期降级。新日志已带退出码与分类。
- **问题3（日语输出中文）定性**：根因链确认无误。该 B 站 P25 仅挂 `ai-zh`（SESSDATA cookie 提供的中文机翻），`_choose_subtitle("original")` 找不到原语种字幕 → 降级选 `ai-zh` 置 `degraded=True` → `_detect_language` 判定 `zh`。真实修复能力是既有 `--audio` 开关（本轮回写提示语引导）。

### 🧑‍💼 主理人（加固编码 + 验证）
- 在 `65bbe5c` 落地三处加固并验证：4 文件 `py_compile` 通过；聚焦测试 `test_lang_source_fix.py` 由 17→19（新增 2 条稀疏标点用例，全仓聚焦 28/28）；`tests/simulate_new_user.py` 13/13。
- 调查员在复核中发现一处**残留风险**（见综合发现 #4）：输入守卫 `_needs_punctuation`（:285）仍是"存在任意标点即放行"的 presence 逻辑，若字幕源视频原始带零星标点会在此短路、跳过恢复。本次回归未改，登记为 follow-up。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🟠 | 正确性 | `core/summarizer.py:439`（旧闸门） | LLM 补稀疏标点（密度 <0.003）被误判"已恢复"，全文文案长段无标点 | 改用 `_is_well_punctuated`（阈值 0.003），不达标强制 `_rule_based_punctuate` | 排障手 |
| 2 | 🟠 | 正确性 | `core/platforms/bilibili.py` 降级分支 + `core/lang.py` | 无公开字幕时降级选中 `ai-zh` 中文机翻，`_detect_language` 误判 `zh`，日语视频出中文稿 | 用户应加 `--audio` 强制音频转录；降级分支已补充该提示 | 排障手 |
| 3 | 🟡 | 可观测性 | `core/transcriber.py`（CPU 兜底分支） | GPU 子进程崩溃后仅打印笼统"已崩溃"，无退出码/原因，易被误认为误报 | 新增 `_classify_worker_exit(rc, child_text)`（:686），打印退出码 + 分类（cudnn 冲突/页面文件/MKL/cuBLAS） | 排障手 |
| 4 | 🟡 | 残留风险 | `core/summarizer.py:285` `_needs_punctuation` | 输入守卫仅校验"存在任意标点即放行"，字幕源视频带零星标点会在此短路、跳过恢复 | 将密度闸门同样前置到输入守卫（follow-up，非本次必改） | 排障手 |

> 问题 1/2/3 已在 `65bbe5c` 修复并验证；问题 4 为已知局限，登记后续项。

---

## ✅ 交付清单（代码变更 + 测试覆盖 + 发布检查 + 回滚预案）

**代码变更（commit `65bbe5c`）**
- `core/summarizer.py`：新增 `_punctuation_density`（:291）、`_is_well_punctuated`（:299，阈值 0.003）；`_restore_punctuation`（:400）验收闸门由 `not _needs_punctuation(result)` 改为 `_is_well_punctuated(result)`，密度不达标强制规则兜底。
- `core/transcriber.py`：新增 `_classify_worker_exit(rc, child_text)`（:686），CPU 兜底分支（:487）打印退出码 + 分类原因。
- `core/platforms/bilibili.py`：降级分支（:341）追加"或使用 `--audio` 强制音频转录（会下载音轨并用 faster-whisper 识别真实语种）"提示。

**测试覆盖**
- `tests/test_lang_source_fix.py` 新增 `test_is_well_punctuated_rejects_sparse_punctuation`、`test_restore_punctuation_fallback_on_sparse_llm_result`（用例 17→19）；全仓聚焦测试 28/28。
- `tests/simulate_new_user.py` 13/13（新用户路径无回归）。

**发布检查清单**
- [x] 4 文件 `py_compile` 通过
- [x] 聚焦单测 28/28、新用户模拟 13/13
- [x] 本地 commit `65bbe5c`
- [ ] 用户本机复测（见行动清单 #1/#2）
- [ ] 23:00 push 确认

**回滚预案**
- `git revert 65bbe5c`（仅影响标点兜底/崩溃日志/提示语，无数据模型变更，可安全回滚）

---

## ✅ 行动清单（至少 3 条具体可执行项）

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 本机复测 `BV1Uy7V6hEc5` 全文文案，确认标点（逗号/句号）已恢复 | 用户 | P0 | 今日 |
| 2 | 本机复测 `BV1xggEeLEyV?p=25 --audio`，确认输出日语而非中文 | 用户 | P0 | 今日 |
| 3 | 23:00 确认 push（含 `65bbe5c` + 前序 `2b0008c`/`80a5a83` + 本报告） | 用户 | P1 | 今日 23:00 |
| 4 | 将密度闸门前置到 `_needs_punctuation`（:285），消除字幕源稀疏标点短路残留风险 | 主理人 | P2 | 下一轮 |

---

## ⚠️ 待完善 / 已知局限

- **输入守卫残留短路**（问题 #4）：仅当视频走"字幕源且原始带零星标点"时才会触发，本次用户反馈的 ASR 视频不受影响；列为 P2 follow-up。
- **GPU cudnn 冲突**：cudnn64_9.dll 版本冲突为环境级依赖问题，非代码可修；根治需统一 ctranslate2/torch 的 cudnn 版本或等上游修复，当前 CPU 兜底为稳妥降级路径。
- **`ai-zh` 语种误判**：在没有原语种字幕、仅有 cookie 机翻时，自动路径无法获知真实语种；最可靠解法始终是用户显式 `--audio`。

---

## 📚 成员产出索引

- gstack-investigator（排障手）原始产出：三项根因结论（含文件:行号、commit `65bbe5c`、产物实测密度 0.00181 / 汉字占比 87.2% 假名 0.47%）。
- 主理人（SoftwareWorkshop CEO）产出：commit `65bbe5c` 三处加固代码 + 验证（py_compile / 聚焦测试 28/28 / simulate_new_user 13/13）+ 本收口报告。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
