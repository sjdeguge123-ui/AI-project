# VidGrab 新手从零开始（傻瓜式教程）

> 适用对象：会一点电脑、会用鼠标键盘、但**没写过代码**也能跟着做。
> 当前版本能力：粘贴一个 **B站视频链接** → 自动生成**带时间戳的结构化摘要**（有字幕 / 无字幕都支持）。
> 最后更新：2026-07-20

---

## 0. 先看你最终要装哪些东西（一张表看明白）

| 要装的东西 | 必须吗 | 大概体积 | 用来干啥 | 什么时候才需要 |
|---|---|---|---|---|
| Python 3.11+ | ✅ 必须 | ~50 MB | 运行工具的地基 | 所有视频 |
| VidGrab 代码 | ✅ 必须 | 几 MB | 工具本身 | 所有视频 |
| 依赖包（yt-dlp 等） | ✅ 必须 | 几百 MB | 各功能模块 | 所有视频 |
| AI Key（硅基流动 / DeepSeek） | ✅ 必须 | **免费** | 把文字变成摘要 | 所有视频 |
| ffmpeg | ⚠️ 仅无字幕 | ~160 MB | 处理音频文件 | 只看**无字幕**视频才要 |
| Whisper 模型 | ⚠️ 仅无字幕 | ~145 MB（base） | 语音转文字 | 第一次处理无字幕视频时**自动下载** |
| B站 SESSDATA | ⚠️ 仅B站字幕 | **免费** | 取 B站「真字幕」 | 只看**有字幕的 B站**视频才要 |

**一句话总结：**
- 纯看【**有字幕的 B站视频**】→ 装 **Python + 代码 + 依赖 + AI Key** 就够。
- 要看【**无字幕视频**】→ 上面基础上**再多装 ffmpeg**（Whisper 模型第一次自动下，不用你管）。

> 整个流程**不翻墙也能用**：AI 用国内的硅基流动/DeepSeek，B站国内直连。只有 YouTube 才需要代理（本期先不重点用）。

---

## 1. 安装 Python（一次性）

1. 打开 https://www.python.org/downloads/ ，下载 **Python 3.11 或更高版本** 的 Windows 安装包。
2. 双击安装，**务必勾选最下面的「Add python.exe to PATH」**（这步最关键，不勾后面会报错）。
3. 一路点「Install Now」。
4. 验证：按 `Win + R` → 输入 `cmd` 回车，在黑框里输入：
   ```
   python --version
   ```
   能看到 `Python 3.11.x` 这类字样就成功了。

---

## 2. 拿到 VidGrab 代码（一次性）

- **方式 A（推荐，需装 Git）**：在 cmd 里 `git clone <仓库地址>` 然后 `cd VidGrab`。
- **方式 B（最简单）**：在网页上点「Download ZIP」下载，解压到任意文件夹（比如 `D:\VidGrab`）。
- 进入项目文件夹：在文件夹地址栏输入 `cmd` 回车，就在这个目录打开了命令行。

---

## 3. 装依赖（一次性）

在项目文件夹的 cmd 里，依次输入：

```
python -m venv .venv
.venv\Scripts\activate
pip install yt-dlp bilibili-api-python requests openai faster-whisper PyYAML
```

- 第 1 行：建一个独立的运行环境（避免把你电脑其他 Python 弄乱）。
- 第 2 行：进入这个环境（之后命令行前面会出现 `(.venv)` 字样，表示成功）。
- 第 3 行：安装核心依赖。会下载几百 MB，耐心等它跑完。

> 如果你**也要用 YouTube**，再补一句：`pip install curl_cffi`
>
> ⚠️ 文档里提到的 Word/PDF/图片导出、MCP 等是**以后才有的功能**，现在不用装，装了反而可能因为缺系统库而失败。

---

## 4. （仅看无字幕视频）安装 ffmpeg

无字幕视频需要先把音频「抽」出来，这一步依赖 ffmpeg。

**最简单：用 winget 一键装（Win10/11 自带）**
```
winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
```
装完**重开一个 cmd**，输入 `ffmpeg -version` 能看到版本号就成功了。

