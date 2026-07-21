# VidGrab 语种错乱与交互问题 · 收口报告

**日期**：2026-07-21
**场景**：调试复盘 + 全流程交付（根因分析 → 实现 → 独立验证）
**参与成员**：排障手（Investigator，根因分析）、主理人（实现 + 收口验证）、质量门神（QA Lead，独立验证派发）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **通过（已修复并验证，可发布）** —— 用户报告的 4 个问题全部修复，commit `243868b`。
- 阻塞项数量：0（纯代码/逻辑修复，无部署阻塞）。
- 核心结论：原 4 问题已按 P0→P1→P2 全部落地：
  - **P0 语种信号统一 + 原语种/翻译选项**：B站字幕选择改为「原始非机翻优先」，`_map_bili_lang` 加「元数据 × 文本」交叉校验纠正 B站错标，新增「原语种 / 中文翻译」交互选项，`lang_source` 透传全链路，摘要与全文强制同源。
  - **P1 标点鲁棒性**：`_restore_punctuation` 接入退避重试，失败用规则断句确定性兜底，全文 `is_zh` 门槛对齐。
  - **P2 导出分隔**：追加导出前提示复用/改格式，`_export_one` 加版本分隔横幅。
- 验证：编译通过；新增 `test_lang_source_fix.py` **16/16**；既有相关测试 **13/13**；`simulate_new_user` **13/13**。
- 下一步：真实环境用 Stanford（英文）/ 日语播客 / 韩文视频各跑一次验证语种与标点；23:00 push 确认。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 **Go**（修复完成，零回归，可发布） |
| 严重度分布（修复前） | 🔴 2（问题 3、4 语种信号系统性缺陷） / 🟠 1（问题 1 标点丢失） / 🟡 1（问题 2 UI 体验） |
| 关键行动项 | 3 条（P0 语种信号+选项 / P1 标点兜底 / P2 导出分隔）全部完成 |
| 建议负责人 | 主理人已实现并验证；QA Lead 独立复验已派发 |

---

## 1. 各成员核心结论

### 🔧 排障手（调试与根因）
- 核心判断：4 个问题用**真实产物 + 日志**坐实。问题 3（英文视频→中文）与问题 4（日语视频摘要/全文语种分裂）同源——根在「B站字幕盲目优先 zh + `_map_bili_lang` 盲信 ja/ko/en 元数据 + 摘要/全文对 `transcript.language` 消费口径不一致」。问题 1（全文无标点）主因是补标点 LLM 调用偶发失败被静默吞掉。问题 2（追加导出无提示/无分隔）是独立 UI 缺陷。
- 关键证据：`output/20260721/` 中 Stanford P70 全文为中文（英文课被 ai-zh 机翻主导）；日语播客 P22 摘要含 Hangul（韩文）、全文为中文（语种分裂）；日志均显示「有字幕，跳过转录」→ 走字幕路径。
- 关键建议：P0 改字幕策略 + `_map_bili_lang` 文本交叉校验 + 统一 canonical lang；P1 标点重试+确定性兜底；P2 格式复用提示+分隔横幅。

### 🛠️ 主理人（实现 + 收口验证）
- 核心判断：排障手根因成立，全部采纳。实现时把「用户想要中文翻译版」诉求前置为**交互选项**（`lang_source`：原语种/中文翻译），而非硬编码，满足用户 7/20「可以让用户选择是原语种还是翻译」的明确要求。
- 关键改动（commit `243868b`，6 文件）：
  - `core/lang.py`：`_detect_language` 拆出 `_detect_language_of(text)`；`_map_bili_lang(lan, text="")` 对 ja/ko/en 与文本检测交叉校验。
  - `core/platforms/bilibili.py`：新增 `_is_machine_translation` / `_choose_subtitle`（original 优先非机翻非 zh，chinese 优先 zh，均带降级告警）；`_fetch_bilibili_subtitle(sub_info, workdir, lang_source)` 调 `_choose_subtitle` + 文本交叉校验，返回 `(segments, lang)`（修原 `return []` 丢 lang 的潜在 bug）。
  - `core/extractor.py` / `core/summarizer.py`：`extract` 透传 `lang_source`；全文 `is_zh` 门槛对齐 `(lang or _detect_language)==zh`。
  - `skill/main.py`：新增 `_select_language_source`（交互 1=原语种默认 / 2=中文翻译，非 TTY 回 original）；`_offer_other_versions` 加版本号 + 复用/改格式提示；`_export_one` 加版本分隔横幅；`_restore_punctuation` 重试 3 次退避 + 失败 `_rule_based_punctuate` 兜底。
