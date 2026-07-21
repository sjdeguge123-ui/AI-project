# VidGrab 三问题回归测试方案（QA 设计，非代码）

> 适用范围：本次需锁定的三个问题
> 1. **中文全文文案**：繁体→简体转换 + 无标点子字幕自动加标点
> 2. **进程清理**：一次 skill 运行结束后，本机无 python 孤儿进程残留
> 3. **多语种一致性**：日语/韩语/其他语种的「摘要」与「全文文案」语种一致
>
> 仓库根：`G:/AI-code-project/git-repo/AI-project/VidGrab`
> 本文为 **测试策略 / 验证思路 / mock 建议 / 断言点 / 通过标准**，不落地测试代码。

---

## 0. 代码现状盘点（设计断言前必读）

| 能力 | 代码位置 | 现状 | 对回归测试的影响 |
|------|----------|------|------------------|
| 语种检测 `_detect_language` | `core/lang.py:11` | **仅 zh/en**：CJK 字符占比 > 8% → `zh`，否则 → `en`。空文本 → `""` | 日语/韩语/其他语种**全部被归为 `en`**（见 §3） |
| 标点恢复 `_restore_punctuation` | `core/summarizer.py:269` | 调 LLM 补中文标点，**仅当** `_detect_language(segments)=="zh"` 且文本基本无标点时触发；失败回退原文 | 仅在 `mode=="fulltext"` 调用（`summarizer.py:509-513`）；非全文模式不加标点 |
| 标点守卫 `_needs_punctuation` | `core/summarizer.py:263` | 文本中**存在任一** CJK 标点即返回 `False`（不重复加） | 已有标点不会重复加（见 §1 断言 D） |
| 繁体→简体转换 | **全仓未实现** | 仅 `_language_instruction("zh")` 用 LLM 指令要求「简体中文」，但**无确定性转换步骤** | ⚠️ **关键缺口**：目前无法用确定性断言验证「全简体」，需先落地转换层才能写硬断言（见 §1 缺口） |
| worker 清理 `_kill_active_worker` / `_ACTIVE_WORKER` | `core/transcriber.py:48,45` | 模块级记录活跃子进程；`atexit.register`(`transcriber.py:61`)；Windows 控制台事件(`transcriber.py:79`) | 单测已覆盖（见 §2） |
| 父进程死亡看门狗 | `skill/main.py:199-227` | 仅 **Windows + 非 tty**（agent/管道模式）启动，每 5s 探活，父死即杀 worker 并 `os._exit` | 跨平台/交互态不生效，需差异化测试（见 §2） |

**测试运行方式**：本机未安装 `pytest`（实测 `import pytest` 失败）。现有测试文件均带 `__main__` runner，优先用 `python tests/<file>.py` 直接跑；若 CI 环境装了 pytest，`pytest tests/<file>.py` 同样可用。

---

## 1. 问题 1：中文全文文案（繁体→简体 + 无标点自动加标点）

### 1.1 Mock 建议（不依赖真实 LLM / faster-whisper）

构造 fake `Segment` 列表即可，三类输入各一组：

| 输入类别 | 示例文本（`Segment.text`） | 用途 |
|----------|---------------------------|------|
| 无标点简体 | `"今天我们来聊聊人工智能的发展"` | 验证自动加标点 |
| 无标点繁体 | `"今天我們來聊聊人工智慧的發展"`（繁体字形） | 验证繁→简 + 加标点 |
| 已有标点 | `"今天我们来聊聊人工智能的发展。"` | 验证**不重复**加标点 |
| 混合（已标点繁体） | `"今天我們來聊聊人工智慧的發展。"` | 验证 T2S 与「有标点不重复加」同时成立 |

Mock 对象：
- **标点恢复**走 `_restore_punctuation`，内部直接调 `client.chat.completions.with_raw_response.create` 并经 `_raw_content` 解析。测试时用 `unittest.mock` 把该调用替换成**返回固定预期字符串**的桩（如 `return_value` 包成 `{...}.content` 形式，或直接 mock `_restore_punctuation` 的上层调用）。
- **T2S 转换**（若落地）应是一个纯函数（建议 `core/lang.py:to_simplified(text)` 或 `core/textnorm.py`），测试直接调用，无需 mock。

