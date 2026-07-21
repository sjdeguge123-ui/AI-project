# VidGrab 调查报告 · 任务 A 繁体/无标点全文文案 + 任务 B 僵尸进程

> 调查员：gstack-investigator ｜ 日期：2026-07-21 ｜ 仓库：`G:/AI-code-project/git-repo/AI-project/VidGrab`
> 结论性质：**只读调查，未改动任何代码**。所有行号基于当前工作区文件。

---

## 任务 A：中文全文文案「繁体、无标点」

### A1. B站字幕提取路径是否经过繁简转换？
**结论：否，全链路没有任何繁→简归一化。**

- `core/platforms/bilibili.py:226-248` `_fetch_bilibili_subtitle`：下载字幕 JSON 后直接 `return parse_bilibili_json(path)`（line 248），不触碰文字。
- 全仓搜索 `繁|简|opencc|traditional|simplif|convert|zhconv|hanzi`：命中的只有
  - `core/summarizer.py:246-247` 摘要提示里的"全部用简体中文输出"（**只约束 AI 生成的摘要文字，不约束原文透传**）；
  - `core/lang.py:11-28` 的 `_detect_language`（只判定语种，不转换）；
  - `models/faster-whisper-base/*` 的词表（无关）。
- **没有任何 opencc / zhconv / 字符映射调用**。所以 B站返回什么（繁体来自港台片源或 AI 繁体字幕），VidGrab 原样透传到底。

### A2. 全文文案/转录文本输出前是否做标点恢复？音频与字幕路径是否都调用？
**结论：标点恢复全仓仅 1 处，且仅在「全文文案(fulltext)」模式触发，依赖 LLM；字幕与音频在此汇合，其它模式不恢复。**

- `_restore_punctuation` 定义：`core/summarizer.py:269-301`。
- 唯一调用点：`core/summarizer.py:513`，位于 `generate_summary` 的 `if mode == "fulltext":` 分支（509-513）。
- 字幕路径（`extract_bilibili` → `Transcript.source="subtitle"`）与音频转录路径（`transcriber.transcribe` → `source="transcript"`）最终都在 `generate_summary` 汇合，**因此只有 fulltext 模式**才会触发标点恢复；精简/详细/自定义模式靠 `_language_instruction` 让 AI 重新生成带标点的简体摘要（那是新文本，不是对原文恢复）。
- 即：用户**不选全文文案**就根本走不到 `_restore_punctuation`。

### A3. `_restore_punctuation` 在哪、实现逻辑、是否只在 audio 路径？
- 位置：`core/summarizer.py:269-301`（系统提示 278-283，调用 290-299）。
- 逻辑：
  - 门槛①：`if not text or not _needs_punctuation(text): return text`（`_needs_punctuation` 263-266：文本里出现任一 CJK 标点即视为"已断句"并跳过）。
  - 门槛②：调用方仅在 `_detect_language(segments)=="zh"` 时调用（513）。
  - 系统提示明令："【仅】补上缺失的中文标点……【绝对不要】改动、增删任何汉字或时间戳"（281）。
  - 失败兜底：`except Exception: return text`（300-301）——任何异常（网络/限流/空响应）**静默返回原文**，不报错、不重试、不打日志。
- 是否只在 audio 路径？**否**。它只在 fulltext 分支，对 subtitle 与 transcript 一视同仁；但它**不能做繁→简**（被提示禁止改字），且是全文**唯一**标点恢复点，且失败静默——典型的脆弱单点。

### A4. 为什么有的视频正常（BV1zh411Y7LX p2）、有的异常（截图视频）？
**结论：差异来自字幕源质量，而代码对"脏源"零兜底。**

- **BV1zh411Y7LX p2**：字幕源本身简体 + 自带标点 → `_needs_punctuation` 返回 False → 跳过 `_restore_punctuation` → 原样输出（正确）。
- **截图视频**：字幕源是**繁体 + 无标点**（"組織們""級大學"）。走 fulltext → `_detect_language` 把繁体也算 CJK（`core/lang.py:27` 范围 `一`~`鿿` 含繁体）→ "zh" → `_needs_punctuation` True → 调 `_restore_punctuation`。但：
  - (a) 该函数提示明令禁止改字，所以**即使成功也只会把繁体补上标点，仍是繁体**，无法满足用户"中文必须简体"的硬性要求（见 `deliverables/gstack/debug-vidgrab-4issues-2026-07-21.md` ② 用户二次纠正）。
  - (b) 截图里**连标点都没有** → 说明 `_restore_punctuation` **调用失败并静默回退**（300-301 返回原繁体无标点文本），或返回不可用结果。与"无标点"现象完全一致。
- **一句话根因**：全文文案是字幕/转录的**逐字透传**，既**无繁→简归一化**，又**只有一处依赖 LLM 且失败静默的标点恢复**；于是"源干净"的视频正常，"源脏（繁体/无标点）"的视频被原样暴露。

