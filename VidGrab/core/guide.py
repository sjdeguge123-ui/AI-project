"""用户引导模块（core/guide.py）

当提取遇到「平台限制」时，给低技术用户看得懂、能照做的引导。
目前覆盖两类最常见的阻塞：
  - B站  ：真字幕需要登录（提供 SESSDATA cookie）
  - YouTube：国内需要代理才能访问

B站 SESSDATA 是 **HttpOnly Cookie**，网页脚本和「一键书签」都读不到，
所以本工具改用「浏览器扩展 Cookie-Editor 复制 + 工具交互式询问」的方式获取，
最稳、最傻瓜。交互式获取逻辑见 core/auth.py (get_bilibili_sessdata)，
完整图文步骤见 docs/BILIBILI_SESSDATA.md。
"""

from __future__ import annotations

from .auth import COOKIE_EDITOR_CHROME, COOKIE_EDITOR_EDGE


def bilibili_login_guide() -> str:
    """返回「如何获取 B站 SESSDATA」的引导文案（面向非技术用户，扩展 + 交互式）。"""

    return (
        "【B站字幕需要登录】这个视频的字幕需要登录你的 B站账号才能获取。\n\n"
        "★ 傻瓜式（推荐，约 1 分钟）\n"
        "  1. 在浏览器装扩展 Cookie-Editor（点链接 → 添加）：\n"
        f"     Chrome : {COOKIE_EDITOR_CHROME}\n"
        f"     Edge  : {COOKIE_EDITOR_EDGE}\n"
        "  2. 打开 https://www.bilibili.com 并登录\n"
        "  3. 点浏览器右上角 Cookie-Editor 图标 → 找到 SESSDATA 那行 → 复制 Value\n"
        "  4. 运行本工具时，它会交互式问你要 SESSDATA，直接粘贴进去即可\n"
        "     （会自动存到 config.yaml，下次免填）\n\n"
        "★ 手动方式（F12 找 Cookie，面板可能是英文，一定有效但较麻烦）\n"
        "  1. 浏览器登录 B站后，按 F12 打开「开发者工具」\n"
        "  2. 点顶部「应用程序」标签（英文界面叫 Application）\n"
        "  3. 左侧展开「Cookie」（英文叫 Cookies）→ 点下面的 bilibili.com 域名\n"
        "  4. 右侧会列出很多行；在「名称(Name)」列里找到 SESSDATA 这一行\n"
        "     （可按 Ctrl+F 搜索 SESSDATA 快速定位）\n"
        "  5. 双击它「值(Value)」那一格，整段复制，粘到工具提示里\n\n"
        "为什么不用「一键书签」？SESSDATA 是 HttpOnly Cookie，网页 JS 读不到，\n"
        "书签方式十有八九会提示「找不到 SESSDATA」，所以改用上面的扩展方式。\n\n"
        "安全说明：SESSDATA 是你自己账号的登录凭证，只存在本地 config.yaml\n"
        "（已被 git 忽略，不会上传）。不想用了？在 B站网页点「退出登录」，立刻失效。"
    )


def youtube_proxy_guide() -> str:
    """返回「如何让工具通过代理访问 YouTube」的引导文案（面向非技术用户）。"""

    return (
        "【YouTube 需要代理】当前环境访问不了 YouTube（国内网络限制）。\n"
        "最简单的解法（二选一）：\n\n"
        "A. 如果你电脑上开了代理软件（如 Clash / v2rayN）：\n"
        "   看它的「端口」，通常是 7890。打开 config/config.yaml，加一行：\n"
        '     proxy: "http://127.0.0.1:7890"\n'
        "   保存后重新运行。\n\n"
        "B. 或者在运行前，先在命令行设好代理（临时生效）：\n"
        "   Windows：  set HTTPS_PROXY=http://127.0.0.1:7890\n"
        "   Mac/Linux：export HTTPS_PROXY=http://127.0.0.1:7890\n"
        "   然后再运行本工具。\n\n"
        "如果你还没有代理软件，需要先准备一个（这部分不在本工具范围内）。"
    )