### 1.2 断言点（Assertion Points）

- **A. 全简体**：对「无标点繁体」输入，输出文本中**不得残留任何繁体字形**（可用一个繁体字表 / `opencc` 反向校验：繁→简后与输出一致）。→ 目前会 **FAIL**（无转换层），属「预期失败，待实现」。
- **B. 合理标点**：对「无标点简体/繁体」输入，输出须包含至少一个中文断句标点（`，` 或 `。`）；且标点位置合理（句末有 `。`，分句有 `，`）。
- **C. 字不变**：标点恢复**不得增删/改写汉字**，仅插入标点（长度增量 ≈ 插入的标点数）。
- **D. 已有标点不重复加**：对「已有标点」输入，`_needs_punctuation` 返回 `False` → 函数直接返回原文，**LLM 不被调用**（断言 mock 调用次数为 0），输出与输入逐字相等。
- **E. 幂等**：对同一条已恢复文本再跑一次，输出不变（守卫拦截第二次 LLM 调用）。
- **F. 仅中文触发**：`_detect_language(segments)=="zh"` 才走恢复；英文/未知语种输入**不**强行加中文标点（断言英文段落不含中文逗号句号）。
- **G. 失败兜底**：mock 抛异常时，`_restore_punctuation` 返回原文，主流程不中断。

### 1.3 建议新增测试文件

| 文件 | 位置 | 核心用例 |
|------|------|----------|
| `tests/test_fulltext_norm.py` | `tests/` | `test_no_punct_simplified_gets_punct`、`test_traditional_converted_to_simplified`（预期失败占位）、`test_existing_punct_not_duplicated`、`test_idempotent`、`test_only_zh_restored`、`test_llm_failure_falls_back` |

> 注：繁→简断言（A）在转换层落地前应标记为 `xfail`/`TODO`，避免 CI 误报通过但实际未实现。

### 1.4 繁→简验收用例（P0，与多语种矩阵联动 —— 来自 product-reviewer / Task A）

investigator 在 Task A 发现、product-reviewer 确认需纳入回归的漏网：**「中文必须简体」是用户二次纠正的硬要求**，且修复时必须不破坏 ja/ko。

**正例（P0 一致性断言）**：
- Mock 输入：`Segment.text = "組織們討論級大學課程"`（繁体源，可等价用真实 zh-Hant B站字幕）。
- 期望：无论**全文文案**还是**摘要**，最终中文内容均为简体 → `"组织们讨论级大学课程"`。
- 当前预期状态（代码审查结论，非测试运行）：**全文 FAIL（仍繁体）** / **摘要 PASS（prompt 约束要求简体中文）**。这正是要修的不一致点；该用例在 `_to_simplified` 层（P0，开发侧）落地后应从 FAIL 翻 PASS。

**关键负例（防修复反向破坏 ja/ko，与 §3 P0 强相关）**：
- `ja` 源含汉字（如 `図書館`）→ 全文**不可**被简化成 `图书馆`，必须保持日文原文。
- `ko` 源含汉字词 → 全文**不可**被触碰。
- 理由：`_to_simplified` 必须**仅当 `language=="zh"` 触发**；若实现成「含汉字就简化」，会把 ja/ko 搞坏。这两类负例与正例必须**同批**进矩阵，缺一不可。

**本组用例锁定的三件事（product-reviewer 原话）**：
1. 语种选择正确性：ja/ko 不被误标成 zh（→ 不会被送去简化）。
2. zh 简体一致性：繁体源 → 简体输出（正例）。
3. ja/ko 不被误简化：含汉字的日/韩文全文保持原样（负例）。

> 实现归属：正例/负例都依赖开发侧先落地 `core/lang.py:to_simplified`（仅 `language=="zh"` 触发）。QA 侧在 `_to_simplified` 存在后即可落 `test_fulltext_norm.py::test_traditional_to_simplified` 与 `test_ja_ko_not_simplified`，并把正例当前 FAIL 状态作为「已知待修」回报。

