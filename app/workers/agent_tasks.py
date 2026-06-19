"""Celery 任务包装（§15）。纯处理逻辑在 runner.run_agent_session。

任务：开自有 DB 会话 → 调 runner → 关闭。retry/超时由 celery 配置（celery_app）兜。
"""
from __future__ import annotations

from app.workers.celery_app import celery_app
from app.workers.runner import run_agent_session


@celery_app.task(name="agent.process_message", bind=True, max_retries=2, default_retry_delay=5)
def process_agent_message(self, session_id: int) -> None:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        # raise_on_error=True：失败/超时重抛 → celery 退避重试；耗尽则会话留在 failed/timeout
        run_agent_session(db, session_id, raise_on_error=True)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)
    finally:
        db.close()
