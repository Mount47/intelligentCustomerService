from datetime import datetime, timedelta

from app.core.redaction import redact_value
from app.db.models import AgentSession, AgentToolCall
from app.observability.retention import purge_expired_audit
from app.tools.base import ToolCallRecord, ToolContext, ToolRegistry


def test_recursive_audit_redaction_masks_credentials_and_pii():
    value = redact_value({
        "password": "plain-text",
        "nested": {
            "authorization": "Bearer abc.def",
            "message": "联系 13800138000 或 me@example.com，key=sk-supersecret123",
        },
        "safe": {"order_id": 42},
    })
    assert value["password"] == "<redacted>"
    assert value["nested"]["authorization"] == "<redacted>"
    assert "13800138000" not in value["nested"]["message"]
    assert "me@example.com" not in value["nested"]["message"]
    assert "sk-supersecret123" not in value["nested"]["message"]
    assert value["safe"]["order_id"] == 42


def test_tool_registry_persists_only_redacted_audit(db):
    session = AgentSession(user_id=1)
    db.add(session)
    db.flush()
    record = ToolCallRecord(
        "demo", {"phone": "13800138000", "order_id": 9},
        {"ok": True, "data": {"email": "me@example.com"}, "error": None},
        True, 1, None,
    )
    ToolRegistry.audit(ToolContext(db=db, session_id=session.id), record)
    audit = db.query(AgentToolCall).filter_by(session_id=session.id).one()
    assert audit.input_json["phone"] == "<redacted>"
    assert audit.input_json["order_id"] == 9
    assert audit.output_json["email"] == "<redacted>"


def test_audit_retention_deletes_only_expired_rows(db):
    session = AgentSession(user_id=1)
    db.add(session)
    db.flush()
    old = AgentToolCall(
        session_id=session.id, tool_name="old", success=True,
        created_at=datetime.utcnow() - timedelta(days=100),
    )
    recent = AgentToolCall(
        session_id=session.id, tool_name="recent", success=True,
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    db.add_all([old, recent])
    db.commit()

    assert purge_expired_audit(db, days=30) == 1
    assert [row.tool_name for row in db.query(AgentToolCall).all()] == ["recent"]
