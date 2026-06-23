"""转人工会话摘要（TODO-1）——把对话上下文打包交接给人工客服。

设计（同读写分离哲学）：**事实字段从 DB 确定性取**（订单/退款/原因，绝不让 LLM 编造数字），
**叙述由 LLM 生成**（用户诉求/经过）。分档：对话太短不总结、直接给原文；LLM 失败回退模板。
默认模板模式（不调 API、可复现）；传入 llm 则启用真实叙述。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.models import Ticket, TicketMessage
from app.llm.base import Msg
from app.services import order_service, refund_service
from sqlalchemy import select

logger = get_logger(__name__)

_STATUS_CN = {"pending_payment": "待付款", "paid": "已付款", "shipped": "已发货",
              "delivered": "已签收", "cancelled": "已取消"}
_SHORT_MSGS = 3          # ≤ 此条数视为简短，不生成摘要、直接给原文
_HISTORY_CAP = 20        # 喂给 LLM 的最近消息上限（控 token）

_SUM_SYS = ("你是客服质检助手。用 2-3 句话向【接手的人工客服】概括这段对话的经过和用户诉求，"
            "只依据对话内容，不要编造订单号/金额/时间。")


def _derive_reason(ticket: Ticket, refund) -> str:
    """转人工原因（handoff_reason 未持久化，据 DB 可得信息粗略推断）。"""
    if refund is not None and refund.status == "pending_human":
        return "高风险退款，需人工审核"
    if ticket.category == "logistics":
        return "物流异常，需人工核实"
    if ticket.category in ("complaint", "投诉"):
        return "投诉升级"
    return "需人工跟进"


def _narrative(msgs: list[TicketMessage], user_msgs: list[TicketMessage], llm) -> tuple[str, str]:
    """返回 (叙述, 来源)。分档 + LLM/模板 + 失败降级。"""
    if len(msgs) <= _SHORT_MSGS:
        return "对话简短，详见下方完整记录。", "template"
    latest = user_msgs[-1].content[:60] if user_msgs else ""
    template = f"共 {len(msgs)} 条往来；用户最近诉求：{latest}"
    if llm is None:
        return template, "template"
    try:
        convo = "\n".join(f"{m.sender_type}: {m.content}" for m in msgs[-_HISTORY_CAP:])
        resp = llm.chat(system=_SUM_SYS, messages=[Msg("user", f"对话记录：\n{convo}")])
        text = (resp.text or "").strip()[:300]
        return (text or template), (f"llm:{getattr(llm, 'model_name', '?')}" if text else "template")
    except Exception:  # noqa: BLE001 — 摘要失败不阻断，回退模板
        logger.warning("handoff summary LLM failed, fallback to template")
        return template, "template"


def summarize_for_human(db, ticket_id: int, llm=None) -> dict | None:
    """生成转人工交接摘要。facts 来自 DB（准），narrative 来自 LLM/模板。"""
    t = db.get(Ticket, ticket_id)
    if t is None:
        return None
    msgs = list(db.scalars(select(TicketMessage).where(
        TicketMessage.ticket_id == t.id).order_by(TicketMessage.id)).all())
    user_msgs = [m for m in msgs if m.sender_type == "user"]

    # ---- 事实字段：确定性从 DB 取 ----
    order_info = refund_info = None
    refund = None
    if t.order_id is not None:
        o = order_service.get_order(db, t.order_id)
        if o:
            order_info = f"{o.order_no}（{_STATUS_CN.get(o.status, o.status)}，{float(o.total_amount)} 元）"
        refund = refund_service.get_active_refund(db, t.user_id, t.order_id)
        if refund:
            refund_info = f"退款单 #{refund.id}（{refund.status}，{float(refund.amount)} 元）"
    agent_actions = []
    if refund:
        agent_actions.append(f"已创建退款申请 #{refund.id}（{refund.status}）")

    # ---- 叙述字段：LLM/模板 ----
    brief, source = _narrative(msgs, user_msgs, llm)

    return {
        "user_need": user_msgs[0].content if user_msgs else "(无用户消息)",
        "handoff_reason": _derive_reason(t, refund),
        "order_info": order_info,
        "refund_info": refund_info,
        "agent_actions": agent_actions,
        "conversation_brief": brief,
        "message_count": len(msgs),
        "generated_by": source,
    }
