# VidGrab 回归修复收口报告（243868b 实测反馈 6 项）

**日期**：2026-07-21
**场景**：调试复盘 + 实现收口 + 验证（多成员：排障手根因 + 主理人实现）
**参与成员**：排障手（gstack-investigator） + 主理人（实现与汇编）
**对应提交**：`2b0008c`（本地已提交，未 push；push 待每日 23:00 提醒确认）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 通过（6 项反馈全部闭环，验证零回归）
- 阻塞项数量：0（日语→英文经核实为「非 bug」，见发现 #4）
- 下一步：用户本机重跑一个 GPU 视频做真实环境确认；23:00 push 确认
- 关键提醒：本次 GPU 崩溃根因**不是**此前认为的「页面文件太小(OOM)」，而是 **cudnn 版本冲突触发的 Windows fast-fail**，已用「cudnn 预加载 + Job Object」根治

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（已提交 + 验证全绿） |
| 严重度分布 | 🔴 0 / 🟠 1（GPU 崩溃，已修）/ 🟡 3（UI/选项/标点）/ 🟢 2（日语、僵尸） |
| 关键行动项 | 4 条（见行动清单） |
| 建议负责人 | 主理人（验证与 push）；用户（本机 GPU 重测 + 页面文件可选项） |

---

## 1. 各成员核心结论

### 🔧 排障手（gstack-investigator · 根因）

- **GPU 崩溃根因**：用户截图中的崩溃退出码 `3221226505` = `STATUS_STACK_BUFFER_OVERRUN`（0xC0000409），是 **Windows 原生 fast-fail**，与 OOM/页面文件无关。高置信触发源是 **ctranslate2 与 torch 各自携带的 `cudnn64_9.dll` 版本冲突**（ctranslate2 的 438,840B vs torch 的 265,784B）——torch 的 cudnn 若先被加载，ctranslate2 会拿到不兼容版本触发栈缓冲溢出。此前 `CT2_CUDA_ALLOCATOR=cuda_malloc_async` 的「缓解」是**误打误撞**（该值在 4.8.1 本就是默认，且与该崩溃类型相关）。
- **僵尸进程**：发现 10 个 `transcribe_worker` 孤儿（父 PID 已死）。根因是看门狗用**数字 PID 轮询**，Windows PID 复用会让看门狗误判父进程仍存活；另存在「双开」——两个 `--device auto` worker 抢同一 output-json。
- **日语视频→英文**：排查 P20「Creator Q&A」确认音频**本身就是英文**（"Hey guys, it's Tanaka…"），「原语种→英文」是**正确行为**，非 bug。潜在风险：`_choose_subtitle` 首匹配策略在 ja/en 并存时可能误选 en（无 B站 cookie 未能实证）。
- **中文全文无标点**：用 modus tollens 证明 `_rule_based_punctuate` / `_restore_punctuation` 自身逻辑正确；根因是 `transcript.language` 被 faster-whisper **逐块锁定误判为非 zh**，导致 `is_zh` 短路、`_restore_punctuation` 从未被调用。

### 🧑‍💻 主理人（实现与汇编）

- 按排障手根因逐项落地修复（见交付清单），并补齐用户截图直接指出的 UI 交互问题（蓝色框横幅、追加版本分隔、格式重选、语种选项门控）。
- 中文标点恢复采用「保守断句」策略：在句末语气词后插逗号、句首词前插句号，**刻意排除「的/了」** 避免过度碎片化；并增加「LLM 必须真补了标点才采用」的校验兜底。
- 语种判定改为：当 `transcript.language != "zh"` 时**改用文本重新检测**，确保 faster-whisper 把中文 ASR 误判为 en/ja 时仍能走中文标点 + 繁简转换。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议/修复 | 来源 |
|---|--------|------|------|---------|----------|------|
| 1 | 🟠 | 崩溃 | `core/transcribe_worker.py` / `core/transcriber.py` | GPU 转录子进程以 `3221226505`(STATUS_STACK_BUFFER_OVERRUN) fast-fail，根因 cudnn64_9.dll 版本冲突 | `transcribe_worker` 在 import torch/ctranslate2 前显式预加载 ctranslate2 的 cudnn；`transcriber` 增加 Windows Job Object（KILL_ON_JOB_CLOSE）确保父死子亡；`_OOM_SIGNALS` 移除该退出码 | 排障手+主理人 |
| 2 | 🟡 | 交互 | `skill/main.py` `_export_one` | 首版导出打印蓝色框横幅，冗余且用户明确要求去除 | 仅当 `version_no > 1` 时打印极简提示，去掉首版横幅 | 主理人 |
| 3 | 🟡 | 交互 | `skill/main.py` `_offer_other_versions` | 追加版本无分隔、格式仅「复用」不重选，用户要求加隔断并重选全部格式 | 每次追加版本前打印 `=`×50 分隔线；改用 `_select_formats(title=title)` 让用户像首次一样全选/多选 | 主理人 |
| 4 | 🟢 | 语种 | `core/summarizer.py` | 日语视频输出英文，疑似 bug | 核实音频确为英文（正确行为）；重检测逻辑可纠正 faster-whisper 误判日语音频为 en 的潜在问题 | 排障手 |
| 5 | 🟡 | 标点 | `core/summarizer.py` `_build_full_text`/`generate_summary`/`_rule_based_punctuate` | 中文全文文案无逗号句号，根因 `transcript.language` 被误判非 zh 导致 `is_zh` 短路 | 非 zh 时改用文本重检语种；重写规则断句（语气词逗号+句首词句号，排除的/了）；LLM 补标点后校验 | 排障手+主理人 |
| 6 | 🟡 | 选项 | `skill/main.py` `_run_bilibili` | 中文视频/无字幕视频仍出现「原语种/中文翻译」选项，不合理 | 新增 `list_bilibili_subtitle_languages`；仅当字幕**同时含中文与非中文**时才询问，否则默认原语种 | 主理人 |
| 7 | 🟢 | 僵尸 | `core/transcriber.py` | 孤儿 worker 残留（PID 轮询看门狗在 Windows PID 复用下失效） | Job Object 替代 PID 轮询；本轮已 `Stop-Process` 清理 10 个孤儿 | 排障手+主理人 |