- 验证结果（亲自跑，非盖章）：编译 OK；`test_lang_source_fix.py` **16/16**；`test_whisper_lang_passthrough`+`test_language_consistency`+`test_chinese_normalization`+`test_resume` **13/13**；`simulate_new_user` **13/13**。

### ✅ 质量门神（QA Lead，独立验证派发）
- 核心判断：QA Lead 任务已派发并标记完成，但其自动化运行在等待后台全套 pytest 时未回写最终书面结论（仅留中间态「等待后台运行完成」）。主理人据此按团队铁律「后台任务必须真正追结果」**不采信中间态**，改为亲自执行等价验证并取得绿证（见上）。
- 关键建议（沿用派发时的验证口径）：真实环境仍需对英文/日文/韩文视频做端到端确认；本沙箱预存在环境失败 `test_language_auto.py`（transcribe_worker 导入期重写 stdout/stderr，自父提交 `39ab41b` 起，非本次回归）应单独跑或排除。

> 仅排障手产出完整书面根因；实现与验证由主理人收口；QA Lead 自动化任务完成但未回写终态，已据铁律以主理人亲验替代。

---

## 2. 综合审查发现（根因 + 修复对照，按严重度）

| # | 严重度 | 类别 | 位置 | 问题描述 | 修复（已落地 commit 243868b） | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🔴 | 语种/字幕 | `core/platforms/bilibili.py:236` | 字幕选择盲目优先含 "zh" 字幕（含 `ai-zh` 机翻），外语视频被中文机翻主导。 | `_choose_subtitle`：original 模式优先「原始非机翻非 zh」字幕，ai-zh 降级兜底并 print 告警；chinese 模式优先 zh。 | 排障手 |
| 2 | 🔴 | 语种/信号 | `core/lang.py` (`_map_bili_lang`) | 对 ja/ko/en 元数据盲信，不交叉校验文本，B站错标 ko→摘要韩文/全文中文。 | `_map_bili_lang(lan, text)` 对 ja/ko/en 用 `_detect_language_of(text)` 校验；文本实为中文则回落 `zh`/`""`。 | 排障手 |
| 3 | 🔴 | 语种/一致性 | `core/summarizer.py` + `bilibili.py` | 摘要用 `transcript.language or _detect_language`，全文仅用 `transcript.language` 控繁简 → 信号不一致时语种分裂。 | f3d25ea 已统一 `transcript.language` 透传；本次新增 `lang_source` 选项贯穿 `extract`→`extract_bilibili`→`_fetch_bilibili_subtitle`，摘要/全文同源。 | 排障手 |
| 4 | 🟠 | 鲁棒性 | `core/summarizer.py` (`_restore_punctuation`) | 补标点单次 LLM 调用，失败被 `except` 静默吞掉 → 无标点（「简体做到、标点没有」）。 | 接入 3 次退避重试（429/503 `(attempt+1)*5s`）；最终失败返回 `_rule_based_punctuate(text)` 确定性兜底。 | 排障手 |
| 5 | 🟠 | 守卫不一致 | `core/summarizer.py` | `_build_full_text` `is_zh=(lang or _detect)==zh`，`_restore_punctuation` 门槛严格 `==zh` → 口径不一致。 | 全文分支统一 `is_zh=(transcript.language or _detect_language(segments))=="zh"`，两处共用。 | 排障手 |
| 6 | 🟡 | UI/流程 | `skill/main.py` (`_offer_other_versions`/`_export_one`) | 追加导出不提示格式、版本间无分隔。 | `_offer_other_versions` 加版本号 + 「复用格式 x,y（回车沿用/输入新格式可改）」提示（30s 超时）；`_export_one` 开头打印 `─`×50 版本分隔横幅。 | 排障手 |

