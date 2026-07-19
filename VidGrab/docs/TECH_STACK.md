# VidGrab 技术栈说明

> 最后更新：2026-07-19

---

## 一、编程语言：Python

**为什么选 Python？**

本项目的核心能力（AI调用、音视频处理、文档导出）所依赖的开源库几乎全部是 Python 生态。选 Python 可以直接使用这些成熟库，不需要重复造轮子。

---

## 二、核心模块技术栈

### 2.1 视频链接解析与字幕提取

| 库 | 用途 | 说明 |
|---|---|---|
| **yt-dlp** | 视频/字幕提取 | 支持 1000+ 网站（YouTube、抖音、快手等），活跃维护的开源项目 |
| **bilibili-api** | B站专项 | B站字幕、视频信息提取更稳定，专为B站设计 |

**选型理由**：yt-dlp 是目前最广泛使用的视频下载/字幕提取工具，社区活跃，持续跟进各平台变化。bilibili-api 对B站的支持比 yt-dlp 更深入。

### 2.2 语音转文字（无字幕视频）

| 库 | 用途 | 说明 |
|---|---|---|
| **OpenAI Whisper API** | 云端语音转文字 | 用户自备 OpenAI API Key，按量付费，约 $0.006/分钟 |
| **faster-whisper** | 本地语音转文字 | 在用户本地电脑运行，免费，需首次下载模型文件（约几百MB） |

**选型理由**：Whisper 是目前精度最高的开源语音识别模型，支持 99+ 语言。两种模式让用户自选，满足不同需求。

### 2.3 AI 摘要生成

| 库 | 用途 | 说明 |
|---|---|---|
| **LiteLLM** | 统一 AI 调用接口 | 相当于 AI 服务的"万能适配器"，写一次代码，支持所有主流模型 |

**支持的 AI 服务**：
- DeepSeek（推荐，国内外可用，有免费额度）
- OpenAI（GPT-4o / GPT-4o-mini）
- Anthropic（Claude 3.5）
- Google（Gemini 1.5 Flash）
- 阿里云（通义千问 Qwen）

**选型理由**：LiteLLM 让用户换模型时不需要改代码，只需修改配置文件中的模型名称即可。

### 2.4 文档导出

| 库 | 用途 | 说明 |
|---|---|---|
| **python-docx** | 生成 Word 文档（.docx） | 成熟稳定，功能完整 |
| **WeasyPrint** | 生成 PDF | 通过 HTML/CSS 渲染 PDF，样式灵活 |
| **Pillow** | 生成图片（.png） | Python 图像处理库，用于渲染文字卡片 |

---

## 三、交付形态技术栈

### 3.1 CodeBuddy Skill（第一阶段）

| 文件 | 说明 |
|---|---|
| `skill/skill.json` | Skill 描述文件，定义工具名称、参数、入口 |
| `skill/main.py` | Skill 入口，调用 core 模块 |

### 3.2 MCP Server（第一阶段，同步交付）

| 文件 | 说明 |
|---|---|
| `mcp_server/server.py` | 标准 MCP 协议服务端，供 Claude/Cursor/Windsurf/Kimi 等调用 |

**MCP（Model Context Protocol，模型上下文协议）**：Anthropic 推出的开放标准，让不同 AI 工具能统一调用外部工具，目前 Claude、Cursor、Windsurf、Kimi、通义千问等均已支持。

### 3.3 浏览器插件（第二阶段）

| 技术 | 说明 |
|---|---|
| **Manifest V3** | Chrome/Edge/Firefox 统一的插件标准 |
| HTML + JavaScript | 插件 UI 和逻辑 |

### 3.4 微信小程序（第三阶段）

| 技术 | 说明 |
|---|---|
| **uni-app** | 一套代码同时编译为微信小程序和 H5，减少重复开发 |
| **FastAPI** | Python Web 框架，小程序需要后端 API 服务 |

### 3.5 Web 版（第四阶段）

| 技术 | 说明 |
|---|---|
| **Next.js** | React 框架，支持服务端渲染，对 SEO 友好，部署到 Vercel 免费 |
| **FastAPI** | 复用小程序阶段的后端 |
| **PostgreSQL** | 用户数据、使用记录存储 |
| **Redis** | 任务队列（视频处理是耗时操作，异步处理） |

---

## 四、基础设施

| 工具 | 用途 | 说明 |
|---|---|---|
| **GitHub** | 代码托管（主库） | 国际用户可见，开源曝光 |
| **Gitee** | 代码托管（国内镜像） | 国内访问速度快 |
| **GitHub Actions** | CI/CD 自动化 | 代码推送后自动测试 |
| **Vercel** | Web 前端部署 | 免费，全球 CDN |
| **uvx** | Python 工具一键运行 | 用户安装 MCP Server 时使用，无需手动配置环境 |

---

## 五、依赖安装

```bash
pip install -r requirements.txt
```

详细依赖见 `requirements.txt`（开发阶段逐步完善）。

---

# VidGrab Tech Stack

> Last updated: 2026-07-19

---

## 1. Language: Python

All core dependencies (AI calls, audio/video processing, document export) are Python-native. Python lets us use mature libraries without reinventing the wheel.

---

## 2. Core Module Stack

### 2.1 Video & Subtitle Extraction

| Library | Purpose | Notes |
|---|---|---|
| **yt-dlp** | Video/subtitle extraction | Supports 1000+ sites (YouTube, Douyin, Kuaishou, etc.), actively maintained |
| **bilibili-api** | Bilibili-specific | More stable for B站 subtitle and metadata extraction |

### 2.2 Speech-to-Text (videos without subtitles)

| Library | Purpose | Notes |
|---|---|---|
| **OpenAI Whisper API** | Cloud speech-to-text | Requires user's OpenAI API Key, ~$0.006/min |
| **faster-whisper** | Local speech-to-text | Runs on user's machine, free, needs one-time model download (~hundreds of MB) |

### 2.3 AI Summary Generation

| Library | Purpose | Notes |
|---|---|---|
| **LiteLLM** | Unified AI interface | Write once, support all major models |

**Supported AI services**: DeepSeek (recommended), OpenAI, Claude, Gemini, Qwen

### 2.4 Document Export

| Library | Purpose | Notes |
|---|---|---|
| **python-docx** | Word (.docx) export | Stable and full-featured |
| **WeasyPrint** | PDF export | HTML/CSS-based rendering, flexible styling |
| **Pillow** | Image (.png) export | Used to render clean text cards |

---

## 3. Delivery Form Stack

- **Phase 1**: CodeBuddy Skill + MCP Server
- **Phase 2**: Browser Extension (Manifest V3)
- **Phase 3**: WeChat Mini Program (uni-app + FastAPI)
- **Phase 4**: Web App (Next.js + FastAPI + PostgreSQL + Redis)

---

## 4. Infrastructure

| Tool | Purpose |
|---|---|
| GitHub | Main code hosting |
| Gitee | China mirror |
| GitHub Actions | CI/CD |
| Vercel | Web frontend deployment (free) |
| uvx | One-click Python tool runner for MCP Server |
