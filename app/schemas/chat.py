"""Chat 接口 schema（§14）。所有 API 用 Pydantic。

注：字段用 typing.Optional（Pydantic v2 在 3.9 会运行时求值，PEP604 `X|None` 报错；
Optional 兼容 3.9/3.11）。
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChatMessageIn(BaseModel):
    user_id: int
    content: str
    ticket_id: Optional[int] = None
    order_id: Optional[int] = None
    client_message_id: Optional[str] = None   # 消息幂等键（§9.1）


class ChatMessageAccepted(BaseModel):
    session_id: int
    ticket_id: int
    task_status: str
    dedup: bool = False                        # True=命中消息幂等，未重复入队


class Step(BaseModel):
    """思考过程时间线的一格（给前端，ADR-10）。"""
    kind: str            # intent | skill | tool | state
    label: str
    detail: Optional[str] = None
    ok: Optional[bool] = None


class TokenView(BaseModel):
    model: Optional[str] = None
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cost: float = 0.0


class SessionView(BaseModel):
    session_id: int
    ticket_id: Optional[int]
    task_status: str
    current_intent: Optional[str]
    current_skill: Optional[str]
    current_state: Optional[str]
    latest_reply: Optional[str]
    final_status: Optional[str]
    error_message: Optional[str]
    retry_count: int
    steps: List[Step]
    tokens: TokenView
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