### 交付清单（代码变更 + 测试覆盖 + 发布检查 + 回滚预案）

**代码变更（commit `243868b`，6 文件）**
- `core/lang.py`：`_detect_language_of` / `_detect_language` 拆分；`_map_bili_lang(lan, text)` 交叉校验。
- `core/platforms/bilibili.py`：`_is_machine_translation` / `_choose_subtitle` / `_fetch_bilibili_subtitle(sub_info, workdir, lang_source)` 返回 `(segments, lang)`。
- `core/extractor.py`：`extract(..., lang_source="original")`。
- `core/summarizer.py`：全文 `is_zh` 对齐；`_restore_punctuation` 重试+兜底；`_rule_based_punctuate` 新增。
- `skill/main.py`：`_select_language_source` / 版本分隔横幅 / 复用格式提示。
- `tests/test_lang_source_fix.py`：新增 16 用例。

**测试覆盖**
- 新增 16 用例：覆盖 `_map_bili_lang` 6 种交叉校验、`_choose_subtitle` 选择/降级、`_is_machine_translation`、`_rule_based_punctuate`、`_restore_punctuation` 兜底与透传。
- 回归：既有 `test_whisper_lang_passthrough`(4) + `test_language_consistency`(3) + `test_chinese_normalization`(5) + `test_resume`(1) = 13/13。
- 新用户路径：`simulate_new_user.py` 13/13。

**发布检查**
- `py_compile` 全部变更文件 OK。
- 无功能回归（摘要/全文同源、标点兜底、导出分隔均经测试与模拟路径验证）。

**回滚预案**
- 单 commit `243868b`，如需回退直接 `git revert 243868b` 即可；`lang_source` 默认 `"original"` 与旧行为（跟随原视频语种）一致，不影响既有用户配置。

---

## ✅ 行动清单（已全部完成）

| # | 行动 | 负责方 | 紧急度 | 状态 |
|---|------|--------|--------|------|
| 1 | P0 语种信号统一 + 「原语种/中文翻译」选项（`lang_source` 透传全链路，字幕策略+交叉校验） | 主理人 | P0 | ✅ 完成 `243868b` |
| 2 | P1 标点鲁棒性（重试退避 + `_rule_based_punctuate` 兜底 + `is_zh` 门槛对齐） | 主理人 | P1 | ✅ 完成 `243868b` |
| 3 | P2 导出分隔（版本横幅 + 复用/改格式提示） | 主理人 | P2 | ✅ 完成 `243868b` |
| 4 | 验证（新增 16 用例 + 回归 13/13 + 新用户 13/13） | 主理人/QA | P0-后 | ✅ 完成，零回归 |

---

## ⚠️ 待完善 / 已知局限

- **真实环境端到端确认**：沙箱无真实 faster-whisper 长音频，Stanford（英文）、日语播客 P22、韩文视频的「原语种」输出需在用户真实环境各跑一次，确认摘要/全文一致 + 标点正常。
- **日/韩标点恢复**：当前仅中文做 LLM 标点恢复；日/韩全文仍靠字幕自带标点（若有）。后续可扩展 `_restore_punctuation` 支持 ja/ko（P1 跟进项）。
- **预存在环境失败（非回归）**：`tests/test_language_auto.py` 在本沙箱因 `core/transcribe_worker.py` 导入期重写 `sys.stdout/stderr` 失败（`lost sys.stderr`），自父提交 `39ab41b` 起存在，验证时排除或单独跑；`git stash` 回原代码同失败，确认非本次引入。
- **QA Lead 终态缺口**：QA Lead 自动化任务完成但未回写书面终态结论，主理人已按铁律以亲验替代；后续建议给验证 agent 明确「必须回写最终报告」的收口约束。

---

## 📚 成员产出索引

- gstack-investigator（排障手）原始产出：独立根因分析（4 问题根因 + 代码行号 + 修复思路 + 验证方式），基于 `output/20260721/` 真实产物与 `logs/`。
- 主理人实现产出：commit `243868b`（P0/P1/P2 全部修复 + 16 新增测试）。
- gstack-qa-lead（质量门神）派发记录：独立验证任务已派发并标记完成（中间态），等价验证由主理人亲自执行并取得绿证。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
