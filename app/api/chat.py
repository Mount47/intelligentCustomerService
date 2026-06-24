"""Chat 接入接口（§14）。POST 只落库+入队立即返回；GET 轮询结果（异步闭环 §10）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import allow_request
from app.db.session import get_db
from app.schemas.chat import ChatMessageIn, ChatSession, SendMessageResponse
from app.services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _dispatch(session_id: int) -> None:
    """投递异步处理。单独成函数，便于测试 monkeypatch。

    - celery（默认/生产）：入队，worker 消费
    - thread（本地无 broker）：后台线程直接跑 run_agent_session，轮询闭环照常
    """
    from app.core.config import get_settings

    if get_settings().agent_dispatch == "thread":
        import threading

        from app.db.session import SessionLocal
        from app.workers.runner import run_agent_session

        def _bg() -> None:
            db = SessionLocal()
            try:
                run_agent_session(db, session_id)
            finally:
                db.close()

        threading.Thread(target=_bg, daemon=True).start()
    else:
        from app.workers.agent_tasks import process_agent_message
        process_agent_message.delay(session_id)


@router.post("/message", response_model=SendMessageResponse)
def post_message(payload: ChatMessageIn, db: Session = Depends(get_db)) -> SendMessageResponse:
    """落用户消息 + 建 session + 入队，立即返回（不阻塞等待 Agent）。"""
    # 接入层前置限流：超额在入队前挡掉，保护队列与下游 LLM 成本（fail-open）
    limit = get_settings().chat_rate_limit_per_min
    allowed, _ = allow_request(f"chat:{payload.user_id}", limit, window_sec=60)
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    sess, dedup = chat_service.accept_message(db, payload)
    if not dedup:
        _dispatch(sess.id)
    return SendMessageResponse(
        session_id=sess.id, ticket_id=sess.ticket_id,
        task_status=sess.task_status, dedup=dedup)


@router.get("/session/{session_id}", response_model=ChatSession)
def get_session(session_id: int, db: Session = Depends(get_db)) -> ChatSession:
    """轮询会话：task_status + latest_reply + steps 时间线 + toolCalls + tokenUsage。"""
    view = chat_service.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="session not found")
    return view