**如果 winget 不行，手动装：**
1. 打开 https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip 下载（约 160 MB）。
2. 解压，会得到 `ffmpeg-master-latest-win64-gpl\bin\` 文件夹，里面有三个 exe。
3. 把这个 `bin` 文件夹的完整路径（例如 `C:\ffmpeg\bin`）加到系统环境变量 PATH（搜「编辑系统环境变量」→ 环境变量 → 用户变量里找 Path → 编辑 → 新建 → 粘贴路径 → 一路确定）。
4. 重开 cmd，输入 `ffmpeg -version` 验证。

---

## 5. 配置 AI Key（生成摘要必须，免费）

摘要文字是由 AI 生成的，你需要一个自己的 Key（**开发者看不到你的 Key，各用各的**）。

**推荐：硅基流动（国内直连、新用户有免费额度）**
1. 打开 https://siliconflow.cn 注册登录。
2. 右上角 → 「API 密钥」→「新建密钥」→ 复制那串 `sk-xxx`。
3. 先放着，下一步运行时粘进去就行（也可以现在就填进 `config/config.yaml` 的 `ai.api_key`）。

> 备选：DeepSeek（https://platform.deepseek.com ，同样国内可用）。

---

## 6. （仅看有字幕的 B站）配置 B站 SESSDATA

B站的「真字幕」必须登录才能取，所以需要你的登录凭证（**免费、本地保存**）。

1. 在 Chrome / Edge 装浏览器扩展 **「Get cookies.txt LOCALLY」**（开源、本地处理，不联网上传）。
2. 打开你要处理的那个 **B站视频网页**，点扩展图标 → 导出 cookies.txt（整页内容）。
3. 运行工具时它会让你粘贴这段内容，工具自动抽出 SESSDATA 存好，**下次不用再粘**。

> 为什么不能「一键复制」？因为 B站 SESSDATA 是 HttpOnly Cookie，网页脚本读不到，必须靠扩展导出。

---

## 7. 运行！（每次用都这样）

在 VidGrab 项目文件夹的 cmd 里：

```
.venv\Scripts\activate
python -m skill "这里粘贴视频链接"
```

例如：
```
python -m skill "https://www.bilibili.com/video/BV1JLNd6MECG/"
```

- **第一次运行**会交互式问你：① 粘贴 AI Key（第 5 步那个）② 如果是 B站字幕，粘贴 cookies.txt（第 6 步那个）。粘一次以后就记住了。
- 然后它会：提取字幕/音频 →（无字幕就转录）→ AI 生成摘要 → 存成文件。
- 跑完会告诉你文件保存在哪。

---

## 8. 成果在哪、长什么样

- 文件在项目的 `output\` 文件夹，名字像 `bilibili_BVxxxx.md`。
- 用记事本 / Typora / VS Code 打开就能看，结构是：

```
## 视频摘要：标题
### 基本信息（来源/作者/时间/时长/概述）
### 内容脉络（时间表：时间 | 核心要点 | 内容 | 备注）
### 金句（值得摘抄的几句话）
### 总结
```

> 「备注」那一列是**留给你自己写笔记的**，工具不会填，你之后手动补。

---

## 9. 无字幕视频怎么跑（和上面完全一样）

还是同一条命令：
```
python -m skill "无字幕视频链接"
```
工具会自动发现「这视频没字幕」→ 下载音频 → 本地 Whisper 转成文字 → 再生成摘要。

- **第一次**处理无字幕视频会自动下载 Whisper 模型（约 145 MB，需联网，之后不用再下）。
- 本地转录**吃 CPU**，视频越长越久（几分钟到十几分钟都正常），是免费换时间。
- 想更快/更准：改 `config/config.yaml` 的 `whisper.local_model`（`tiny` 最快、`small` 更准）。
- 不想用本地转录：把 `whisper.mode` 改成 `api` 并填 OpenAI Key（按分钟收费）。

> ⚠️ **模型下载卡住 / 连不上 huggingface.co？** 多数情况下它会自动下好，不用管。如果真的卡住，按这个顺序试：
> 1. **最稳的退路——改用云端转录（推荐）**：编辑 `config/config.yaml`，把
>    `whisper.mode` 改成 `api`，并填好 OpenAI Key。这样**完全不需要下载本地模型**，
>    由 OpenAI 云端把音频转成文字，按分钟计费（约 $0.006/分钟）。
> 2. 想继续用本地模型，可临时切国内镜像（仅本次生效，每次开新 cmd 要重设）：
>    ```
>    set HF_ENDPOINT=https://hf-mirror.com
>    ```
>    注意：镜像有时有效、有时会被弹回官方站；若你处在公司/学校等做了 **SSL 拦截**的网络，
>    镜像也帮不上，请直接用第 1 条「云端转录」绕过。
> 3. 模型只要成功下过一次，就缓存在 `~/.cache/huggingface`，以后不用再下。

---

## 10. 常见问题对照表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 报错「未检测到 ffmpeg」 | 没装 ffmpeg 或没加 PATH | 回到第 4 步重装，重开 cmd 验证 `ffmpeg -version` |
| B站提示要登录 / 说没字幕 | SESSDATA 过期了 | 重新用扩展导出 cookies.txt 粘贴（第 6 步） |
| 摘要报错 401 / Authentication | AI Key 错了 | 检查 `config.yaml` 的 `ai.api_key`，重新复制 `sk-` 开头的 key |
| 无字幕视频跑了很久 | 正常，CPU 本地转录 | 耐心等；或换 `whisper.mode: api`（云端，需 OpenAI Key） |
| 下载 Whisper 模型报连不上 huggingface.co | 国内访问不稳 / 网络 SSL 拦截 | 最稳：把 `whisper.mode` 改成 `api` 用云端转录（见第 9 步）；或试 `set HF_ENDPOINT=https://hf-mirror.com` |
| `python` 不是内部命令 | 第 1 步没勾 PATH | 重装 Python 并勾选「Add python.exe to PATH」 |
| 输出文件是空的 | AI 返回异常 | 看 cmd 报错，多半是 Key 或网络问题 |

---

*本教程对应「当前可用版本」。Word / PDF / 图片导出、浏览器插件、小程序等是后续版本功能。*
