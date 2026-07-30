"""Admin / 运维监测端查询（ADR-12）。tickets / sessions 列表与详情 + AdminMetrics。"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.state_machine import States
from app.db.models import AgentSession, Order, Ticket, TicketMessage, TicketStateTransition
from app.observability.metrics import compute_metrics
from app.schemas.admin import (
    AdminMetrics, SessionSummary, TicketActionIn, TicketDetail, TicketSummary,
)
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


def list_tickets(
    db: Session, limit: int = 100, offset: int = 0,
    status: str | None = None, category: str | None = None,
) -> list[TicketSummary]:
    query = select(Ticket)
    if status:
        query = query.where(Ticket.status == status)
    if category:
        query = query.where(Ticket.category == category)
    rows = db.scalars(
        query.order_by(desc(Ticket.id)).offset(offset).limit(limit)
    ).all()
    return [
        TicketSummary(
            id=t.id, user_id=t.user_id, order_no=_order_no(db, t.order_id),
            category=_cat(t.category), priority=t.priority, status=t.status,
            current_state=t.status, updated_at=t.updated_at, sla_deadline=t.sla_deadline,
            assigned_to=t.assigned_to)
        for t in rows
    ]


def get_ticket_detail(db: Session, ticket_id: int) -> TicketDetail | None:
    t = db.get(Ticket, ticket_id)
    if t is None:
        return None
    msgs = list(db.scalars(select(TicketMessage).where(
        TicketMessage.ticket_id == t.id).order_by(TicketMessage.id)).all())
    transitions = list(db.scalars(select(TicketStateTransition).where(
        TicketStateTransition.ticket_id == t.id
    ).order_by(TicketStateTransition.id)).all())
    timeline = [
        AgentTimelineStep(
            id=f"transition-{tr.id}",
            kind="state",
            title="创建工单" if tr.from_state is None else f"{tr.from_state} → {tr.to_state}",
            detail=" · ".join(filter(None, [
                tr.to_state,
                f"actor={tr.actor_type}" if tr.actor_type else None,
                tr.reason,
            ])),
            status="success",
            created_at=tr.created_at,
        )
        for tr in transitions
    ]
    if not timeline:  # 迁移前历史数据兼容
        timeline = [
            AgentTimelineStep(id="created", kind="state", title="创建工单",
                              detail="created", status="success", created_at=t.created_at),
            AgentTimelineStep(id="current", kind="state", title="当前状态",
                              detail=t.status, status="success", created_at=t.updated_at),
        ]
    from app.schemas.admin import HandoffSummary
    from app.services import handoff_service
    # 交接摘要：模板模式(不调 API)；事实字段来自 DB。需真实叙述可传 llm。
    summ = handoff_service.summarize_for_human(db, t.id)
    return TicketDetail(
        id=t.id, user_id=t.user_id, order_no=_order_no(db, t.order_id),
        category=_cat(t.category), priority=t.priority, status=t.status,
        current_state=t.status, updated_at=t.updated_at, sla_deadline=t.sla_deadline,
        assigned_to=t.assigned_to,
        state_timeline=timeline,
        messages=[ChatMessageView(id=m.id, sender=m.sender_type, content=m.content,
                                  created_at=m.created_at) for m in msgs],
        handoff_summary=HandoffSummary(**summ) if summ else None,
    )


def handle_ticket(
    db: Session, ticket_id: int, action: TicketActionIn, *, admin_id: int
) -> TicketDetail | None:
    """管理员人工处理闭环：指派、解决、驳回或关闭，并追加人工处理记录。"""
    from app.services import sla_service, ticket_service

    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        return None
    actor_id = str(admin_id)
    if action.action == "assign":
        if not action.assigned_to:
            raise ValueError("assigned_to is required for assign")
        ticket.assigned_to = action.assigned_to
        if ticket.status == States.CREATED:
            ticket_service.update_status(
                db, ticket.id, States.NEED_HUMAN,
                actor_type="human", actor_id=actor_id, reason=action.note or "assigned",
            )
        else:
            db.add(TicketStateTransition(
                ticket_id=ticket.id, from_state=ticket.status, to_state=ticket.status,
                actor_type="human", actor_id=actor_id,
                reason=action.note or f"assigned_to:{action.assigned_to}",
            ))
    else:
        targets = {
            "resolve": States.RESOLVED_BY_HUMAN,
            "reject": States.REJECTED,
            "close": States.CLOSED,
        }
        target = targets[action.action]
        ticket_service.update_status(
            db, ticket.id, target,
            actor_type="human", actor_id=actor_id, reason=action.note or action.action,
        )
        if target == States.RESOLVED_BY_HUMAN:
            sla_service.mark_resolved(db, ticket.id)
    if action.note:
        ticket_service.add_message(
            db, ticket.id, "human", action.note,
        )
    db.commit()
    return get_ticket_detail(db, ticket.id)


def list_sessions(
    db: Session, limit: int = 100, offset: int = 0,
    task_status: str | None = None,
) -> list[SessionSummary]:
    query = select(AgentSession)
    if task_status:
        query = query.where(AgentSession.task_status == task_status)
    rows = db.scalars(
        query.order_by(desc(AgentSession.id)).offset(offset).limit(limit)
    ).all()
    return [
        SessionSummary(
            id=s.id, ticket_id=s.ticket_id, current_intent=s.current_intent,
            current_skill=s.current_skill, task_status=to_frontend_task_status(s.task_status),
            total_tokens=s.total_tokens, estimated_cost=float(s.estimated_cost or 0),
            total_latency_ms=_latency(s), updated_at=s.updated_at)
        for s in rows
    ]


def build_admin_metrics(db: Session) -> AdminMetrics:
    from app.services.quality_service import quality_stats

    m = compute_metrics(db)
    # 精确 percentile 在 PostgreSQL/SQLite 上写法不同；管理看板取最近 2000 条有界样本，
    # 避免每次 3 秒轮询把整张 agent_sessions 拉进 Python。
    samples = db.execute(select(
        AgentSession.started_at, AgentSession.finished_at,
    ).where(
        AgentSession.started_at.is_not(None),
        AgentSession.finished_at.is_not(None),
    ).order_by(desc(AgentSession.id)).limit(2000)).all()
    durations = [(finished - started).total_seconds() * 1000
                 for started, finished in samples]
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
        sla=m["sla"],
        quality=quality_stats(db),
    )
