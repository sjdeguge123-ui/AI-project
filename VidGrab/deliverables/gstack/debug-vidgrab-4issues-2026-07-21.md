# VidGrab 本轮 4 项问题收口 · 自检报告

- **提交**：`a1da63c`（本地已提交，未 push；push 待每日 23:00 提醒确认）
- **验证总况**：`py_compile` 6 文件 OK · `test_issue_fixes.py` 4/4 · `test_language_auto.py` 3/3 · `simulate_new_user.py` 13/13（零回归，比上轮 12/13 更好）

---

## ① UI 再次选择 + 全选

**问题**：第一次选「全文」后再选时，「精简」选项消失；选择输出格式时没有「全选」。

**修复**（`skill/main.py`）：
- `_select_formats` 新增「全选」入口：`0 / all / 全选 / 全部` → 返回全部 5 格式（Markdown / HTML / Word / PDF / 图片）；CLI `--formats all` 同样生效。
- `_offer_other_versions` 菜单补回「精简」，并重编号 **1-5**：`1.精简 2.详细 3.自定义（关键词） 4.全文文案 5.不需要了，退出`。再次选择时所有选项均可见（旧版漏了精简，且只列了 1-4）。

**自检**：`test_issue_fixes.py::test_select_formats_all` 覆盖 `forced="all"` 与 `forced="全选"` 均返回 5 格式 → PASS。

## ② 语种真实化（全文随真实语种，摘要始终中文）

**问题**：用户实测 BV1CW411P7rb（p1/p2，实为**英文**视频）全文文案是英文、但精简/详细摘要是中文，质疑不一致。

**根因**：旧代码对**所有** B站视频硬标 `language="zh"`，导致英文视频被误判中文、全文被强行加中文标点；同时 `_language_instruction` 按视频语种切换，英文视频摘要会变英文。

**修复**：
- 新增 `_detect_language(segments)`：按转录文本 CJK 字符占比判定（>8%→zh，否则 en，空→""），替换三处硬编码 `language="zh"`。
- 全文文案分支仅在 `_detect_language(segments)=="zh"` 时才做轻量中文标点恢复；英文全文保持英文、不塞中文标点。
- `summarizer._language_instruction`：**跟随视频语种动态变化**（见下"用户二次纠正"）。

**结论（关键澄清）**：该视频音频确为英文（用户自己确认"听到的音频是英文"）。因此**全文=英文是正确的**（跟随真实语种）；摘要按语种动态出（英文视频→英文，中文视频→简体中文+标点）。之前的不一致是旧代码把英文视频硬标中文所致，现已修。

> ⚠️ **用户二次纠正（2026-07-21 16:05，commit c31ddfd）**：上轮我曾把 `_language_instruction` 写死成"始终简体中文"，用户明确反对——**摘要不能一律中文，必须跟随视频语种动态调整**。已改回按 `transcript.language` 分支：zh→简体中文+中文标点（满足用户"中文必须简体+逗号句号"的硬性要求）、en→英文、空/auto→跟随音频语种不强行统一中文。全文文案本身也跟随真实语种，二者现在一致。

## ③ 分P 体现

**问题**：分P 视频的文件名与内容标题未体现 P1/P2。

**修复**（`bilibili.py` + `exporter.py`）：
- 用 `get_pages()` 复核真实分P数（`get_info().pages` 有时只返回 1 条导致漏判合集）。
- 多P 标题加 `P{n} · ` 前缀（正文标题明确第几集）；文件名由 `exporter._safe_title` 统一用 `-P{n}` 后缀体现，并**剥离**正文里的 `P{n} · ` 前缀避免重复。
- 单P 视频：无 `-P{n}` 后缀、无前缀（测试覆盖）。

**自检**：`test_issue_fixes.py::test_safe_title_multipp_strips_prefix`（多P 文件名以 `-P1` 结尾且无 `P1 · ` 前缀）与 `::test_safe_title_singlep_no_suffix`（单P 无 `-P`）→ PASS。

## ④ GPU 仍报 WinError 1455（页面文件太小）

**问题**：用户查最新日志，GPU 转录仍报 `WinError 1455 页面文件太小`（日志 line ~1217），CPU 回退后又因 RAM 压力 `mkl_malloc` 崩。

**根因**：Windows 加载 `cublas64_12.dll`（CUDA）时提交内存峰值超过页面文件上限，子进程 import torch/ctranslate2 阶段即崩。这是**系统资源限制**，非纯逻辑 bug。

**修复**（`transcriber.py`）：
- 子进程注入 `CUDA_MODULE_LOADING=LAZY`（NVIDIA 11.7+ 懒加载 CUDA 模块），大幅降低加载 CUDA 库时对页面文件的峰值占用——**代码侧可缓解**。
- 失败提示区分「页面文件太小」(1455) 与其他原因，精准引导：把虚拟内存调到物理内存 1.5–2 倍并重启，或直接设 `whisper.mode: api` 走云端转录彻底规避本地显存/内存限制。

**诚实说明**：代码注入 LAZY 能显著降低触发概率，但**不能 100% 保证**在页面文件/RAM 确实过小的机器上 GPU 一定成功。根治仍需用户调大虚拟内存或改用 api 模式。本机真实长视频稳定性建议用户最终确认。

---

## 后续追加修复（commit c1ef5c1）

### A. 英文视频摘要仍出中文
**根因**：`a1da63c` 只改了 `_language_instruction`，但 `_CHUNK_SYSTEM_PROMPT` / `_MERGE_SYSTEM_PROMPT` 的「要求」列表里还硬编码了 `- 全部用简体中文。`，位置靠后、更具体，模型优先执行。

**修复**：将这两处改为引用 `{language_instruction}`，让语言指令成为摘要语种的唯一来源。

