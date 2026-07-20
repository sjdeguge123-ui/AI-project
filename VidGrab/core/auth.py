"""交互式获取登录凭证（core/auth.py）

B站 SESSDATA 和 YouTube 字幕，都需要你浏览器里已登录的 Cookie。
最稳、最通用、对「非技术用户」最友好的办法，是装一个浏览器扩展
「Get cookies.txt LOCALLY」(开源、本地处理、Chrome/Edge/Brave/Firefox 通用)，
在对应网站页面点一下导出整份 Netscape cookies.txt，然后把内容粘贴进来：

  - B站：粘贴 bilibili.com 的 cookies.txt → 这里自动抽出 SESSDATA 存好，下次免填。
  - YouTube：粘贴 youtube.com 的 cookies.txt → 这里存成 config/youtube_cookies.txt，直接用。

注意：SESSDATA 是浏览器的 HttpOnly Cookie，网页脚本/书签读不到，所以
「打开一个链接自动复制」对 B站 不可行；统一用「扩展导出 + 粘贴」最稳。
粘贴的内容仅保存在本地（config.yaml / config/youtube_cookies.txt，均已被 git 忽略），
不上传、不外发。

本模块对外提供：
  - get_bilibili_sessdata()         ：返回可用的 SESSDATA（config 优先；否则交互式粘贴）
  - get_youtube_cookies_file()      ：返回 cookies.txt 路径（config 优先；否则交互式粘贴并保存）
  - setup_ai()                      ：首次运行引导配置 AI 服务商与 Key（每个人用自己的 Key）
  - ensure_config_file()            ：config.yaml 缺失时从模板自举，别人下载即用
  - parse_netscape_cookies(text)    ：Netscape cookies.txt → {name: value}
  - extract_sessdata_from_cookie_text(text)：从粘贴内容抽取 SESSDATA（支持 cookies.txt / SESSDATA=xxx / 原始值）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import config as _config

# 「Get cookies.txt LOCALLY」扩展一键安装链接（开源、本地处理、跨浏览器通用）
GET_COOKIES_TXT_CHROME = "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
GET_COOKIES_TXT_FIREFOX = "https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/"
# Cookie-Editor（备选用，能复制单个 cookie 值；但不能导出整份 cookies.txt，故不是首选）
COOKIE_EDITOR_CHROME = "https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm"
COOKIE_EDITOR_EDGE = "https://microsoftedge.microsoft.com/addons/detail/cookieeditor/neaplmfkghagebokpgfbieoobohfdjkl"


# ----------------------------------------------------------------------------
# Cookie 文本解析
# ----------------------------------------------------------------------------

def parse_netscape_cookies(text: str) -> dict:
    """把 Netscape 格式 cookies.txt 解析成 {name: value}。

    格式示例：
        # Netscape HTTP Cookie File
        .youtube.com\tTRUE\t/\tTRUE\t1819068912\tPREF\tf7=...
    忽略 # 注释行与空行；按制表符切分取第 6(name)、7(value) 列。
    """
    cookies: dict[str, str] = {}
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def extract_sessdata_from_cookie_text(text: str) -> str:
    """从用户粘贴的内容里抽取 B站 SESSDATA，支持三种输入：

    1) 整份 Netscape cookies.txt（含 .bilibili.com 的 SESSDATA 行）
    2) 一行 'SESSDATA=xxxx' 形式
    3) 直接就是 SESSDATA 值本身（无空格、较长）
    """
    text = text.strip()
    # 情况 1：Netscape cookies（含制表符或 # 头）
    if "\t" in text or text.startswith("#"):
        cookies = parse_netscape_cookies(text)
        if "SESSDATA" in cookies:
            return cookies["SESSDATA"]
        raise ValueError("在粘贴的 cookies 里没找到 SESSDATA（请确认是在 bilibili.com 页面导出的）")
    # 情况 2：含 SESSDATA=xxx
    m = re.search(r"SESSDATA=([^;\s]+)", text)
    if m:
        return m.group(1)
    # 情况 3：看起来就是原始值
    if " " not in text and "\n" not in text and len(text) > 20:
        return text
    raise ValueError("没认出 SESSDATA：请粘贴『整份 cookies.txt』，或『SESSDATA=xxx』，或直接粘 SESSDATA 值")


# ----------------------------------------------------------------------------
# 本地保存
# ----------------------------------------------------------------------------

def _config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _config_example_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "config.example.yaml"


def ensure_config_file() -> bool:
    """配置自举：若 config/config.yaml 不存在，从 config.example.yaml 复制一份。

    这样「别人下载项目后直接运行」无需手动 cp，工具会自动生成空白配置，
    再由各 setup_* 向导引导用户填入自己的 Key / Cookie。返回是否刚创建了文件。
    """
    cfg_path = _config_path()
    if cfg_path.exists():
        return False
    example = _config_example_path()
    if not example.exists():
        return False
    import shutil
    shutil.copyfile(example, cfg_path)
    print(f"📄 已从模板生成配置文件：{cfg_path}\n   接下来按提示填入你自己的 Key / Cookie（不会上传）。")
    return True


def _patch_config(updates: dict) -> None:
    """把若干字段写回 config.yaml（嵌套 dict 合并，保留其它内容）。

    用于各 setup_* 向导把用户填的内容持久化，下次运行免填。
    """
    import yaml

    path = _config_path()
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return

    def _merge(d: dict, u: dict) -> None:
        for k, v in u.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                _merge(d[k], v)
            else:
                d[k] = v

    _merge(data, updates)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _save_sessdata_to_config(value: str) -> None:
    """把 SESSDATA 写回 config/config.yaml 的 bilibili.sessdata（该文件已被 git 忽略）。"""
    path = _config_path()
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    lines = []
    replaced = False
    for line in text.splitlines():
        if not replaced and line.strip().startswith("sessdata:"):
            lines.append(f'  sessdata: "{value}"')
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append('  sessdata: "%s"' % value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_youtube_cookies_file(text: str) -> Path:
    """把用户粘贴的 cookies.txt 内容存到 config/youtube_cookies.txt（已被 git 忽略）。"""
    path = _config_path().parent / "youtube_cookies.txt"
    # 兜底：若粘贴的是被 markdown 包裹的文本，剥掉首尾 ``` 围栏
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()
    path.write_text(cleaned + "\n", encoding="utf-8")
    return path


def load_cookies_file(path: str) -> dict:
    """读取 Netscape cookies.txt 为 {name: value}，供 requests 使用。"""
    p = Path(path)
    if not p.exists():
        return {}
    return parse_netscape_cookies(p.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# 交互式获取
# ----------------------------------------------------------------------------

def _interactive_input(prompt: str) -> str:
    """仅当在真正的交互式终端里才阻塞等待输入；否则返回空字符串。"""
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def get_bilibili_sessdata(force_prompt: bool = False) -> str:
    """返回可用的 B站 SESSDATA。

    - config 里已有且非强制重输 → 直接返回；
    - 否则交互式提示：用「Get cookies.txt LOCALLY」在 bilibili 页面导出 cookies.txt，
      粘贴进来 → 自动抽出 SESSDATA 并存到 config.yaml，下次免填。
    - 非交互环境（沙箱/管道）不阻塞，返回空字符串，由上层给出引导文案。
    """
    cfg = _config.load_config()
    existing = (cfg.bilibili.sessdata or "").strip()
    if existing and not force_prompt:
        return existing

    if not sys.stdin.isatty():
        return ""

    print("=" * 64)
    print("需要你的 B站 SESSDATA（用于获取「真字幕」）")
    print("=" * 64)
    print("最稳的获取方式（约 1 分钟，全平台通用）：")
    print("  1) 装扩展 Get cookies.txt LOCALLY（点链接 → 添加）：")
    print(f"     Chrome/Edge/Brave : {GET_COOKIES_TXT_CHROME}")
    print(f"     Firefox           : {GET_COOKIES_TXT_FIREFOX}")
    print("  2) 打开 https://www.bilibili.com 并登录你的账号")
    print("  3) 点扩展图标 → 选 Netscape 格式 → 导出（或复制）cookies.txt")
    print("  4) 把整份 cookies.txt 内容粘贴到下面，回车即可（自动抽 SESSDATA 并存好）")
    print("-" * 64)

    while True:
        val = _interactive_input("请粘贴 cookies.txt 或 SESSDATA（直接回车取消）：")
        if not val:
            return ""
        try:
            sess = extract_sessdata_from_cookie_text(val)
        except ValueError as e:
            print(f"⚠️ {e}")
            continue
        if len(sess) < 20:
            print("⚠️ 这段太短了，SESSDATA 通常很长（含逗号、约 200+ 字符）。请重新粘贴。")
            continue
        _save_sessdata_to_config(sess)
        print("✅ 已保存到 config/config.yaml，下次运行免填。")
        return sess


def get_youtube_cookies_file() -> str:
    """返回可用的 YouTube cookies.txt 路径。

    - config.youtube.cookies_file 指定了 → 用它；
    - 否则若项目里存在 config/youtube_cookies.txt（零配置默认路径）→ 用它；
    - 否则交互式提示：用「Get cookies.txt LOCALLY」在 youtube 页面导出 cookies.txt，
      粘贴进来 → 存成 config/youtube_cookies.txt 并返回路径。
    - 非交互环境返回空字符串。
    """
    cfg = _config.load_config()
    if cfg.youtube and cfg.youtube.cookies_file and Path(cfg.youtube.cookies_file).exists():
        return cfg.youtube.cookies_file
    default_path = _config_path().parent / "youtube_cookies.txt"
    if default_path.exists():
        return str(default_path)
    if not sys.stdin.isatty():
        return ""

    print("=" * 64)
    print("YouTube 需要登录 Cookie 才能绕过「确认你不是机器人」")
    print("=" * 64)
    print("获取方式（全平台通用）：")
    print("  1) 装扩展 Get cookies.txt LOCALLY（点链接 → 添加）：")
    print(f"     Chrome/Edge/Brave : {GET_COOKIES_TXT_CHROME}")
    print(f"     Firefox           : {GET_COOKIES_TXT_FIREFOX}")
    print("  2) 打开 https://www.youtube.com 并登录（看个视频也行）")
    print("  3) 点扩展图标 → 选 Netscape 格式 → 导出（或复制）youtube.com 的 cookies.txt")
    print("  4) 把整份 cookies.txt 内容粘贴到下面，回车即可（自动存好，下次免填）")
    print("-" * 64)

    val = _interactive_input("请粘贴 youtube.com 的 cookies.txt（直接回车取消）：")
    if not val:
        return ""
    try:
        path = save_youtube_cookies_file(val)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 保存失败：{e}")
        return ""
    print(f"✅ 已保存到 {path}，下次运行免填。")
    return str(path)


# ----------------------------------------------------------------------------
# AI 服务商 Key 首次引导（每个人用自己的 Key，开发者不介入）
# ----------------------------------------------------------------------------

# 可选服务商及其默认 base_url / 模型，用户填 Key 时自动套用
_AI_PROVIDERS = {
    "1": {
        "name": "硅基流动 SiliconFlow（国内直连，DeepSeek-V3 免费）",
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
    },
    "2": {
        "name": "DeepSeek（国内直连）",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "3": {
        "name": "OpenAI（海外通用，需代理）",
        "provider": "openai",
        "base_url": "",
        "model": "gpt-4o-mini",
    },
    "4": {
        "name": "本地 Ollama（完全离线，需自托管）",
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5",
    },
}


def setup_ai(force_prompt: bool = False):
    """首次运行引导用户配置 AI 摘要服务商与 Key。

    - 已配置 ai.api_key 且非强制重填 → 直接返回现有 AIConfig；
    - 否则交互式让用户选服务商（硅基流动/DeepSeek/OpenAI/本地 Ollama）、填自己的 Key，
      写回 config.yaml，下次免填；
    - 非交互环境（沙箱/管道）不阻塞，原样返回，由 summarizer 给清晰报错。

    返回 AIConfig（可能被就地更新过）。
    """
    cfg = _config.load_config()
    ai = cfg.ai
    if ai.api_key and not force_prompt:
        return ai
    if not sys.stdin.isatty():
        return ai

    print("=" * 64)
    print("配置 AI 摘要服务商（用自己的 Key，不会上传给任何人）")
    print("=" * 64)
    print("可选服务商：")
    for k, v in _AI_PROVIDERS.items():
        print(f"  {k}) {v['name']}")
    print("-" * 64)

    choice = _interactive_input("请选择服务商编号（直接回车取消）：").strip()
    if not choice or choice not in _AI_PROVIDERS:
        return ai
    prov = _AI_PROVIDERS[choice]

    key = _interactive_input(f"请粘贴你的 {prov['provider']} API Key：").strip()
    if not key:
        return ai

    model = _interactive_input(
        f"模型名（默认 {prov['model']}，直接回车用默认）："
    ).strip() or prov["model"]

    # 额度类型：免费档有 RPM 限频，工具会自动加调用间隔；付费档 RPM 高、不主动限速
    tier_input = _interactive_input(
        "你的额度类型（1=免费 2=付费，默认 1 免费；免费有 RPM 限频，工具会自动加调用间隔）："
    ).strip()
    tier = "paid" if tier_input == "2" else "free"

    _patch_config({
        "ai": {
            "provider": prov["provider"],
            "api_key": key,
            "model": model,
            "base_url": prov["base_url"],
            "tier": tier,
        }
    })
    print(f"✅ 已保存 {prov['provider']} 配置到 config/config.yaml，下次运行免填。")
    # 返回更新后的配置
    return _config.load_config().ai
