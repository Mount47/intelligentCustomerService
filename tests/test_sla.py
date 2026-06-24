"""SLA 监控闭环：解决回填 resolved_at/is_timeout + 达成统计 + 进 metrics + runner 端到端。"""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.agent.agent_core import build_default_agent
from app.db.models import AgentSession, SlaRecord, Ticket, User
from app.observability.metrics import compute_metrics
from app.services import sla_service, ticket_service
from app.services.admin_service import build_admin_metrics
from app.workers.runner import run_agent_session


def _ticket(db):
    u = User(username="sla")
    db.add(u)
    db.flush()
    t = Ticket(user_id=u.id, category="chat")
    db.add(t)
    db.flush()
    return t


def test_mark_resolved_within_deadline(db):
    t = _ticket(db)
    now = datetime.utcnow()
    rec = sla_service.create_sla_record(db, t.id, "resolution", now + timedelta(hours=1))
    sla_service.mark_resolved(db, t.id, now=now)
    assert rec.resolved_at == now and rec.is_timeout is False


def test_mark_resolved_after_deadline_flags_timeout(db):
    t = _ticket(db)
    now = datetime.utcnow()
    rec = sla_service.create_sla_record(db, t.id, "resolution", now - timedelta(hours=1))  # 已过期
    sla_service.mark_resolved(db, t.id, now=now)
    assert rec.resolved_at == now and rec.is_timeout is True


def test_sla_stats_counts(db):
    t = _ticket(db)
    now = datetime.utcnow()
    r1 = sla_service.create_sla_record(db, t.id, "a", now + timedelta(hours=1))  # 将 met
    r1.resolved_at = now
    r1.is_timeout = False
    sla_service.create_sla_record(db, t.id, "b", now - timedelta(hours=1))       # breached(未解决已过期)
    sla_service.create_sla_record(db, t.id, "c", now + timedelta(hours=2))       # pending
    db.flush()
    s = sla_service.sla_stats(db, now=now)
    assert s == {"total": 3, "met": 1, "breached": 1, "pending": 1, "met_rate": round(1 / 3, 3)}


def test_metrics_and_admin_expose_sla(db):
    t = _ticket(db)
    sla_service.create_sla_record(db, t.id, "resolution", datetime.utcnow() + timedelta(hours=1))
    db.flush()
    m = compute_metrics(db)
    assert m["sla"]["total"] == 1 and "met_rate" in m["sla"]
    am = build_admin_metrics(db)
    assert am.sla["total"] == 1                       # 透到 /admin/metrics


def test_runner_closes_sla_on_resolve(db):
    """端到端闭环：会话被 Agent 解决后，该工单的 SLA 记录被回填（此前只写不查）。"""
    u = User(username="r")
    db.add(u)
    db.flush()
    t = ticket_service.create_ticket(db, u.id, category="chat")   # 自动建 SLA(未来截止)
    ticket_service.add_message(db, t.id, "user", "帮我查下我的订单", user_id=u.id)
    sess = AgentSession(user_id=u.id, ticket_id=t.id, task_status="queued")
    db.add(sess)
    db.commit()

    run_agent_session(db, sess.id, agent=build_default_agent())   # stub，无订单→列单→resolved

    rec = db.scalars(select(SlaRecord).where(SlaRecord.ticket_id == t.id)).first()
    assert rec.resolved_at is not None and rec.is_timeout is False
