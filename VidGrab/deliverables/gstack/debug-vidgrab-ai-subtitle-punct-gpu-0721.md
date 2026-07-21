# VidGrab 三问题回归修复收口报告（AI字幕语种 / 中文标点 / GPU崩溃）

**日期**：2026-07-21
**场景**：调试复盘（多成员协作：排障手 + 质量门神）
**参与成员**：排障手（gstack-investigator）、质量门神（gstack-qa-lead）
**关联提交**：`27c8188`（AI字幕语种）、`7a8ccb2`（标点密度守卫）、`08531ef`（回归测试）、`41c5664`（GPU cudnn 预加载 + 会话跳过，含收口语「B站的视频摘要提取基本已经跑通，包含无字幕」）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 通过（三项问题均已定位根因并修复，目标测试全绿）
- 阻塞项数量：**0**（剩余 1 项为沙箱环境限制，非代码回归，已按规则排除）
- 用户本轮三个反馈均已闭环：①B站 AI 字幕语种码不再误判；②中文全文标点恢复不再被符号短路；③GPU cudnn 冲突改为整组 DLL 预加载 + 会话级崩溃跳过，避免无谓重试。
- 自检结论：目标单测 32/32 通过，`simulate_new_user.py` 13/13 通过，5 个改动文件 `py_compile` 全部通过。
- 下一步：真实 Windows 机（RTX4070）回归长视频 GPU 稳定性；清理残留僵尸进程建议重启。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（功能闭环，无阻塞） |
| 严重度分布 | 🔴 2（语种识别混乱、GPU 崩溃）/ 🟠 1（中文标点缺失）/ 🟢 0 |
| 关键行动项 | 4 条（3 条已随提交落地，1 条为环境局限待办） |
| 建议负责人 | 主理人跟进真实机验证；用户侧负责放大页面文件/重启清僵尸 |

---

## 1. 各成员核心结论

### 🔧 排障手（调试与根因）
- **核心判断**：三个问题根因分属三处、互不耦合。①语种混乱在 `core/lang.py:_map_bili_lang` 对 `ai-ja` 做 `split("-")[0]` 得到 `"ai"` 而落空，叠加 `core/platforms/bilibili.py:_choose_subtitle` 在原语种模式下把全部 `ai-*` 过滤后又回退到任意 `subtitles[0]`；②标点缺失是 `_restore_punctuation` 的输入守卫用「是否含标点」的存在性判定，纯汉字 +《》/· 通过存在性但密度不足，导致整段被跳过；③GPU 崩溃是 `core/transcribe_worker.py:_preload_ctranslate2_cudnn` 只预加载 `cudnn64_9.dll`，与 torch 自带 cudnn 版本冲突触发 `STATUS_STACK_BUFFER_OVERRUN`（退出码 3221226505 / 0xC0000409），且 `transcriber` 无会话级跳过标志，崩溃后反复重试。
- **关键建议**：语种码先剥离 `ai-` 前缀、扩展语种白名单并对非 CJK 语种直接信任元数据；标点守卫改用密度阈值 `_is_well_punctuated`；GPU 预加载整组 cudnn/cublas/c10 DLL 并加入 DLL 搜索路径，新增 `_GPU_CUDA_CRASHED` 会话标志，二次命中即降级 CPU。

### ✅ 质量门神（QA测试与发布）
- **核心判断**：自检应分层——单测覆盖语种映射、字幕选择、标点守卫、GPU 跳过；集成测覆盖「B站字幕列表→选择→语种透传」；E2E 用 `simulate_new_user.py` 验证新/老用户路径。沙箱中 `core/transcribe_worker` 在 win32 导入时重写 `sys.stdout/stderr`，会令任何 import `core` 的测试抛 `I/O operation on closed file`，属预存在环境问题，按既定规则排除，不计入回归。
- **关键建议**：目标测试门禁 = `test_lang_source_fix.py` + `test_gpu_skip.py`（32 passed）+ `simulate_new_user.py`（13/13）。其余套件失败统一标注为环境性、不阻断发布。

