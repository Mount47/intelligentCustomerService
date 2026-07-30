"""消息接入幂等真并发：唯一约束裁决，失败方回查赢家而不是返回 500。"""
import os
import threading
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import AgentSession, Base, SlaRecord, Ticket, TicketMessage, User
from app.schemas.chat import ChatMessageIn
from app.services import chat_service


def _engine(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return create_engine(url)
    return create_engine(
        f"sqlite:///{tmp_path / 'message-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def test_concurrent_same_client_message_returns_one_session(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    marker = uuid.uuid4().hex[:10]
    with Session() as db:
        user = User(username=f"msg-conc-{marker}")
        db.add(user)
        db.commit()
        user_id = user.id

    threads_count = int(os.getenv("MESSAGE_CONC_THREADS", "8"))
    client_message_id = f"client-{marker}"
    barrier = threading.Barrier(threads_count)
    results: list[tuple[int, bool]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker():
        with Session() as db:
            try:
                barrier.wait()
                sess, dedup = chat_service.accept_message(
                    db,
                    ChatMessageIn(
                        user_id=user_id,
                        content="同一条消息并发重放",
                        client_message_id=client_message_id,
                    ),
                )
                with lock:
                    results.append((sess.id, dedup))
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(results) == threads_count
    assert len({session_id for session_id, _ in results}) == 1
    assert sum(1 for _, dedup in results if not dedup) == 1

    with Session() as db:
        message_count = db.scalar(select(func.count()).select_from(TicketMessage).where(
            TicketMessage.user_id == user_id,
            TicketMessage.client_message_id == client_message_id,
        ))
        session_count = db.scalar(select(func.count()).select_from(AgentSession).where(
            AgentSession.user_id == user_id,
        ))
        ticket_ids = list(db.scalars(select(Ticket.id).where(Ticket.user_id == user_id)))
        sla_count = db.scalar(select(func.count()).select_from(SlaRecord).where(
            SlaRecord.ticket_id.in_(ticket_ids),
        ))
    assert message_count == session_count == len(ticket_ids) == sla_count == 1


def test_replaying_old_message_returns_its_original_session(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    marker = uuid.uuid4().hex[:10]
    with Session() as db:
        user = User(username=f"msg-replay-{marker}")
        db.add(user)
        db.commit()
        first, _ = chat_service.accept_message(
            db,
            ChatMessageIn(
                user_id=user.id,
                content="第一轮",
                client_message_id=f"first-{marker}",
            ),
        )
        second, _ = chat_service.accept_message(
            db,
            ChatMessageIn(
                user_id=user.id,
                ticket_id=first.ticket_id,
                content="第二轮",
                client_message_id=f"second-{marker}",
            ),
        )
        replay, dedup = chat_service.accept_message(
            db,
            ChatMessageIn(
                user_id=user.id,
                ticket_id=first.ticket_id,
                content="第一轮",
                client_message_id=f"first-{marker}",
            ),
        )

    assert first.id != second.id
    assert dedup is True
    assert replay.id == first.id