### A 修复建议（不改代码，供实施）
1. **新增繁→简归一化（治本）**：引入 `opencc`（或 `opencc-python`），在 segment 级别归一。
   - `core/lang.py` 新增 `_to_simplified(text)`，仅当 `_detect_language` 为 zh 时调用（避免误转英文/专有名词）。
   - 调用点：① `core/platforms/bilibili.py:248` 在 `parse_bilibili_json` 返回前对每个 `segment.text` 归一；② 音频转录结果装配处（`core/transcriber.py` 的 `transcribe` 返回前 / `core/transcribe_worker.py` 写出前）。这样字幕与转录进 pipeline 即统一为简体，全文与摘要同时受益，摘要引用的原话也变简体。
2. **标点恢复可观测 + 去单点依赖**：
   - `_restore_punctuation`（269-301）失败时应 `print`/记日志（至少一行告警），不要无声回退。
   - 在 LLM 恢复之外加一层**本地规则标点**（按句末语气词/停顿补逗号句号）作为 LLM 不可用兜底；或把标点恢复扩展到所有需展示原文的模式，而非只有 fulltext。
   - 也可让该函数提示"补标点并把繁体转简体"，但与现有"绝对不要改字"约束冲突，不如第 1 条在 pipeline 入口统一做更干净。
3. 验证：用同款繁体无标点字幕跑 fulltext，确认输出简体且有标点。

---

## 任务 B：僵尸 / 孤儿 python 进程

### B1. 当前本机 python.exe 进程清单（快照）
**确认：确有残留，且存在孤儿转录 worker。**

来源：`Get-CimInstance Win32_Process -Filter "Name='python.exe'"`（解释器 `D:\Python\Python3.11\python.exe`）。关键条目：

| PID | PPID | 启动时间 | 命令行(截断) | 判断 |
|---|---|---|---|---|
| 46928 | 69136 | 07-21 18:51:09 | `python -u -m core.transcribe_worker ... BV1b4411e7KF ...` | **孤儿 worker**（父 69136 已不在 python.exe 表） |
| 71612 | 69136 | 07-21 18:51:13 | `python -u -m core.transcribe_worker ... BV1b4411e7KF ...` | **孤儿 worker**（同视频两只） |
| 24808 | 75976 | 07-21 18:57:52 | `python -u -m core.transcribe_worker ... BV1Bp411R71X ...` | **孤儿 worker**（父 75976 已不在表） |
| 55512 | 75976 | 07-21 18:57:55 | `python -u -m core.transcribe_worker ... BV1Bp411R71X ...` | **孤儿 worker**（同视频两只） |
| 48824 | 63752 | 07-21 18:53:12 | `python -m skill .../BV1b4411e7KF...` | 主进程仍在 |
| 9744  | 36344 | 07-21 19:02:09 | `python -m skill .../BV1Bp411R71X...` | 主进程仍在 |
| 62612 | 75956 | 07-21 18:49:57 | `python -m skill .../BV1ot411d7T5...` | 主进程仍在 |
| 10424 | 54336 | 07-21 18:39:28 | `python tests/simulate_new_user.py` | 测试进程仍在 |

- 4 个 `core.transcribe_worker` 的父 PID（69136、75976）**均不在 python.exe 进程表中** → 其父主进程已被杀/退出，子进程未被回收 = 典型 Windows 孤儿进程。
- **同一视频出现两只 worker**（46928+71612、24808+55512）→ 重试/并发留下了未被追踪的子进程（见 B3 漏洞②）。
- （另有 07-20 创建、命令行空的 python.exe，属 agent/编辑器宿主，与本次无关。）

### B2. 是否还有其它 Popen/subprocess 启动点未纳入清理？
全局搜索 `subprocess.Popen/run`、`os.system`、`os.spawn`、`multiprocessing`，除测试/工具外：
- `core/transcriber.py:450` `subprocess.Popen` 启 worker —— 已用 `_ACTIVE_WORKER` 单槽 + atexit 跟踪（仅覆盖干净退出）。
- `core/transcriber.py:743` `subprocess.run(ffmpeg...)` —— 阻塞式，ffmpeg 自行退出，无残留风险。
- `core/platforms/_ytdlp.py:104` `subprocess.run(...)` —— 阻塞式，无风险。
- `skill/main.py:90` `subprocess.run(pip install...)` —— 阻塞式，无风险。
- `tools/gpu_stress.py:49` `subprocess.Popen` —— 开发工具，非运行路径。
- `core/transcribe_worker.py` 自身：成功/失败都 `os._exit`（96/101），**不再 spawn 子进程**，是叶子节点。

**结论**：运行期唯一会遗留子进程的只有 `_transcribe_local` 的 worker（line 450），它已被"单槽"跟踪，但**单槽本身就是漏洞**。

