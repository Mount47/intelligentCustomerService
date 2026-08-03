"""发件箱投递程序（outbox relay）：把 pending 事件补投到消息队列。

独立进程，只依赖 PostgreSQL。**刻意不做成 Celery beat 定时任务**——beat 自己也要
把调度消息投进 broker，用 broker 去救 broker，在 broker 宕机时同样失效。relay 直接
读库、直接投递，才能覆盖"提交成功但入队失败"这个窗口。

并发安全：取件用 `SELECT ... FOR UPDATE SKIP LOCKED`，多个 relay 实例互不重复取件
（PostgreSQL）。SQLite 无 SKIP LOCKED，退化为普通查询，仅用于单进程测试。

投递语义 at-least-once：失败按指数退避重试，重复投递由 `runner._claim_session`
的 CAS 兜成幂等 no-op。
"""
from __future__ import annotations

import signal
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import OutboxEvent
from app.services.outbox_service import STATUS_PENDING, STATUS_SENT

logger = get_logger(__name__)


def _publish(event: OutboxEvent) -> None:
    """把事件投进 Celery。topic 即任务名，payload 即关键字参数。"""
    from app.workers.agent_tasks import process_agent_message

    if event.topic != "agent.process_message":
        raise ValueError(f"unknown outbox topic: {event.topic!r}")
    payload = dict(event.payload or {})
    session_id = payload.get("session_id")
    if session_id is None:
        raise ValueError(f"outbox event {event.id} has no session_id")
    process_agent_message.delay(session_id, trace_id=payload.get("trace_id"))


def _claim_batch(db: Session, limit: int) -> list[OutboxEvent]:
    """取一批到期的 pending 事件并加行锁，避免多实例重复投递。"""
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == STATUS_PENDING,
               OutboxEvent.available_at <= datetime.utcnow())
        .order_by(OutboxEvent.id)
        .limit(limit)
    )
    # get_bind() 是有绑定与否都可用的公开接口；.bind 在部分 Session 构造下为 None，
    # 静默退化会让多实例失去 SKIP LOCKED 保护，变成重复取件。
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return list(db.scalars(stmt).all())


def _backoff_sec(attempts: int, settings: Settings) -> float:
    return min(settings.outbox_backoff_base_sec * (2 ** max(0, attempts - 1)),
               settings.outbox_backoff_max_sec)


def drain_once(db: Session, settings: Settings | None = None) -> dict:
    """补投一批。返回本轮统计，供测试与日志使用。"""
    settings = settings or get_settings()
    events = _claim_batch(db, settings.outbox_relay_batch)
    sent = failed = 0
    for event in events:
        event.attempts += 1
        try:
            _publish(event)
        except Exception as exc:  # noqa: BLE001 — 单条失败不该中断整批
            failed += 1
            event.last_error = str(exc)[:512]
            event.available_at = datetime.utcnow() + timedelta(
                seconds=_backoff_sec(event.attempts, settings))
            logger.warning("outbox redeliver failed (event=%s attempts=%s): %s",
                           event.id, event.attempts, exc)
            if event.attempts == settings.outbox_stuck_attempts:
                logger.error(
                    "outbox event %s stuck after %s attempts; needs attention",
                    event.id, event.attempts)
        else:
            sent += 1
            event.status = STATUS_SENT
            event.sent_at = datetime.utcnow()
            event.last_error = None
            logger.info("outbox redelivered event=%s topic=%s attempts=%s",
                        event.id, event.topic, event.attempts)
    db.commit()
    return {"claimed": len(events), "sent": sent, "failed": failed}


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    from app.db.session import SessionLocal

    stopping = False

    def _stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        logger.info("outbox relay received stop signal; finishing current batch")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("outbox relay started (interval=%ss batch=%s)",
                settings.outbox_relay_interval_sec, settings.outbox_relay_batch)
    while not stopping:
        db = SessionLocal()
        try:
            stats = drain_once(db, settings)
            if stats["claimed"]:
                logger.info("outbox relay batch: %s", stats)
        except Exception:  # noqa: BLE001 — relay 必须长驻，单轮异常不退出
            logger.exception("outbox relay batch failed")
            db.rollback()
        finally:
            db.close()
        time.sleep(settings.outbox_relay_interval_sec)
    logger.info("outbox relay stopped")


if __name__ == "__main__":
    run_forever()
