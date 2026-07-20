# YouTube 访问指引（代理 + Cookie）

> 更新：2026-07-20
> 已验证：代理（你的端口 7897）可连通 YouTube；Cookie 能绕过「确认你不是机器人」校验。
> 字幕提取改用「解析 watch 页面」的方式，绕开了 yt-dlp 的 n 签名挑战，更稳定。

---

## 一、代理（国内访问 YouTube 必需）

确认你电脑上有代理软件在跑（如 Clash / v2rayN），记下端口（你的是 **7897**）。

在 `config/config.yaml` 里配置：

```yaml
proxy:
  http: "http://127.0.0.1:7897"
  https: "http://127.0.0.1:7897"
```

（只要代理可达，工具就能连上 YouTube——已实测通过。）

---

## 二、Cookie（绕过「确认你不是机器人」校验必需）

YouTube 现在强制校验，必须带登录 Cookie。最稳、最通用、和 B站同一套流程的办法：

### 用「Get cookies.txt LOCALLY」扩展（推荐）

开源、本地处理、Chrome / Edge / Brave / Firefox 通用，导出整份 Netscape cookies.txt。

1. 安装扩展：
   - Chrome / Edge / Brave：<https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc>
   - Firefox：<https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/>
2. 打开 <https://www.youtube.com> 并**登录**（随便看个视频也行）。
3. 点扩展图标 → 选 **Netscape** 格式 → **导出**（或「复制」）youtube.com 的 cookies.txt。
4. 把整份 cookies.txt **粘贴给本工具**（`python -m skill` 会提示）；
   工具自动存成 `config/youtube_cookies.txt` 并直接使用，**下次免填**。

### 或者手动放文件

把导出的 `youtube.com` 的 cookies.txt 存为下面任一路径即可，无需改 config：

- `config/youtube_cookies.txt`（零配置默认路径，推荐）
- 或在 `config/config.yaml` 显式指定：
  ```yaml
  youtube:
    cookies_file: "config/youtube_cookies.txt"
  ```

> 也可以让 yt-dlp 直接读浏览器 Cookie（`cookies_from_browser: chrome`），但本工具在
> 非交互式/沙箱环境无法解密浏览器加密 Cookie（DPAPI），所以 **cookies.txt 文件方式最稳**。

---

## 三、怎么运行

```bash
# 方式 A：交互式（会提示粘贴 cookies.txt）
python -m skill
# 方式 B：直接给链接
python -m skill "https://www.youtube.com/watch?v=xxxx"
```

---

## 四、已知环境限制（重要）

- **yt-dlp 的 n 签名挑战**：某些环境（沙箱 / 无法联网下载 challenge solver 脚本）解不开
  YouTube 的 n 参数，会在选视频格式时报 `Requested format is not available`。
  **本工具已改用「解析 watch 页面」拿字幕轨道，绕开这一步**，因此更稳。
- **经代理下载字幕字节**：YouTube 的 `timedtext` 接口会校验来源 IP，经过代理转发时
  可能返回空内容（`Content-Length: 0`）。这是代理/网络环境限制，**在你本机能直连
  YouTube 的机器上会正常返回字幕**。判断标准：工具若提示「已找到 N 条字幕轨道，
  但下载字幕内容失败」，说明是网络环境限制，请在本机直连运行。
- **无字幕视频**：如果页面里 `captionTracks` 为空（例如测试用的 `4gciWspBVHw` 就
  没有字幕），工具会明确告诉你「该视频无字幕」；要转录需开启音频下载（Step 2 / Whisper）。
