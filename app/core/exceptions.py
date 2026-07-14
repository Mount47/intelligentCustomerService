"""自定义异常。业务/流程错误集中在此，便于 API 层统一映射。"""
from __future__ import annotations


class SupportFlowError(Exception):
    """所有自定义异常基类。"""

    code = "supportflow_error"
    http_status = 500


class ResourceAccessDenied(SupportFlowError):
    """请求引用了不属于当前用户的订单或工单。"""

    code = "resource_access_denied"
    http_status = 403


class InvalidStateTransition(SupportFlowError):
    """工单状态机非法转移（§7）。"""

    code = "invalid_state_transition"
    http_status = 409


class ConcurrentStateUpdate(SupportFlowError):
    """工单状态已被其他事务推进，当前 stale writer 必须回滚并重试。"""

    code = "concurrent_state_update"
    http_status = 409


class IdempotencyKeyConflict(SupportFlowError):
    """同 idempotency_key 但请求参数不一致（§9.2）。"""

    code = "idempotency_key_conflict"
    http_status = 409


class ToolExecutionError(SupportFlowError):
    """工具执行失败（主流程不应崩，工具内捕获后返回结构化错误）。"""

    code = "tool_execution_error"
    http_status = 502


class LLMError(SupportFlowError):
    """LLM 调用失败（连接/超时/拒答处理）。"""

    code = "llm_error"
    http_status = 502
