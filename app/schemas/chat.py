"""Chat 接口 schema（前端契约对齐，camelCase）。见 frontend/src/api/types.ts。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field

from app.schemas.common import CamelModel


class ChatMessageIn(CamelModel):
    # 兼容旧客户端仍可携带，但 API 身份只取 Bearer Token；不再信任该字段。
    user_id: Optional[int] = None
    content: str
    client_message_id: Optional[str] = None
    ticket_id: Optional[int] = None
    order_id: Optional[int] = None


class ChatActionIn(CamelModel):
    ticket_id: int
    action_id: str
    decision: Literal["confirm", "cancel"]
    client_action_id: str = Field(min_length=8, max_length=128)


class SendMessageResponse(CamelModel):
    session_id: int
    ticket_id: Optional[int]
    task_status: str
    dedup: bool = False


class AgentTimelineStep(CamelModel):
    id: str
    kind: str                         # intent | skill | tool | risk | reply | state
    title: str
    detail: Optional[str] = None
    status: str = "success"           # pending | running | success | failed
    latency_ms: Optional[int] = None
    created_at: Optional[datetime] = None


class ToolCallView(CamelModel):
    id: int
    session_id: int
    tool_name: str
    input_json: Optional[dict] = None
    output_json: Optional[dict] = None
    success: bool
    latency_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class ChatMessageView(CamelModel):
    id: int
    sender: str                       # user | agent | human | system
    content: str
    created_at: Optional[datetime] = None


class TokenUsage(CamelModel):
    model_name: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    cache_hit: bool = False


class PendingActionView(CamelModel):
    id: str
    type: str
    order_id: int
    amount: float
    created_at: Optional[datetime] = None


class ChatSession(CamelModel):
    id: int
    ticket_id: Optional[int] = None
    task_status: str
    current_intent: Optional[str] = None
    current_skill: Optional[str] = None
    current_state: Optional[str] = None
    final_status: Optional[str] = None
    latest_reply: Optional[str] = None
    messages: List[ChatMessageView] = []
    steps: List[AgentTimelineStep] = []
    tool_calls: List[ToolCallView] = []
    token_usage: TokenUsage = TokenUsage()
    pending_action: Optional[PendingActionView] = None
    total_latency_ms: Optional[int] = None