### B. GPU 仍报错 → 默认改 CPU
用户要求「如果还是不行，一律改成 CPU 处理」。日志显示 GPU 失败后确实自动回退 CPU，但用户机器页面文件过小，GPU 仍触发 WinError 1455。

**修复**：`WhisperConfig.device` 默认值由 `auto` 改为 `cpu`（`core/config.py` + `config/config.example.yaml`）。新用户默认走 CPU，规避 Windows GPU 页面文件不稳定；需要 GPU 加速的用户可手动改 `device: cuda` 或 `auto`。

### C. 提示重复视频名字
**修复**：`_offer_other_versions` 的输入提示不再带 `《标题》`，标题只在上方 `💡` 行展示一次。

### D. 文件名格式（已确认并落地，commit 9c2130b）
用户确认样本：「是 【TED】化负为正：和给我负面评论的人对话（中英字幕）…-摘要-精简.html，其他的场景也是这样处理视频名字」。

**最终规则**（用户 2026-07-21 确认）：`视频名(≤30字截断)` + (`-Pxx` 合集) + `-摘要` + `-模式`
- 单P 精简（与用户确认样本**逐字一致**）：`【TED】化负为正：和给我负面评论的人对话（中英字幕）…-摘要-精简.html`
- 合集 P1 精简：`【TED】化负为正：和给我负面评论的人对话（中英字幕）…-P1-摘要-精简.html`
- 合集 P1 全文文案：`…-P1-摘要-全文文案.html`
- 截断点若落在标题自带「 - 」横线上，剥掉尾横线+空格再接省略号（得「（中英字幕）…」而非「（中英字幕） -…」）。
- 模式标签=`skill/main.py _short_mode_label`：精简/详细/自定义/全文文案（query 也改为『自定义』，不再出现英文 mode）。

**改动文件**：`core/exporter.py`（`_safe_title` 组装顺序）+ `skill/main.py`（`_short_mode_label` query→自定义）+ 测试断言精确锁定单P 格式。
**验证**：精确字符串断言 PASS；`py_compile` OK + `test_issue_fixes` 5/5 + `test_language_auto` 3/3 + `simulate_new_user` 13/13。

---

## 续（17:08）GPU WinError 1455 根因坐实 + 默认回退 auto + 续传加固（commit e7f3a4e）

用户新要求：①转录默认 GPU、CPU 兜底 ②保证断点续传（GPU 崩 CPU 不从头）③可访问其电脑、列出配置问题待确认 ④解决 GPU 报错。

### 本机实查（PowerShell / 系统）
- GPU：NVIDIA RTX 4070（驱动 32.0.15.9186）——硬件完全支持 GPU 转录。
- 物理内存 47.8 GB；页面文件 C:初始1024/最大8192 MB、D:初始2048/最大8192 MB。
- **可用虚拟内存仅 ~2 GB**（FreeVirtualMemory≈2GB）→ **这才是 WinError 1455 的真正根因**：CUDA 库（cublas64_12.dll 等）加载时突发提交内存超过「提交上限=物理内存+页面文件」，而该上限已被其他进程吃满。属系统资源限制，**代码无法根治**。
- 实际 `config.yaml` 里 `device: auto`（用户机本就 GPU 优先）；上轮 `c1ef5c1` 我把**代码默认值**误改成 cpu，本次改回 auto。

### 代码改动（commit e7f3a4e，已本地提交未 push）
- `core/config.py` + `config/config.example.yaml`：`whisper.device` 默认由 `cpu` 改回 **`auto`**（自动检测 GPU、优先 GPU，失败自动回退 CPU）。
- `tests/test_resume.py`（新增）：真实临时 WAV + Mock 模型验证 `core/transcriber._transcribe_chunked` 断点续传——
  `resume_sec=0` 转录全块；给定已完成进度后只转录 `offset>=resume_sec` 的块，`model.transcribe` 仅被调用 **1/3**，
  证明 GPU 中途崩时 CPU 从断点续跑、**不从头**。
- 续传机制代码审查已确认健壮：`_transcribe_chunked` 每块完成即原子写 `progress.json`（写临时文件再 `os.replace`），父进程 `_transcribe_local` 重试前读 `resume_sec` 传 worker，worker 跳过已完成块（边界一致、不串音）。

### 待用户确认的配置项（未擅自改系统设置）
- **页面文件过小** → 建议调大到物理内存的 1.5–2 倍（用户机 48GB RAM，建议页面文件 ≥32GB，可设在 D: 盘避免占用 C: 系统盘）；根治 WinError 1455。**需管理员权限，且修改后重启生效**。
- 代码侧缓解已全部就位且无害：`CUDA_MODULE_LOADING=LAZY`（降低 CUDA 加载期页面文件峰值）+ `CT2_CUDA_ALLOCATOR=cuda_malloc_async`（压制显存碎片化）+ `OMP/MKL` 单线程。
- 备选方案：若不想动系统，可在 `config.yaml` 设 `whisper.mode: api` 走云端转录，彻底绕开本地显存/内存限制（但按分钟计费）。

**验证**：`py_compile` OK + `test_resume` 1/1 + `test_issue_fixes` 5/5 + `test_language_auto` 3/3 + `simulate_new_user` 13/13（零回归）。

---

## 下一步
- **远程 push**：待每日 23:00「VidGrab 每日 Git 上传提醒」触发、用户确认后执行（按既定 Git 工作流）。
- **GPU 报错根治**：待用户确认是否调大页面文件；确认后我可代为执行（需管理员+重启）。
- **真实环境验证**：本机重跑 BV1CW411P7rb 各分P，确认 ①默认走 GPU ②若 GPU 仍 1455 则自动回退 CPU 且从断点续跑 ③英文视频摘要随语种。
