"""Chat 接入接口（§14）。POST 只落库+入队立即返回；GET 轮询结果（异步闭环 §10）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.chat import ChatMessageAccepted, ChatMessageIn, SessionView
from app.services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _dispatch(session_id: int) -> None:
    """投递异步任务。单独成函数，便于测试 monkeypatch（避免连真实 broker）。"""
    from app.workers.agent_tasks import process_agent_message
    process_agent_message.delay(session_id)


@router.post("/message", response_model=ChatMessageAccepted)
def post_message(payload: ChatMessageIn, db: Session = Depends(get_db)) -> ChatMessageAccepted:
    """落用户消息 + 建 session + 入队，立即返回（不阻塞等待 Agent）。"""
    sess, dedup = chat_service.accept_message(db, payload)
    if not dedup:
        _dispatch(sess.id)
    return ChatMessageAccepted(
        session_id=sess.id, ticket_id=sess.ticket_id,
        task_status=sess.task_status, dedup=dedup)


@router.get("/session/{session_id}", response_model=SessionView)
def get_session(session_id: int, db: Session = Depends(get_db)) -> SessionView:
    """轮询会话：task_status + latest_reply + steps 时间线 + token。"""
    view = chat_service.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="session not found")
    return view