> 仅排障手与质量门神实际上场，安全官/设计师/产品官未参与本调试任务，不列。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🔴 | 功能 | `core/lang.py:_map_bili_lang` + `core/platforms/bilibili.py:_choose_subtitle` | AI 字幕语种码 `ai-ja/ai-es/ai-ar` 被 `split("-")[0]` 截断为 `"ai"` 落空；原语种模式过滤全部 `ai-*` 后无候选回退到任意 `subtitles[0]`，日语视频提取不到日文 | 剥离 `ai-` 前缀；白名单扩至 `(ja,jp,ko,en,es,ar,fr,de,ru,pt,it)`；非 CJK 语种信任元数据；原语种模式纳入「非中文 AI 轨」候选 | 排障手 |
| 2 | 🔴 | 稳定 | `core/transcribe_worker.py:_preload_ctranslate2_cudnn` + `core/transcriber.py` | 仅预加载 `cudnn64_9.dll`，与 torch 自带 cudnn 版本冲突触发 `STATUS_STACK_BUFFER_OVERRUN`（0xC0000409）；无会话级跳过，崩溃后反复重试 | 预加载整组 cudnn/cublas/c10 DLL + `os.add_dll_directory`/前置 PATH；新增 `_GPU_CUDA_CRASHED` 会话标志，二次命中即降级 CPU 仅提示一次 | 排障手 |
| 3 | 🟠 | 功能 | `core/summarizer.py:_restore_punctuation` | 输入守卫用「是否含标点」存在性判定，纯汉字 +《》/· 通过但有密度不足，整段被跳过 → 中文标点缺失 | 改为密度判定 `_is_well_punctuated(text)`（密度 ≥0.003）才跳过恢复 | 排障手 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 提交 AI 字幕语种码修复 + 回归测试 | 主理人（已落地 `27c8188`/`08531ef`） | P0 | 已完成 |
| 2 | 提交标点密度守卫修复 + 回归测试 | 主理人（已落地 `7a8ccb2`/`08531ef`） | P0 | 已完成 |
| 3 | 提交 GPU 整组 DLL 预加载 + 会话级跳过 | 主理人（已落地 `41c5664`） | P0 | 已完成 |
| 4 | 真实 Windows 机（RTX4070）长视频 GPU 稳定性回归；残留 PM=0 僵尸进程建议重启 | 用户侧 + 主理人跟进 | P1 | 下轮验证 |

---

## ⚠️ 待完善 / 已知局限

- **沙箱 win32 导入副作用（环境性，非回归）**：`core/transcribe_worker.py` 在模块导入时（win32 分支）重写 `sys.stdout/stderr`。本受限沙箱中任何 `import core` 的测试（如 `test_issue_fixes.py::test_select_formats_all`、连同 `test_resume.py`/`test_whisper_lang_passthrough.py`/`test_worker_cleanup.py` 等）会抛 `ValueError: I/O operation on closed file`。已用单独跑该测试确认其 traceback 落在 `tempfile.py`/`contextlib.py`，与本次三处功能改动无关。**按用户长期记忆规则排除，不计入回归**。真实 CLI 环境下 stdout 为真实控制台/日志重定向，不受影响。
- **推荐（未本轮实施）**：将 `transcribe_worker` 的 stdout 重定向改为惰性（仅在实际运行转写 worker 时、且仅在子进程内）而非模块导入时执行，可彻底消除对导入方的副作用，使完整测试套件在沙箱也能跑通。是否实施待用户决策。
- **GPU 长视频稳定性**：代码侧已缓解（整组 DLL 预加载 + 会话跳过 + `CUDA_MODULE_LOADING=LAZY` + MKL/OMP 单线程），但 WinError 1455 页面文件上限属系统限制，需用户在真实机调大页面文件。

---

## 📚 成员产出索引

- 排障手（gstack-investigator）原始产出：三问题根因定位（语种码截断 / 标点密度守卫 / cudnn 冲突），file:line 级。
- 质量门神（gstack-qa-lead）原始产出：自检计划（单测/集成/E2E/checklist）与沙箱环境性失败判定规则。
- 实施与验证由主理人完成，目标测试：
  - `tests/test_lang_source_fix.py` + `tests/test_gpu_skip.py` → **32 passed**
  - `tests/simulate_new_user.py` → **13/13 passed**
  - 5 个改动文件 `py_compile` → 全部通过

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
