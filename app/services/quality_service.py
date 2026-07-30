"""会话质检：基于真实落库事实生成可复现的第一层质量记录。

这不是 LLM-as-Judge。它衡量流程结果、工具成功率、禁语合规和人工升级事实；
语义准确性与帮助度由显式付费的 real eval 独立评估。
"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.guardrails import Guardrails
from app.db.models import (
    AgentSession, AgentToolCall, QualityReview, RefundRequest, Ticket, TicketMessage,
)


def _score_ratio(ok: int, total: int) -> int:
    return 5 if total == 0 else max(1, round(5 * ok / total))


def review_session(db: Session, session_id: int, *, commit: bool = True) -> QualityReview | None:
    session = db.get(AgentSession, session_id)
    if session is None or session.ticket_id is None:
        return None
    existing = db.scalar(select(QualityReview).where(
        QualityReview.session_id == session_id))
    if existing is not None:
        return existing

    calls = list(db.scalars(select(AgentToolCall).where(
        AgentToolCall.session_id == session_id)).all())
    reply = db.scalar(select(TicketMessage.content).where(
        TicketMessage.ticket_id == session.ticket_id,
        TicketMessage.sender_type == "agent",
    ).order_by(desc(TicketMessage.id))) or ""
    forbidden = Guardrails().scan_forbidden(reply)
    state = session.final_status or session.current_state or ""
    if session.task_status in ("failed", "timeout"):
        resolution_score = 1
    elif state in ("resolved_by_agent", "resolved_by_human"):
        resolution_score = 5
    elif state in ("need_human", "waiting_user_confirm", "info_required"):
        resolution_score = 4
    else:
        resolution_score = 3

    ticket = db.get(Ticket, session.ticket_id)
    refund = None
    if ticket is not None and ticket.order_id is not None:
        refund = db.scalar(select(RefundRequest).where(
            RefundRequest.order_id == ticket.order_id,
        ).order_by(desc(RefundRequest.id)))
    review = QualityReview(
        session_id=session.id,
        ticket_id=session.ticket_id,
        resolution_score=resolution_score,
        tool_call_correctness=_score_ratio(
            sum(1 for call in calls if call.success), len(calls)),
        policy_compliance=1 if forbidden else 5,
        handoff_decision=(
            "handoff_triggered" if state == "need_human" else "self_service_or_waiting"),
        risk_level=refund.risk_level if refund else None,
        suggestion=(
            f"命中禁语：{','.join(forbidden)}" if forbidden
            else "流程、工具与禁语检查通过"
        ),
    )
    db.add(review)
    db.flush()
    if commit:
        db.commit()
        db.refresh(review)
    return review


def quality_stats(db: Session) -> dict:
    rows = list(db.scalars(select(QualityReview)).all())
    if not rows:
        return {"count": 0, "resolution": 0.0, "tool": 0.0, "compliance": 0.0}
    count = len(rows)
    return {
        "count": count,
        "resolution": round(sum(r.resolution_score or 0 for r in rows) / count, 2),
        "tool": round(sum(r.tool_call_correctness or 0 for r in rows) / count, 2),
        "compliance": round(sum(r.policy_compliance or 0 for r in rows) / count, 2),
    }
