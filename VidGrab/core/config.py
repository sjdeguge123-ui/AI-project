# 配置加载模块
# Phase 0 — 读取 config.yaml，未配置时给出引导提示
"""加载 VidGrab 配置。

优先读取项目根下 config/config.yaml；不存在则抛出带引导的异常，
提示用户复制 config.example.yaml 并填入 Key。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

# notify 是可选的「个人微信推送」便利模块（不纳入 git，可一键删除）。
# 删除 core/notify.py 后，主程序仍能正常运行，只是不再有微信推送。
try:  # noqa: PLC0415
    from .notify import NotifyConfig, load_notify_config

    _HAVE_NOTIFY = True
except Exception:  # noqa: BLE001
    _HAVE_NOTIFY = False

    class NotifyConfig:  # 退化桩：删除 notify 模块时的兼容占位
        wecom_webhook: str = ""
        serverchan_key: str = ""

    def load_notify_config(raw=None):  # noqa: ARG001
        return NotifyConfig()


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


@dataclass
class AIConfig:
    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = ""      # 留空则按 provider 用默认地址；填了则用于任意 OpenAI 兼容服务商
                          # （如硅基流动 https://api.siliconflow.cn/v1、本地 Ollama http://localhost:11434/v1）
    tier: str = "free"     # free（免费额度，有 RPM 限频，工具会自动加调用间隔）| paid（付费额度，RPM 高，不主动限）
                          # 注意：Key 本身看不出免费/付费，这是用户首次配置时自报的；默认 free=保守安全


@dataclass
class WhisperConfig:
    mode: str = "api"          # api（云端）| local（本地）
    api_key: str = ""          # api 模式填 OpenAI Key
    local_model: str = "base"  # local 模式模型大小
    device: str = "cpu"        # cpu（默认，规避 Windows GPU 页面文件不稳定）| auto（自动检测）| cuda
    compute_type: str = "auto" # auto（GPU默认int8_float16/CPU默认int8）| float16 | int8 | int8_float16
    language: str = "auto"     # auto（跟随视频/自动检测）| zh | en；透传给 ASR 以锁定语种与标点


@dataclass
class OutputConfig:
    language: str = "auto"     # auto（跟随视频）| zh | en
    default_format: str = "markdown"
    save_path: str = ""        # 留空则默认桌面


@dataclass
class BilibiliConfig:
    sessdata: str = ""         # B站登录 cookie 的 SESSDATA；留空则用 yt-dlp 回退（仅弹幕，无真字幕）


@dataclass
class YoutubeConfig:
    # 方式一：让 yt-dlp 直接从本机浏览器读取 Cookie（浏览器名：chrome/edge/firefox/brave）。
    # 前提：你在该浏览器里已登录 YouTube。
    # 注意：本工具若在「非交互式」环境运行（如某些沙箱），浏览器 Cookie 经 DPAPI 加密
    #       可能无法解密；且浏览器开着时会锁住 Cookie 数据库。此时请用方式二。
    cookies_from_browser: str = ""
    # 方式二（最稳）：Netscape 格式 cookies.txt 文件路径。用浏览器扩展（如 Get cookies.txt）
    # 在已登录 YouTube 的页面导出该文件，把路径填这里。明文文件，不受 DPAPI / 锁库影响。
    cookies_file: str = ""


@dataclass
class ProxyConfig:
    http: str = ""             # HTTP 代理地址，如 http://127.0.0.1:7890
    https: str = ""            # HTTPS 代理地址；访问 YouTube 等被墙站点时需要


@dataclass
class Config:
    ai: AIConfig
    whisper: WhisperConfig
    output: OutputConfig
    notify: Optional[NotifyConfig] = None
    bilibili: BilibiliConfig = None  # type: ignore
    youtube: YoutubeConfig = None  # type: ignore
    proxy: ProxyConfig = None  # type: ignore


class ConfigError(Exception):
    """配置缺失或无效。"""


def load_config(path=None) -> Config:
    """加载配置；config.yaml 不存在时抛出带引导文案的 ConfigError。"""

    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(
            f"未找到配置文件：{cfg_path}\n"
            "请先复制模板并填入你的 Key：\n"
            "  cp config/config.example.yaml config/config.yaml\n"
            "然后编辑 config/config.yaml，填入 ai.api_key 等配置。"
        )
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件解析失败（{cfg_path}）：{exc}") from exc

    ai_raw = raw.get("ai") or {}
    whisper_raw = raw.get("whisper") or {}
    output_raw = raw.get("output") or {}
    bili_raw = raw.get("bilibili") or {}
    youtube_raw = raw.get("youtube") or {}
    proxy_raw = raw.get("proxy") or {}

    ai = AIConfig(
        provider=ai_raw.get("provider", "deepseek"),
        api_key=ai_raw.get("api_key", ""),
        model=ai_raw.get("model", "deepseek-chat"),
        base_url=ai_raw.get("base_url", ""),
        tier=ai_raw.get("tier", "free"),
    )
    whisper = WhisperConfig(
        mode=whisper_raw.get("mode", "api"),
        api_key=whisper_raw.get("api_key", ""),
        local_model=whisper_raw.get("local_model", "base"),
        device=whisper_raw.get("device", "auto"),
        compute_type=whisper_raw.get("compute_type", "auto"),
    )
    output = OutputConfig(
        language=output_raw.get("language", "auto"),
        default_format=output_raw.get("default_format", "markdown"),
        save_path=output_raw.get("save_path", ""),
    )
    bilibili = BilibiliConfig(
        sessdata=bili_raw.get("sessdata", ""),
    )
    proxy = ProxyConfig(
        http=proxy_raw.get("http", "") if isinstance(proxy_raw, dict) else str(proxy_raw or ""),
        https=proxy_raw.get("https", "") if isinstance(proxy_raw, dict) else "",
    )
    youtube = YoutubeConfig(
        cookies_from_browser=youtube_raw.get("cookies_from_browser", ""),
        cookies_file=youtube_raw.get("cookies_file", ""),
    )
    # 零配置便利：若未显式配置 cookies_file，但项目里存在 config/youtube_cookies.txt，
    # 自动采用它（用户把导出的 cookies 放到这个默认路径即可，无需改 config.yaml）。
    if not youtube.cookies_file:
        default_cookies = DEFAULT_CONFIG_PATH.parent / "youtube_cookies.txt"
        if default_cookies.exists():
            youtube.cookies_file = str(default_cookies)
    notify = load_notify_config(raw)
    return Config(ai=ai, whisper=whisper, output=output, bilibili=bilibili, youtube=youtube, proxy=proxy, notify=notify)
