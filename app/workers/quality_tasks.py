"""异步会话质检任务；与 Agent 主任务解耦，质检失败不回滚业务结果。"""
from __future__ import annotations

from app.observability.tracing import use_or_create_trace_id
from app.workers.celery_app import celery_app


@celery_app.task(name="quality.review_session", autoretry_for=(Exception,),
                 retry_backoff=True, max_retries=2)
def review_agent_session(session_id: int, trace_id: str | None = None) -> None:
    from app.db.session import SessionLocal
    from app.services.quality_service import review_session

    # Celery signal会先绑定；直接调用任务函数时也保持可诊断性。
    use_or_create_trace_id(trace_id)
    with SessionLocal() as db:
        review_session(db, session_id)
