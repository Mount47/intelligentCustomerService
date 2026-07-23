"""前端对接：统一 camelCase 序列化（前端契约用 camelCase；后端内部仍 snake_case）。

响应按 alias(camel) 输出（FastAPI 默认 response_model_by_alias=True）；
请求同时接受 camel 与 snake（populate_by_name=True）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),   # 允许 model_name 等字段
    )


# 内部 task_status → 前端 TaskStatus 枚举。
# waiting_user_input 必须独立暴露：它表示流程暂停等待用户补充/确认，不是“已解决”。
_FRONTEND_TASK_STATUS = {
    "queued": "queued",
    "processing": "processing",
    "completed": "final",
    "waiting_user_input": "waiting_user_input",
    "need_human": "need_human",
    "failed": "failed",
    "timeout": "failed",
}


def to_frontend_task_status(internal: str | None) -> str:
    return _FRONTEND_TASK_STATUS.get(internal or "", "processing")
