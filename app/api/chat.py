"""Chat 接入接口（§14）。POST 只落库+入队立即返回；GET 轮询结果（异步闭环 §10）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ResourceAccessDenied
from app.core.logging import get_logger
from app.core.ratelimit import allow_request
from app.core.security import Principal, get_current_principal
from app.db.models import AgentSession
from app.db.session import get_db
from app.schemas.chat import ChatActionIn, ChatMessageIn, ChatSession, SendMessageResponse
from app.services import chat_service, outbox_service
from app.observability.tracing import get_trace_id

router = APIRouter(prefix="/api/chat", tags=["chat"])

logger = get_logger(__name__)


def _dispatch(session_id: int, trace_id: str) -> None:
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
                run_agent_session(db, session_id, trace_id=trace_id)
                from app.services.quality_service import review_session
                review_session(db, session_id)
            finally:
                db.close()

        threading.Thread(target=_bg, daemon=True).start()
    else:
        from app.workers.agent_tasks import process_agent_message
        process_agent_message.delay(session_id, trace_id=trace_id)


def _dispatch_and_settle(db: Session, session_id: int, trace_id: str) -> None:
    """内联投递快路 + 发件箱销账。

    投递失败不再是丢消息：事件已与会话同事务落库，留在 pending 由 relay 补投，
    因此这里吞掉异常、照常给用户返回 200，而不是把 broker 故障放大成请求失败。
    """
    settings = get_settings()
    try:
        _dispatch(session_id, trace_id)
    except Exception as exc:  # noqa: BLE001 — broker 故障由发件箱兜底，不该连坐请求
        if not settings.outbox_enabled:
            raise
        logger.warning(
            "inline dispatch failed for session %s; left to outbox relay: %s",
            session_id, exc)
        return
    if settings.outbox_enabled:
        outbox_service.mark_sent(
            db, outbox_service.dedup_key_for(outbox_service.AGENT_TOPIC, session_id))


def _check_rate_limit(user_id: int) -> None:
    limit = get_settings().chat_rate_limit_per_min
    allowed, _ = allow_request(f"chat:{user_id}", limit, window_sec=60)
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


@router.post("/message", response_model=SendMessageResponse)
def post_message(
    payload: ChatMessageIn,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SendMessageResponse:
    """落用户消息 + 建 session + 入队，立即返回（不阻塞等待 Agent）。"""
    if payload.user_id is not None and payload.user_id != principal.user_id:
        raise ResourceAccessDenied("request user_id does not match authenticated identity")
    payload = payload.model_copy(update={"user_id": principal.user_id})
    # 接入层前置限流：超额在入队前挡掉，保护队列与下游 LLM 成本（fail-open）
    _check_rate_limit(principal.user_id)
    sess, dedup = chat_service.accept_message(db, payload)
    if not dedup:
        _dispatch_and_settle(db, sess.id, get_trace_id())
    return SendMessageResponse(
        session_id=sess.id, ticket_id=sess.ticket_id,
        task_status=sess.task_status, dedup=dedup)


@router.post("/action", response_model=SendMessageResponse)
def post_action(
    payload: ChatActionIn,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SendMessageResponse:
    """提交页面上的确认或取消按钮；业务含义由服务端待确认记录决定。"""
    _check_rate_limit(principal.user_id)
    sess, dedup = chat_service.accept_action(db, payload, principal.user_id)
    if not dedup:
        _dispatch_and_settle(db, sess.id, get_trace_id())
    return SendMessageResponse(
        session_id=sess.id,
        ticket_id=sess.ticket_id,
        task_status=sess.task_status,
        dedup=dedup,
    )


@router.get("/session/{session_id}", response_model=ChatSession)
def get_session(
    session_id: int,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatSession:
    """轮询会话：task_status + latest_reply + steps 时间线 + toolCalls + tokenUsage。"""
    sess = db.get(AgentSession, session_id)
    if sess is not None and sess.user_id != principal.user_id and principal.role != "admin":
        raise ResourceAccessDenied("session does not belong to authenticated user")
    view = chat_service.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="session not found")
    return view


@router.get("/session/{session_id}/stream")
def stream_session(
    session_id: int,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE 流式推送会话进度（替代轮询）：状态/思考步骤/回复有变化就推，终态收尾。"""
    settings = get_settings()
    sess = db.get(AgentSession, session_id)
    if sess is not None and sess.user_id != principal.user_id and principal.role != "admin":
        raise ResourceAccessDenied("session does not belong to authenticated user")

    def fetch():
        db.expire_all()   # 丢身份映射缓存，强制重读 → 看到 worker 进程的提交
        return chat_service.get_session_view(db, session_id)

    gen = chat_service.stream_session_events(
        fetch, interval=settings.sse_poll_interval_sec, max_iters=settings.sse_max_iters)
    # X-Accel-Buffering: no → 关掉 nginx 缓冲，事件即时下发
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
