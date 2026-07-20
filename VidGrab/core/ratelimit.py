# 调用频率限制管理（core/ratelimit.py）
"""管理 AI 调用的频率，区分「免费额度」与「付费额度」。

为什么需要它（用户诉求）：
  · 免费 API 额度有每分钟请求数（RPM）限制，短时间内连发会触发 429 限流。
  · 付费额度 RPM 高得多，单次任务（一个视频）基本不会触顶，可放心连发。
  · 所有意外（限流 / 服务器繁忙）都要把「发生了什么 + 怎么解决」告诉用户。

设计：
  tier 来源：用户在 setup_ai 里自报 free / paid（默认 free=保守安全）。
            Key 本身看不出免费/付费，所以必须让用户自己确认。
  + 已知免费模型库（如硅基流动 deepseek-ai/DeepSeek-V3 免费档）做提示与默认间隔。
  + 每次调用后读取响应头 x-ratelimit-*，动态细化真实 RPM 间隔。

关于「付费可随意调用直到达上限」的论证（写进代码注释，便于团队理解）：
  付费档 RPM 通常数十~数百次/分钟（DeepSeek 付费约 60 RPM 起、可提额；
  硅基流动付费随充值提升；OpenAI 付费 gpt-4o-mini 500 RPM、gpt-4o 500 RPM）。
  一个视频产生的 LLM 调用数 = ceil(字数 / 40000) + 1（合并那次），
  即便 2 小时长视频也就 5~10 次调用，远小于付费 RPM 在「真实生成耗时（每次数秒）」
  内重置的额度。因此单次任务不主动限速是安全的。
  唯一安全网：若服务端仍返回 429，按响应头 Retry-After 退避重试（不假设无限额）。
"""

from __future__ import annotations

import time
from typing import Optional


# 已知「免费档」模型：provider 或 model 命中其一即视为免费档（间隔更保守）
_FREE_MODELS: dict = {
    "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-V2.5", "Qwen/Qwen2.5-7B-Instruct"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],  # DeepSeek 免费档
}

# 免费档默认 RPM（次/分钟）——保守估计，仅用于决定默认间隔
_DEFAULT_FREE_RPM = 5


def _is_known_free_model(provider: str, model: str) -> bool:
    if not provider or not model:
        return False
    known = _FREE_MODELS.get(provider, [])
    if model in known:
        return True
    # 部分字符串匹配，避免拼写/前缀差异
    short = model.split("/")[-1]
    for m in known:
        if short in m or model in m:
            return True
    return False


class RateLimiter:
    """按 tier 管理 AI 调用间隔，免费档主动限速、付费档仅退避。"""

    def __init__(self, tier: str = "free", provider: str = "", model: str = ""):
        self.tier = (tier or "free").lower()
        self.provider = provider or ""
        self.model = model or ""
        self.limit: Optional[int] = None        # 真实 RPM 上限（来自响应头）
        self.remaining: Optional[int] = None     # 剩余可用次数
        self.reset: Optional[float] = None       # 重置窗口（秒，来自响应头）
        self._last_call_ts: float = 0.0
        self.min_interval: float = self._compute_interval()
        self._notified: bool = False

    # ---- 间隔计算 ----------------------------------------------------------
    def _compute_interval(self) -> float:
        if self.tier == "paid":
            return 0.0
        rpm = _DEFAULT_FREE_RPM
        if _is_known_free_model(self.provider, self.model):
            rpm = _DEFAULT_FREE_RPM
        # 间隔 = 60 / rpm，加 1.2 安全系数；最低 8 秒（避免抖动误触）
        return max(8.0, (60.0 / rpm) * 1.2)

    # ---- 用户提示 ----------------------------------------------------------
    def notify_if_free(self) -> None:
        """在首次调用前，若是免费档，给用户一句清晰提示。"""
        if self.tier != "free" or self._notified:
            return
        self._notified = True
        print("⏳ 检测到【免费 API 额度】，有每分钟请求数（RPM）限制。")
        print(f"   为保证流程跑完不被限流，每次 AI 调用之间会间隔约 {int(self.min_interval)} 秒。")
        if _is_known_free_model(self.provider, self.model):
            print(f"   （{self.provider}/{self.model} 属于已知免费模型，限频更明显）")
        print("   💡 若你其实是【付费】额度：把 config.yaml 的 ai.tier 改成 paid 即可提速；")
        print("      或把硅基流动 / DeepSeek 账户升级为付费，RPM 会大幅提升。")

    # ---- 调用前等待 --------------------------------------------------------
    def wait_before_call(self) -> None:
        """每次 LLM 调用前调用：必要时睡眠以避免触发限流。"""
        if self.tier == "paid":
            # 付费：仅在响应头显示剩余额度极低时防一手
            if self.remaining is not None and self.remaining <= 1 and self.reset:
                self._sleep(max(1.0, float(self.reset)), "付费额度剩余不足，短暂停顿")
            self._last_call_ts = time.time()
            return
        # 免费：固定间隔 + 若已知真实 RPM 则按剩余额度自适应
        now = time.time()
        elapsed = now - self._last_call_ts
        need = self.min_interval
        if self.limit and self.reset:
            precise = (self.reset / self.limit) * 1.2  # 安全系数
            need = max(need, precise)
        if elapsed < need:
            self._sleep(need - elapsed, "免费额度限频，主动间隔")
        self._last_call_ts = time.time()

    # ---- 调用后更新（从响应头读真实 RPM）----------------------------------
    def update_from_response(self, resp) -> None:
        """从一次成功调用的响应头里读取限流信息，动态细化间隔。"""
        h = _extract_headers(resp)
        if not h:
            return
        limit = _to_int(h.get("x-ratelimit-limit-requests"))
        remaining = _to_int(h.get("x-ratelimit-remaining-requests"))
        reset = _to_float(
            h.get("x-ratelimit-reset-requests") or h.get("x-ratelimit-reset-requests-reset")
        )
        if limit:
            self.limit = limit
        if remaining is not None:
            self.remaining = remaining
        if reset:
            self.reset = reset
        # 用真实 RPM 重新细化免费档间隔（不会比默认更激进）
        if self.tier == "free" and self.limit and self.reset:
            self.min_interval = max(8.0, (self.reset / self.limit) * 1.2)

    # ---- 限流时读取 Retry-After -------------------------------------------
    def extract_retry_after(self, err) -> Optional[float]:
        """从 openai.RateLimitError 里读 Retry-After（秒）。读不到返回 None。"""
        resp = getattr(err, "response", None)
        if resp is None:
            return None
        hdrs = getattr(resp, "headers", None)
        if hdrs is None:
            return None
        try:
            ra = hdrs.get("retry-after") if hasattr(hdrs, "get") else None
        except Exception:
            ra = None
        if ra is None:
            return None
        try:
            return float(ra)
        except (TypeError, ValueError):
            return None

    # ---- 内部工具 ----------------------------------------------------------
    def _sleep(self, secs: float, reason: str = "") -> None:
        secs = max(0.5, float(secs))
        msg = f"   ⏱️ {reason}：等待 {secs:.0f} 秒..." if reason else f"   ⏱️ 等待 {secs:.0f} 秒..."
        print(msg)
        time.sleep(secs)


def _extract_headers(resp) -> dict:
    h = getattr(resp, "headers", None)
    if h is None:
        return {}
    if isinstance(h, dict):
        return h
    try:
        return dict(h)
    except Exception:
        return {}


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
