"""P4 长对话记忆：token 预算 + 滚动摘要（ADR-21 P4；顺带治 TODO-2 上下文溢出）。

思路：历史超 token 预算时，**最近若干条原样保留**，**更早的一大段摘成一句**进 prompt。
- 既不超上下文（防溢出/防成本失控），又不丢"最初诉求"（防 Agent 忘了用户要啥）。
- **硬事实（order_id/金额/状态）不靠摘要**——它们在 ticket/DB，查库即可（见 ADR-21 读写分离）；
  摘要只扛"软上下文"（经过/诉求/语气），所以即便有损也丢不了关键键。
- 摘要：有 LLM 调 LLM，否则/失败回退模板（stub/CI 确定性可复现，同 handoff_service）。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.llm.base import Msg

logger = get_logger(__name__)

_SUM_SYS = ("把下面这段客服对话历史压成 2-3 句：保留用户核心诉求和关键经过，"
            "只依据内容、不要编造订单号/金额/时间。")


def estimate_tokens(text: str) -> int:
    """粗估 token：按字符数（中文≈1 token/字；英文偏高估）。

    刻意**保守高估**——预算判断宁可早一点摘要，也不冒超上下文的险。不追求精确。
    """
    return max(1, len(text or ""))


def summarize_history(msgs: list[Msg], llm=None, max_chars: int = 300) -> str:
    """把一段旧历史摘成一句。llm=None 或调用失败 → 模板兜底（确定性、可复现）。"""
    if not msgs:
        return ""
    convo = "\n".join(f"{m.role}: {m.content}" for m in msgs)
    if llm is None:
        return f"共 {len(msgs)} 条早期对话；摘录：{convo[:max_chars]}"
    try:
        resp = llm.chat(system=_SUM_SYS, messages=[Msg("user", convo)])
        text = (resp.text or "").strip()[:max_chars]
        return text or f"共 {len(msgs)} 条早期对话；摘录：{convo[:max_chars]}"
    except Exception:  # noqa: BLE001 — 摘要失败不阻断，回退模板
        logger.warning("history summarize failed, fallback to template")
        return f"共 {len(msgs)} 条早期对话；摘录：{convo[:max_chars]}"


def apply_token_budget(msgs: list[Msg], *, token_budget: int, llm=None) -> list[Msg]:
    """最近消息按 token 预算原样保留；超出的旧段摘成一条前置消息。

    至少保留最新 1 条（哪怕它本身超预算，也不丢当前上下文）。
    """
    kept: list[Msg] = []
    used = 0
    split = 0   # msgs[:split] = 需摘要的旧段
    for i in range(len(msgs) - 1, -1, -1):
        t = estimate_tokens(msgs[i].content)
        if kept and used + t > token_budget:   # 预算用尽（至少已留1条）→ 其余更旧的去摘要
            split = i + 1
            break
        used += t
        kept.insert(0, msgs[i])
    old = msgs[:split]
    if not old:
        return kept
    summary = summarize_history(old, llm=llm)
    # 摘要作为前置 user 消息（首条=user，适配各 provider；明确标注是摘要）
    return [Msg(role="user", content=f"[早期对话摘要] {summary}")] + kept