---

## 2. 问题 2：进程清理（无 python 孤儿进程）

### 2.1 单元测试能否验证 worker 清理？—— 能，且已部分覆盖

现有 `tests/test_worker_cleanup.py` 已验证：
- `test_kill_active_worker_terminates_child`：`_ACTIVE_WORKER` 指向一个 sleep 30s 假 worker，`_kill_active_worker()` 后 `proc.poll()` 非 `None`。
- `test_kill_active_worker_noop_when_none`：`_ACTIVE_WORKER=None` 时不抛异常。

**需补充的单元级断言**（建议并入同文件或 `tests/test_process_cleanup.py`）：

| 用例 | 验证点 | 做法 |
|------|--------|------|
| atexit 兜底 | 解释器正常退出时 worker 被清理 | 起一个子进程：`python -c "import core.transcriber as t,subprocess,sys; t._ACTIVE_WORKER=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"`；该命令返回后，`tasklist/ps` 中不应再有 `sleep(30)` 的 python 子进程 |
| 控制台事件（Windows） | `SetConsoleCtrlHandler` 注册成功且 Ctrl+C 时能清理 | 仅 Windows 跑；可起子进程发送 `CTRL_C_EVENT`，断言 worker 退出 |
| 看门狗 `_start_parent_watchdog` | 父死即清理 | 仅 Windows+非 tty；起一个「父进程立即退出、看门狗线程探活」的场景，断言 worker 在 5s 内被 `os._exit` 掉 |
| 记录清空 | worker 正常结束后 `_ACTIVE_WORKER=None` | mock 转录成功路径，断言 `_ACTIVE_WORKER` 复位，避免 atexit 误杀已退出进程（`transcriber.py:471`） |

### 2.2 集成 / 本地验证：一次 skill 运行后无残留

**目的**：端到端确认「项目运行结束」后本机无 VidGrab 转录孤儿进程。

**推荐做法（需要自动化脚本）**：
- 新增 `tests/check_no_orphan_workers.py`：用 `psutil` 枚举进程，匹配 `cmdline` 含 `core.transcribe_worker` / `faster-whisper` / `vidgrab` 的 python 进程。
- **前后对比法**：在运行前快照进程列表 `before`，运行结束（含正常退出 / 异常 / Ctrl+C）后快照 `after`，断言 `after - before` 中**无**属于本项目的转录子进程。
- Windows 命令直查（人工/CI 辅助）：`tasklist /FI "IMAGENAME eq python.exe" /V`，人工核对 COMMAND LINE 列无 `transcribe_worker`。
- Linux/macOS：`ps -eo pid,ppid,args | grep -i transcribe_worker | grep -v grep`，断言为空。

**是否需要自动化脚本？—— 需要**，但应分层：
- 轻量纯单测（无需 psutil，跨平台）→ 并入 `test_worker_cleanup.py`。
- 端到端残留扫描（需 psutil，模拟完整运行）→ `check_no_orphan_workers.py`，作为 CI 的「集成」档，可在真机/Windows CI 跑。

### 2.3 通过标准（问题 2）

- 单测：上述 atexit / 看门狗(Windows) / 记录清空全部 PASS。
- 集成：一次完整 skill 运行（成功 / 转录失败 / 用户 Ctrl+C 三种结局各跑一遍）后，本机 `psutil` 扫描**零** VidGrab 转录孤儿进程；前后进程差为空。
- 超时保护：运行结束后 10s 内扫描应已无残留；超时需要人工介入排查。

---

## 3. 问题 3：多语种一致性（日语/韩语/其他语种）

### 3.1 `_detect_language` 只支持 zh/en —— 契约分两层（baseline 已知失败 / target 正确态）

**关键事实**：当前 `_detect_language` 按 CJK 字符占比判定。
- 日语：含汉字（属 CJK 区 `一`–`鿿`）但混大量平假名/片假名，**CJK 占比通常 < 8%** → 返回 `"en"`。
- 韩语：Hangul（`가`–`힣`）**不在** CJK 区 → 返回 `"en"`。

