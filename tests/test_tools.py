"""M3 tools：统一 ToolResult + 自动写 agent_tool_calls + 幂等贯通到工具层。"""
from app.db.models import AgentSession, AgentToolCall
from app.tools.base import ToolContext
from app.tools.registry import build_tool_registry


def _ctx_with_session(db, user_id):
    sess = AgentSession(user_id=user_id, current_state="tool_executing")
    db.add(sess)
    db.flush()
    return ToolContext(db=db, session_id=sess.id), sess.id


def test_registry_loads_all_tools():
    reg = build_tool_registry()
    names = set(reg.names())
    assert {"get_order_detail", "check_refund_policy", "create_refund_draft",
            "get_logistics_status", "create_ticket", "search_policy_docs"} <= names
    assert len(names) == 15


def test_tool_returns_result_and_writes_audit(db, user_order):
    u, o = user_order
    reg = build_tool_registry()
    ctx, sid = _ctx_with_session(db, u.id)

    result, record = reg.execute(ctx, "get_order_detail", {"order_id": o.id, "user_id": u.id})
    assert result["ok"] is True
    assert result["data"]["order_no"] == o.order_no
    assert record.success is True and record.latency_ms >= 0
    # 审计落库
    rows = db.query(AgentToolCall).filter_by(session_id=sid).all()
    assert len(rows) == 1 and rows[0].tool_name == "get_order_detail"


def test_tool_unknown_returns_err(db, user_order):
    u, _ = user_order
    reg = build_tool_registry()
    ctx, _ = _ctx_with_session(db, u.id)
    result, record = reg.execute(ctx, "no_such_tool", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_tool"
    assert record.success is False


def test_create_refund_draft_idempotent_via_tool(db, user_order):
    u, o = user_order
    reg = build_tool_registry()
    ctx, _ = _ctx_with_session(db, u.id)
    args = {"order_id": o.id, "user_id": u.id, "refund_reason": "不想要了"}
    r1, _ = reg.execute(ctx, "create_refund_draft", args)
    r2, _ = reg.execute(ctx, "create_refund_draft", args)
    assert r1["ok"] and r2["ok"]
    assert r1["data"]["created"] is True
    assert r2["data"]["idempotent_hit"] is True
    assert r1["data"]["refund_request_id"] == r2["data"]["refund_request_id"]
