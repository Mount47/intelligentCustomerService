"""M9：tracing trace_id 基础行为。"""
from app.observability.tracing import get_trace_id, new_trace_id, set_trace_id


def test_new_and_get_trace_id():
    tid = new_trace_id()
    assert len(tid) == 12 and get_trace_id() == tid


def test_set_trace_id():
    set_trace_id("fixed-123")
    assert get_trace_id() == "fixed-123"
