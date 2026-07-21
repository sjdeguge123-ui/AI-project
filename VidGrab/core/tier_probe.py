# 付费额度自动探测（core/tier_probe.py）
"""根据 AI provider 与 Key 自动判定额度类型（free / paid），免去用户手改 config.yaml。

设计目标（用户诉求）：
  · 付费 key 不应要求用户手动改 ai.tier；工具应能检测到。
  · 用户从免费充值为付费后，重跑即自动解除限速（每次运行都重新探测）。

各 provider 探测能力
────────────────────
  - openai     : GET {base_url}/v1/subscription → has_paid_subscription (bool)，最干净直接。
  - deepseek   : GET https://api.deepseek.com/user/balance → balance_infos[] 中
                 topped_up_balance（充值余额）> 0 ⇒ 付费；仅 granted_balance（赠送）⇒ 免费。
  - siliconflow: 无标准「是否付费」接口；用免费模型清单启发式（命中即 free，否则保守 free）。
  - ollama     : 本地部署，无配额限制，恒为 paid。

任何异常 / 超时 / 探测失败都**回退到用户在 setup 里自报的 ai.tier**（不降级、不阻塞流程）。
"""

from __future__ import annotations

from .config import AIConfig


def _http_get_json(url: str, api_key: str, proxy: str = "", timeout: float = 8.0):
    """极简 GET + Bearer 鉴权，返回解析后的 JSON；失败抛异常（交给上层回退）。"""

    import requests

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def detect_tier(ai: AIConfig, proxy: str = "") -> str:
    """返回 "free" 或 "paid"。探测失败一律回退 ai.tier（用户自报，绝不阻塞）。"""

    provider = (ai.provider or "").lower()

    # 本地部署：无配额限制，恒为付费
    if provider == "ollama":
        return "paid"

    # 没有 key 也无法探测，保持自报
    if not ai.api_key:
        return (ai.tier or "free").lower()

    try:
        if provider == "openai":
            base = (ai.base_url or "https://api.openai.com").rstrip("/")
            url = f"{base}/v1/subscription"
            data = _http_get_json(url, ai.api_key, proxy)
            paid = bool(data.get("has_paid_subscription"))
            return "paid" if paid else "free"

        if provider == "deepseek":
            url = "https://api.deepseek.com/user/balance"
            data = _http_get_json(url, ai.api_key, proxy)
            infos = data.get("balance_infos") or []
            topped = 0.0
            for info in infos:
                try:
                    topped += float(info.get("topped_up_balance") or 0)
                except (TypeError, ValueError):
                    topped += 0.0
            return "paid" if topped > 0 else "free"

        if provider == "siliconflow":
            # 硅基流动无标准「是否付费」接口；用免费模型清单启发式：
            # 命中已知免费模型（如 deepseek-ai/DeepSeek-V3 免费档）→ 保守 free（主动限速）；
            # 其余（如 DeepSeek-V3.1、Qwen3 等非免费模型）视为付费，自动解除限速，
            # 满足用户「充值为付费后无需手改 config 即自动升级」的诉求。
            # 误判风险由 ratelimit.downgrade_if_limited() 在真触发 429 时兜底降级。
            from .ratelimit import _is_known_free_model
            return "free" if _is_known_free_model(provider, ai.model) else "paid"

    except Exception:  # noqa: BLE001  探测失败不阻塞，回退自报
        pass

    return (ai.tier or "free").lower()
