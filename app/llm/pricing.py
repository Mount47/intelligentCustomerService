"""LLM 价目与成本折算（USD / 1M tokens，近似值，可按需调整）。

按模型名子串匹配；未知模型（含 stub）→ 0。缓存读 token 按输入价打折。
真实成本进 agent_sessions.estimated_cost，供 metrics/eval 量化。
"""
from __future__ import annotations

# (输入价, 输出价) USD / 1M tokens —— 近似，按厂商实际计费调整
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "qwen-max": (2.4, 9.6),
    "qwen-plus": (0.4, 1.2),
    "qwen-turbo": (0.05, 0.2),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
}

CACHE_READ_DISCOUNT = 0.1   # 缓存读约 0.1x 输入价


def _lookup(model_name: str) -> tuple[float, float]:
    m = (model_name or "").lower()
    for key, price in PRICES.items():
        if key in m:
            return price
    return (0.0, 0.0)        # 未知 / stub → 不计费


def estimate_cost(
    model_name: str, prompt_tokens: int, completion_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """估算累计成本（USD）。缓存读从输入里折价计算。"""
    in_price, out_price = _lookup(model_name)
    billable_in = max(prompt_tokens - cache_read_tokens, 0)
    cost = (
        billable_in / 1_000_000 * in_price
        + cache_read_tokens / 1_000_000 * in_price * CACHE_READ_DISCOUNT
        + completion_tokens / 1_000_000 * out_price
    )
    return round(cost, 6)
