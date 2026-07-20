# 如何获取 B站 SESSDATA（傻瓜式 · 全平台通用）

> 更新：2026-07-20
> 结论先行：**最推荐、最通用、对「非技术用户」最省事的办法，是用浏览器扩展
> 「Get cookies.txt LOCALLY」在 bilibili 页面导出整份 cookies.txt，然后粘贴给本工具，
> 工具会自动从中抽出 SESSDATA 并存好。B站 和 YouTube 用同一个扩展、同一套流程。**

---

## 为什么「打开一个链接 / 点一下书签」拿不到 SESSDATA？

B站的 `SESSDATA` 是浏览器的 **HttpOnly Cookie**。

- **网页脚本 / 书签（bookmarklet）读不到 HttpOnly Cookie**——这是浏览器的安全设计，
  所以「做个链接一点就复制」对 B站 SESSDATA 行不通（之前给的书签方式已确认无效）。
- **浏览器扩展可以读**（扩展申请了 `cookies` 权限，包含 HttpOnly）——所以装扩展就能拿到。

---

## 方法一（首选 · 通用）：Get cookies.txt LOCALLY 扩展

开源、本地处理不上传、Chrome / Edge / Brave（Chromium 内核）和 Firefox 都能用。
它导出的是**整份 Netscape cookies.txt**，本工具拿到后能自动解析出 SESSDATA。

1. 安装扩展（点链接 → 添加即可）：
   - Chrome / Edge / Brave：<https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc>
   - Firefox：<https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/>
2. 打开 <https://www.bilibili.com> 并**登录**你的账号。
3. 点扩展图标 → 选 **Netscape** 格式 → **导出**（或「复制」）当前网站的 cookies.txt。
4. 把整份 cookies.txt 内容**粘贴给本工具**（运行 `python -m skill` 时会提示）；
   工具自动抽出 SESSDATA 并存入 `config/config.yaml`，**下次免填**。

> 如果你更习惯手动：在扩展里也能直接看到 `SESSDATA` 这一行，复制它的 Value 单独粘贴也行。

---

## 方法二（备选）：Cookie-Editor 扩展

> 注：此前文档里给的 Cookie-Editor 链接 ID 拼错（`...lfohlijjcdo` 应为 `...dfddnkalmdm`），
> 已在此更正。抱歉给你造成困惑。

Cookie-Editor 能复制**单个** cookie 值（含 HttpOnly 的 SESSDATA），但**不能导出整份 cookies.txt**，
所以不是首选；适合只想单独复制 SESSDATA 的场景。

- Chrome：<https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm>
- Edge：<https://microsoftedge.microsoft.com/addons/detail/cookieeditor/neaplmfkghagebokpgfbieoobohfdjkl>

用法：打开 bilibili 页面（已登录）→ 点扩展图标 → 找到 `SESSDATA` → 复制 Value → 粘给工具。

---

## 方法三（兜底）：F12 开发者工具手动找

1. 浏览器登录 B站后，按 **F12** 打开开发者工具。
2. 点 **应用程序（Application）** 标签（英文界面叫 Application）。
3. 左侧展开 **Cookie** → 点 **bilibili.com** 域名。
4. 在名称列里 **Ctrl+F 搜索 `SESSDATA`**，双击它的「值」整段复制。
5. 粘给工具即可。

---

## 安全说明

- `SESSDATA` 是你账号的**登录凭证**，只存在本地 `config/config.yaml`
  （已被 git 忽略，不会上传；`core/notify.py` 同理不纳入版本控制）。
- 不想用了？在 B站网页点「退出登录」，**立刻失效**。
- 粘贴给工具的 cookies.txt 仅用于本地提取，不会外发。
