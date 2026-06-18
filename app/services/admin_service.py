"""Admin / 运维监测端查询（ADR-12）。tickets / sessions 列表与详情 + AdminMetrics。"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import AgentSession, Order, Ticket, TicketMessage
from app.observability.metrics import compute_metrics
from app.schemas.admin import AdminMetrics, SessionSummary, TicketDetail, TicketSummary
from app.schemas.chat import AgentTimelineStep, ChatMessageView
from app.schemas.common import to_frontend_task_status

_KNOWN_CAT = {"refund", "logistics", "complaint"}


def _cat(c: str) -> str:
    return c if c in _KNOWN_CAT else "other"


def _order_no(db: Session, order_id: int | None) -> str | None:
    if not order_id:
        return None
    o = db.get(Order, order_id)
    return o.order_no if o else None


def _latency(sess: AgentSession) -> int | None:
    if sess.started_at and sess.finished_at:
        return int((sess.finished_at - sess.started_at).total_seconds() * 1000)
    return None


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    idx = min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))
    return round(xs[idx], 1)


def list_tickets(db: Session, limit: int = 100) -> list[TicketSummary]:
    rows = db.scalars(select(Ticket).order_by(desc(Ticket.id)).limit(limit)).all()
    return [
        TicketSummary(
            id=t.id, user_id=t.user_id, order_no=_order_no(db, t.order_id),
            category=_cat(t.category), priority=t.priority, status=t.status,
            current_state=t.status, updated_at=t.updated_at, sla_deadline=t.sla_deadline)
        for t in rows
    ]


def get_ticket_detail(db: Session, ticket_id: int) -> TicketDetail | None:
    t = db.get(Ticket, ticket_id)
    if t is None:
        return None
    msgs = list(db.scalars(select(TicketMessage).where(
        TicketMessage.ticket_id == t.id).order_by(TicketMessage.id)).all())
    timeline = [
        AgentTimelineStep(id="created", kind="state", title="创建工单",
                          detail="created", created_at=t.created_at),
        AgentTimelineStep(id="current", kind="state", title="当前状态",
                          detail=t.status, created_at=t.updated_at),
    ]
    return TicketDetail(
        id=t.id, user_id=t.user_id, order_no=_order_no(db, t.order_id),
        category=_cat(t.category), priority=t.priority, status=t.status,
        current_state=t.status, updated_at=t.updated_at, sla_deadline=t.sla_deadline,
        state_timeline=timeline,
        messages=[ChatMessageView(id=m.id, sender=m.sender_type, content=m.content,
                                  created_at=m.created_at) for m in msgs],
    )


def list_sessions(db: Session, limit: int = 100) -> list[SessionSummary]:
    rows = db.scalars(select(AgentSession).order_by(desc(AgentSession.id)).limit(limit)).all()
    return [
        SessionSummary(
            id=s.id, ticket_id=s.ticket_id, current_intent=s.current_intent,
            current_skill=s.current_skill, task_status=to_frontend_task_status(s.task_status),
            total_tokens=s.total_tokens, estimated_cost=float(s.estimated_cost or 0),
            total_latency_ms=_latency(s), updated_at=s.updated_at)
        for s in rows
    ]


def build_admin_metrics(db: Session) -> AdminMetrics:
    m = compute_metrics(db)
    sessions = list(db.scalars(select(AgentSession)).all())
    durations = [(s.finished_at - s.started_at).total_seconds() * 1000
                 for s in sessions if s.started_at and s.finished_at]
    by = m["sessions_by_task_status"]
    n = m["totals"]["sessions"]
    return AdminMetrics(
        ticket_count=m["totals"]["tickets"],
        resolved_rate=m["agent"]["resolution_rate"],
        handoff_rate=m["agent"]["handoff_rate"],
        avg_token_cost=round(m["cost"]["total_cost"] / n, 6) if n else 0.0,
        p95_latency_ms=_p95(durations),
        active_sessions=by.get("queued", 0) + by.get("processing", 0),
        queue_depth=m["queue_depth"],
        sessions_by_task_status=by,
        total_tokens=m["cost"]["total_tokens"],
        avg_tokens_per_session=m["cost"]["avg_tokens_per_session"],
    )