### B3. `_start_parent_watchdog` 触发条件与漏洞
（实际函数名 `_start_parent_watchdog`，定义 `skill/main.py:199-227`，在 `main()` 首行 791 调用。）

- 触发条件：
  - `sys.platform == "win32"` 才继续（206-207）；非 Windows 不启看门狗。
  - `sys.stdin.isatty()` 时**直接 return 不启用**（208-209）——交互终端模式关看门狗（靠关闭终端触发控制台事件）。
  - 因此：**agent/自动化以非交互（stdin 非 tty）启动时，看门狗确实会启用**（守护线程每 5s 探父存活，父死即杀 worker 并 `os._exit(0)`）。该点对"agent 启动"是生效的。
- 漏洞（这就是仍出现孤儿的原因）：
  1. **看门狗只监视"主进程自己的父进程"（agent/终端），不监视 worker 的父进程（VidGrab 主进程本身）**。当 **VidGrab 主进程被直接杀掉**（agent 的 TaskStop / 会话拆解 / 超时 / SIGKILL / 任务管理器），主进程连同看门狗线程一起死，**没有任何机制去杀 worker** → worker 变孤儿。本次快照正是此情形（主 69136/75976 已死，worker 残留）。
  2. **`_ACTIVE_WORKER` 是单槽变量**（`transcriber.py:45,461-472`）。重试时若上一个 worker 尚未回收就起新 worker，旧 worker 不再被任何引用追踪 → 孤儿。快照里"同视频两只 worker"即此隐患实证。
  3. **atexit 与控制台事件只在干净退出时触发**：`transcriber.py:61` 的 `atexit.register`、`:70-79` 的 `SetConsoleCtrlHandler`，对 `SIGKILL`/进程被强杀不生效（看门狗内 `os._exit` 本身绕过 atexit，但看门狗已先手动 kill worker，故该路径 OK）。

### B4. 根因结论
Windows 不在父进程死亡时级联杀子进程。VidGrab 现有三道防线（atexit、控制台事件、父进程看门狗）**都依赖主进程仍然存活或干净退出**，无法抵御"主进程被外部直接终止"这一最常见场景（agent 终止任务、会话结束、超时）。且 worker 跟踪是单槽，重试/并发会丢引用。结果：主进程一死，worker 孤儿残留占内存——与快照完全吻合。

### B 修复建议（不改代码，供实施）
1. **使用 Windows Job Object（治本，唯一抗 SIGKILL 方案）**：
   - 主进程（`skill/main.py` 启动早期或 `transcriber.py` 模块加载处）创建 Job Object，设 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（或 `SetInformationJobObject` 的 `KillOnJobClose=TRUE`）。
   - 每启动一个 worker（`core/transcriber.py:450` 的 `Popen` 之后）立即 `AssignProcessToJobObject(job, proc._handle)`；ffmpeg 也可纳入。
   - 主进程**无论以何种方式终止（含 TaskStop/SIGKILL）**，Job 句柄关闭 → OS 强制杀掉 Job 内所有进程 → 孤儿杜绝。
   - 注意：`AssignProcessToJobObject` 要求子进程未设 `CREATE_BREAKAWAY_FROM_JOB`（默认满足）。
2. **worker 跟踪从单槽改集合**：`_ACTIVE_WORKER` → `_ACTIVE_WORKERS: set`；每次 `Popen` 加入，`wait` 后移除；`_kill_active_worker` 遍历 kill 全部。修复"同视频双 worker 漏追踪"。
3. **保留并加固现有三道防线**（atexit + 控制台事件 + 父看门狗）作为干净退出快速路径；Job Object 一并兜底"被强杀"场景。
4. **可选**：`_kill_active_worker` / 看门狗失败路径加 `print`/日志，便于事后确认清理是否成功。

---

## 给 team-lead 的总览
- **A（繁体/无标点）**：根因 = 全文文案逐字透传，①无繁→简归一化 ②唯一标点恢复 `_restore_punctuation` 仅 fulltext、被禁止改字、且失败静默。修复 = pipeline 入口做繁→简（opencc）+ 标点恢复可观测/加本地兜底。
- **B（僵尸进程）**：根因 = 三道防线都假设主进程干净存活/退出，无法抗"主进程被外部强杀"，且 worker 单槽跟踪漏引用。修复 = Windows Job Object（KillOnJobClose）兜底所有子进程 + worker 用集合跟踪。

### 跨成员协作建议
- 请 **gstack-product-reviewer** 把 A 的"中文必须简体"约束补一条用例：**繁体字幕源 → 全文文案应输出简体**（当前缺此覆盖，正是漏网点）。
- 请 **gstack-qa-lead** 针对 B 增加 Job Object 单元测试：**模拟主进程被终止（如 `proc.kill()` 父进程）后，worker 子进程应被 OS 回收**，以及 `_ACTIVE_WORKERS` 集合跟踪多 worker 的清理测试。