**两层契约**（经 product-reviewer 评审确认）：
- **Baseline（当前已知缺陷，标记为 known-failing）**：`ja→en` / `ko→en`。写 `test_detect_language_ja_returns_en` / `test_detect_language_ko_returns_en`，用 `xfail` / 预期失败占位锁进回归，防止未来改动意外改变返回。
- **Target（P0 落地后应断言通过）**：`ja→"ja"`、`ko→"ko"`，**不得**再落到 `en`。即契约测试的最终目标态是**正确态**，不是「两者都归 en」。

**语种码映射（供 #1–#10 矩阵覆盖）**：

| 信号来源 | 原始值 | 内部码 |
|---|---|---|
| Whisper（本地/API 返回） | `"ja"` / `"ko"` | `ja` / `ko` |
| B站 `lan` | `"ja"` / `"ko"` / `"zh-CN"`·`"zh-Hans"` / `"zh-Hant"` / `"en"` | `ja` / `ko` / `zh` / `zh` / `en` |
| 文本启发式兜底（P0 新版 `_detect_language`） | 含假名(Hiragana/Katakana)→`ja`；含 Hangul→`ko`；纯汉字无假名/Hangul→`zh`；其余→`en` | 同上 |

**当前不一致定位**：`_language_instruction` 的「未知/auto/空」默认分支要求模型「使用与文字稿相同的语言输出」（`summarizer.py:251-255`）。但 `_detect_language` 不会产出 `"ja"/"ko"`，所以日语视频的 `transcript.language` 仍是 `"en"`，摘要出英文、全文留日文 = 当前不一致（已锁定为已知缺陷）。P0 落地后该路径改为确定性分支，见 §3.2。

### 3.2 扩展后测试矩阵（经 product-reviewer 确认，P0/P1 落地）

**前置改动（测试随实现落地）**：
1. `core/lang.py:_detect_language` 改为**基于文字脚本判定**：含假名(Hiragana/Katakana)→`ja`；含 Hangul→`ko`；纯汉字无假名/Hangul→`zh`；其余→`en`（维持原 8% CJK 兜底亦可）。
2. `core/summarizer.py:_language_instruction` **补齐 `ja`/`ko` 显式分支**（P1 #4，当前缺失）。默认分支 ONLY 在未知/空码时触发，ja/ko/zh/en 都走确定分支，不靠 LLM 软对齐。
3. `summarizer.py:512` 的 `_restore_punctuation` 守卫 `if _detect_language(...)=="zh"` 需改成语种感知（P1 #6）：日语全文走日文标点恢复，而非中文/无。

**测试矩阵（`tests/test_multilingual.py` #1–#10，scope=ja/ko only，不铺 fr/es/ru）**：

| # | 场景 | 输入/信号 | 期望 `_detect_language` | 期望 `_language_instruction` 分支 | 一致性不变量 |
|---|------|-----------|------------------------|-----------------------------------|--------------|
| 1 | Whisper 返 ja 透传 | Whisper 输出 `language="ja"` | `ja` | ja 分支（日本語） | 摘要=全文=日文 |
| 2 | Whisper 返 ko 透传 | Whisper 输出 `language="ko"` | `ko` | ko 分支（한국어） | 摘要=全文=韩文 |
| 3 | B站 `lan=ja` | `lan="ja"` | `ja` | ja 分支 | 摘要=全文=日文 |
| 4 | B站 `lan=ko` | `lan="ko"` | `ko` | ko 分支 | 摘要=全文=韩文 |
| 5 | 纯假名文本启发式 | `こんにちは、今日はいい天気ですね` | `ja` | ja 分支 | 摘要=全文=日文 |
| 6 | 纯 Hangul 文本启发式 | `안녕하세요 오늘 날씨가 좋네요` | `ko` | ko 分支 | 摘要=全文=韩文 |
| 7 | 汉字为主→zh | `今天我们来聊聊人工智能` | `zh` | zh 分支（简体中文） | 摘要=全文=简中 |
| 8 | 纯英文→en | `This is an English video` | `en` | en 分支（English） | 摘要=全文=英文 |
| 9 | 空文本→"" | `""` | `""` | 默认分支（先判断再输出） | 不空跑进 LLM |
| 10 | 未知码→默认 | `transcript.language="xx"` | `xx`（透传/未知） | 默认分支 | 模型按文字稿语种输出，不混语 |

