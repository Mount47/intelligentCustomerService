"""Admin / 运维监测端 schema（前端契约对齐，camelCase）。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.schemas.chat import AgentTimelineStep, ChatMessageView, ChatSession
from app.schemas.common import CamelModel


class AdminMetrics(CamelModel):
    # 前端 AdminMetrics 字段
    ticket_count: int = 0
    resolved_rate: float = 0.0
    handoff_rate: float = 0.0
    avg_token_cost: float = 0.0
    p95_latency_ms: float = 0.0
    active_sessions: int = 0
    # 削峰可视化超集（前端可后续采用，ADR-12）
    queue_depth: Optional[int] = None
    sessions_by_task_status: dict = {}
    total_tokens: int = 0
    avg_tokens_per_session: float = 0.0


class TicketSummary(CamelModel):
    id: int
    user_id: int
    order_no: Optional[str] = None
    category: str = "other"           # refund | logistics | complaint | other
    priority: str = "normal"
    status: str = ""
    current_state: str = ""
    updated_at: Optional[datetime] = None
    sla_deadline: Optional[datetime] = None


class SessionSummary(CamelModel):
    id: int
    ticket_id: Optional[int] = None
    current_intent: Optional[str] = None
    current_skill: Optional[str] = None
    task_status: str
    total_tokens: int = 0
    estimated_cost: float = 0.0
    total_latency_ms: Optional[int] = None
    updated_at: Optional[datetime] = None


class TicketDetail(TicketSummary):
    state_timeline: List[AgentTimelineStep] = []
    messages: List[ChatMessageView] = []


class SessionDetail(ChatSession):
    pass