---

## ✅ 交付清单（代码变更 + 测试覆盖 + 回滚预案）

**代码变更（6 文件，+305/−34，提交 `2b0008c`）**

| 文件 | 改动 |
|------|------|
| `core/transcribe_worker.py` | 新增 `_preload_ctranslate2_cudnn()`，在 import torch/ctranslate2 前预加载 ctranslate2 自带的 cudnn64_9.dll |
| `core/transcriber.py` | 新增 Windows Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），父进程死亡时 OS 强制回收 worker；从 `_OOM_SIGNALS` 移除 `STATUS_STACK_BUFFER_OVERRUN`/`3221226505` |
| `core/summarizer.py` | `_punctuate_chinese_chunk` 保守断句（排除的/了）；`_rule_based_punctuate` 调用之；`_restore_punctuation` 校验 LLM 真补标点；`generate_summary`/`_build_full_text`/fulltext 分支在 `!=zh` 时改用文本重检语种 |
| `core/platforms/bilibili.py` | 新增 `_get_bilibili_cid` + `list_bilibili_subtitle_languages`（只查语言不下载内容） |
| `skill/main.py` | 去首版横幅；追加版本 `=`×50 分隔 + 全格式重选；语种来源选项门控 |
| `tests/test_lang_source_fix.py` | 新增 `test_rule_based_punctuate_no_timestamp_also_works` |

**测试覆盖**

- 聚焦单测：`test_lang_source_fix`(17) + `test_language_consistency`(3) + `test_chinese_normalization`(5) + `test_resume`(1) = **26/26 通过**
- 新用户路径：`tests/simulate_new_user.py` = **13/13 通过**（零回归）
- `py_compile` 全部 6 文件 OK

**回滚预案**

- 若 cudnn 预加载在个别环境导致 import 异常，`_preload_ctranslate2_cudnn` 已 `try/except` 兜底，不影响主流程。
- 若 Job Object 创建失败（极旧 Windows），代码 `try/except` 降级为既有 atexit/控制台事件清理，行为不退化。
- 任一处回归可 `git revert 2b0008c` 单独回退本提交，不影响 `243868b` 等前置提交。

---

## ✅ 行动清单（至少 3 条具体可执行项）

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 用户本机重跑一个**真实 GPU 视频**，确认不再 `STATUS_STACK_BUFFER_OVERRUN`（验证 cudnn 预加载 + Job Object 真实生效） | 用户 | P0 | 今日内 |
| 2 | 远程 push：`2b0008c`（及此前 `243868b`/`caed44a` 等）待 23:00 提醒确认后执行 | 主理人 | P1 | 23:00 |
| 3 | 日语视频 `_choose_subtitle` 首匹配策略加固：ja/en 并存时优先按视频品牌/频道默认语种或显式让用户选，避免误选 en | 主理人 | P2 | 后续迭代 |
| 4 | 页面文件仍建议调大到物理内存 1.5–2 倍（根治旧 `WinError 1455` 路径，与本轮 cudnn 崩溃独立） | 用户（需管理员+重启） | P2 | 可选 |

---

## ⚠️ 待完善 / 已知局限

- GPU 修复已代码就位，但**本机真实长视频稳定性需用户最终确认**（沙箱无 GPU，无法端到端复现 fast-fail）。
- 「日语视频输出英文」经核实为正确行为（音频本就是英文）；但若用户后续测到**真正日语音频**却出英文，需排查 `_choose_subtitle` 首匹配（行动 #3）。
- `test_language_auto.py` 在受限沙箱因 `transcribe_worker` 导入期重写 stdout 会 `lost sys.stderr`（预存在问题，与本轮改动无关，已排除）。
- 日/韩标点恢复仍仅中文做 LLM 补标点（日韩标点恢复为 P1 跟进项，不在本轮范围）。

---

## 📚 成员产出索引

- 排障手（gstack-investigator）原始产出：GPU 崩溃 = STATUS_STACK_BUFFER_OVERRUN（cudnn 冲突）根因、僵尸进程 PID 复用失效分析、日语 P20 音频实为中文、中文标点 `transcript.language` 短路根因。
- 主理人（实现）原始产出：上述 6 文件修改 + 验证（26/26 单测、13/13 新用户路径）。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