> 注：#1–#2 验证 Whisper 码**透传不翻译**；#5–#6 验证文本启发式兜底；#9–#10 验证默认分支仅在未知/空码触发。

**ja 分支断言文案（产品保证，实现可微调但须保留保证）**：
> すべてを日本語で出力し、日本語として自然な句読点（、。など）で適切に区切ってください。章の要点・内容のまとめ・名言（golden quotes）はすべて日本語としてください。中国語や英語に翻訳しないでください（ただし、中国語・英語由来の固有名詞は原文のままでかまいません）。

**ko 分支断言文案**：
> 모든 것을 한국어로 출력하고, 한국어에 자연스러운 문장 부호(,. 등)로 적절히 끊어 쓰세요. 챕터 요점·내용 요약·명언(golden quotes)은 모두 한국어로 작성하세요. 중국어나 영어로 번역하지 마세요(단, 중국어·영어 고유명사는 원문 그대로 둬도 됩니다).

**一致性契约（写进矩阵断言）**：
- `transcript.language` ∈ {zh, en, ja, ko} → 摘要、金句、章节要点**全部**使用该语种，不混语（断言摘要文本/金句主要语种 == 该语种码）。
- 全文文案始终为转录原文（verbatim），本身即该语种 → 与摘要语种一致。
- ja 视频 → 摘要 ja + 全文 ja(原) + 金句 ja；ko 同理。即「内容跟随听到的语种」的确定性表现。
- P1 #6 待补断言：ja/ko 全文的标点恢复走各自语种标点（日文/韩文标点），不误用中文标点、也不漏加。

**通过标准（问题 3，扩展后）**：#1–#10 全部 PASS；`_language_instruction("ja")`/`("ko")` 返回对应语种指令（非默认分支）；一致性契约对所有语种成立；baseline 的 known-failing 用例在 P0 落地后翻转为 PASS。

**T2S 联动闸门（与 §1.4 同批，缺一不可）**：`core/lang.py:to_simplified` 必须**仅 `language=="zh"` 触发**。矩阵须同时断言：① 繁体 zh 源 → 简体输出（正例）；② `ja` 含汉字（図書館）全文**不被**简化；③ `ko` 含汉字词全文**不被**触碰。三者同批锁住「语种选择正确 + zh 简体一致 + ja/ko 不被误简化」。

### 3.3 通过标准（问题 3）

- 当前契约测试：ja/ko 输入 `_detect_language` 返回 `"en"` 的断言 PASS（锁定现状）；同时标记「ja/ko 全文≠摘要」为已知缺陷项，进入产品待办。
- 扩展后：矩阵 #1–#10 全部 PASS；`_language_instruction` 对 `ja`/`ko` 返回对应语种指令（非默认分支）；一致性不变量对全部语种成立。

---

## 4. 可执行测试命令 + 全局通过标准

> 默认用 `python tests/<file>.py`（本机无 pytest）。装有 pytest 时用 `pytest tests/<file>.py -q`。

```bash
# 问题 1：全文归一化（繁→简 + 标点）
python tests/test_fulltext_norm.py        # 新增；繁→简断言在转换层落地前 xfail

# 问题 2：worker 清理
python tests/test_worker_cleanup.py       # 已有；建议并入 atexit/看门狗用例
python tests/check_no_orphan_workers.py   # 新增；端到端残留扫描（需 psutil）

# 问题 3：语种一致性
python tests/test_language_consistency.py # 已有（zh/en + 空语言防御）
python tests/test_multilingual.py         # 新增；ja/ko 矩阵 + 扩展后全矩阵

# 一键全量（本机无 pytest 时）
python tests/test_issue_fixes.py && python tests/test_language_auto.py \
  && python tests/test_language_consistency.py && python tests/test_resume.py \
  && python tests/test_worker_cleanup.py && python tests/test_fulltext_norm.py \
  && python tests/test_multilingual.py && python tests/check_no_orphan_workers.py
```

