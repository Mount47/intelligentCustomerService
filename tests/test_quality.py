from app.agent.agent_core import build_default_agent
from app.db.models import QualityReview
from app.schemas.chat import ChatMessageIn
from app.services import chat_service, quality_service
from app.workers.runner import run_agent_session


def test_completed_session_produces_idempotent_quality_review(db, user_order):
    user, order = user_order
    session, _ = chat_service.accept_message(
        db,
        ChatMessageIn(
            user_id=user.id,
            order_id=order.id,
            content="查一下订单状态",
            client_message_id="quality-1",
        ),
    )
    run_agent_session(db, session.id, agent=build_default_agent())

    first = quality_service.review_session(db, session.id)
    second = quality_service.review_session(db, session.id)

    assert first is not None and second.id == first.id
    assert db.query(QualityReview).filter_by(session_id=session.id).count() == 1
    assert first.resolution_score == 5
    assert first.tool_call_correctness == 5
    assert first.policy_compliance == 5
    stats = quality_service.quality_stats(db)
    assert stats == {
        "count": 1,
        "resolution": 5.0,
        "tool": 5.0,
        "compliance": 5.0,
    }


def test_quality_review_missing_session_is_noop(db):
    assert quality_service.review_session(db, 999999) is None
