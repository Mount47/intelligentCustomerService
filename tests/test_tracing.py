"""M9：tracing trace_id 基础行为。"""
from app.observability.tracing import (
    clear_trace_id,
    get_trace_id,
    new_trace_id,
    set_trace_id,
    use_or_create_trace_id,
)


def test_new_and_get_trace_id():
    tid = new_trace_id()
    assert len(tid) == 12 and get_trace_id() == tid


def test_set_trace_id():
    set_trace_id("fixed-123")
    assert get_trace_id() == "fixed-123"


def test_use_safe_upstream_trace_or_replace_invalid():
    assert use_or_create_trace_id("api-trace_123") == "api-trace_123"
    generated = use_or_create_trace_id("bad trace\ninjection")
    assert len(generated) == 12 and generated != "bad trace\ninjection"


def test_clear_trace_id_prevents_worker_context_leak():
    set_trace_id("previous-task")
    clear_trace_id()
    assert get_trace_id() == "-"


def test_celery_task_signals_bind_and_clear_trace():
    from app.workers.celery_app import _bind_task_trace, _clear_task_trace

    _bind_task_trace(kwargs={"trace_id": "celery-task-123"})
    assert get_trace_id() == "celery-task-123"
    _clear_task_trace()
    assert get_trace_id() == "-"