**全局通过标准**：
1. 问题 1：无标点中文输入产出含 `，`/`。` 的文本且汉字不增删；已有标点输入零改动、零 LLM 调用；英文不强行加中文标点。（繁→简在转换层落地后，须额外 PASS「零繁体残留」断言。）
2. 问题 2：单测（atexit / 看门狗[Win] / 记录清空）PASS；端到端扫描零转录孤儿进程（成功/失败/Ctrl+C 三结局）。
3. 问题 3：当前契约（ja/ko→en）锁定 PASS；扩展后矩阵 #1–#10 PASS 且「摘要语种 == 全文语种」对所有语种成立。
4. 全部测试退出码为 0，无 `xfail` 之外的失败（繁→简 xfail 须附带 TODO 跟踪）。

---

## 5. 建议新增/调整文件一览

| 文件 | 动作 | 归属问题 | 核心用例 |
|------|------|----------|----------|
| `tests/test_fulltext_norm.py` | 新增 | 1 | 无标点加标点 / 已有标点不重复 / T2S（xfail）/ 仅 zh / 失败兜底 / 幂等 / **繁→简正例(§1.4)** / **ja·ko 不被误简化负例(§1.4)** |
| `tests/check_no_orphan_workers.py` | 新增 | 2 | 前后进程差扫描（psutil），断言零残留；成功/失败/Ctrl+C 三结局 |
| `tests/test_multilingual.py` | 新增 | 3 | ja/ko 当前返回 en 契约；扩展后 #1–#10 矩阵 + 一致性不变量 + T2S 联动闸门 |
| `tests/test_worker_cleanup.py` | 扩充 | 2 | 并入 atexit 兜底、看门狗(Win)、记录清空用例 |
| `core/lang.py` | 需实现 | 1+3 | `to_simplified()`（繁→简，**仅 `language=="zh"` 触发**）；`_detect_language` 扩展 ja/ko 脚本判定 |
| `core/summarizer.py` | 需实现 | 3 | `_language_instruction` 补齐 `ja`/`ko` 分支 |

---

## 6. 风险与缺口（QA 视角的必须提示）

1. **【高】繁体→简体转换层缺失**：当前全仓无确定性 T2S 步骤，仅依赖 LLM 指令「简体中文」。这意味着「全简体」无法硬断言，且 LLM 可能漏转。回归测试前**必须先落地** `core/lang.py:to_simplified`（建议 `opencc-python-rehab` 或 `hanziconv`），否则问题 1 的「全简体」断言只能 xfail。
2. **【中】标点恢复仅覆盖全文模式**：`_restore_punctuation` 只在 `mode=="fulltext"` 调用（`summarizer.py:512`）。若用户期望「精简/详细」模式摘要里的中文也强制标点，需确认范围——当前设计是摘要靠 LLM 指令自行断句，不二次标点。
3. **【P0，已与 product-reviewer 确认】ja/ko 一致性当前不成立**：如 §3.1，日语/韩语视频摘要被判英文、全文留原语种，属已知不一致。已确认处理方案：baseline 锁为 known-failing，target 契约为 `ja→"ja"`/`ko→"ko"`（P0 落地后翻 PASS）；`_language_instruction` 需补 ja/ko 显式分支（P1 #4）；fulltext 标点守卫需语种感知（P1 #6）。scope 仅 ja/ko，不铺 fr/es/ru（属 P2）。
4. **【低】看门狗/控制台事件仅 Windows 生效**：跨平台（Linux/macOS）与交互 tty 模式不启动看门狗，孤儿防护弱于 Windows。CI 应在 Windows runner 上单独验证该路径。
5. **【低】本机无 pytest**：CI 若依赖 pytest 需先装；本地直接用 `python tests/<file>.py` 即可，建议两份命令都保留。
