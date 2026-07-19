# VidGrab 使用手册

> 最后更新：2026-07-19 | 版本：开发中

---

## 一、安装方式

### 方式A：CodeBuddy Skill（推荐，最简单）

1. 打开 CodeBuddy
2. 进入 Skill 市场
3. 搜索 `VidGrab`
4. 点击安装

### 方式B：其他 Agent（Claude / Cursor / Windsurf / Kimi 等）

1. 从 GitHub Releases 下载安装包
2. Windows 用户双击运行 `install_windows.bat`
3. Mac 用户双击运行 `install_mac.sh`
4. 按提示选择你使用的 AI 工具，自动完成配置
5. 重启你的 AI 工具

---

## 二、配置 API Key

首次使用时，工具会引导你配置 AI Key。

### 推荐：DeepSeek（国内外均可使用，新用户有免费额度）

1. 访问 [platform.deepseek.com](https://platform.deepseek.com)
2. 注册账号（支持手机号/邮箱）
3. 登录后进入「API Keys」页面
4. 点击「创建 API Key」
5. 复制生成的 Key（格式：`sk-xxxxxxxxxxxxxxxx`）
6. 粘贴到 VidGrab 配置界面

### 其他支持的 AI 服务

| 服务 | 注册地址 | 国内可用 |
|---|---|---|
| OpenAI | platform.openai.com | 需梯子 |
| Claude | console.anthropic.com | 需梯子 |
| Gemini | aistudio.google.com | 需梯子 |
| 通义千问 | dashscope.aliyun.com | ✅ |

---

## 三、使用方法

在 AI 工具的对话框输入：

```
总结这个视频 [粘贴视频链接]
```

例如：
```
总结这个视频 https://www.bilibili.com/video/BV1xx411c7mD
```

VidGrab 会自动处理并返回带时间戳的结构化摘要。

---

## 四、导出格式

摘要生成后，可选择导出为：

- **Markdown (.md)**：默认格式，适合进一步编辑
- **PDF (.pdf)**：适合存档、打印
- **Word (.docx)**：适合二次编辑
- **图片 (.png)**：简洁文字卡片，适合分享

---

## 五、支持的平台

| 平台 | 支持情况 |
|---|---|
| YouTube | ✅ 稳定支持 |
| B站 | ✅ 稳定支持 |
| 抖音 | ⚠️ 尽力支持 |
| 快手 | ⚠️ 尽力支持 |
| 腾讯视频/爱奇艺/优酷 | ❌ 不支持 |

---

*更多内容持续更新中...*
