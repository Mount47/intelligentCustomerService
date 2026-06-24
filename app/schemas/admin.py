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
    # SLA 闭环：{total, met, breached, pending, met_rate}（breached 含正在违约的超时工单）
    sla: dict = {}


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


class HandoffSummary(CamelModel):
    """转人工交接摘要：事实字段来自 DB(准)，叙述来自 LLM/模板(TODO-1)。"""
    user_need: str                          # 用户核心诉求
    handoff_reason: str                     # 转人工原因
    order_info: Optional[str] = None        # 涉及订单(DB)
    refund_info: Optional[str] = None       # 退款概况(DB)
    agent_actions: List[str] = []           # Agent 已做(DB)
    conversation_brief: str = ""            # 对话经过(LLM/模板)
    message_count: int = 0
    generated_by: str = "template"          # template | llm:<model>


class TicketDetail(TicketSummary):
    state_timeline: List[AgentTimelineStep] = []
    messages: List[ChatMessageView] = []
    handoff_summary: Optional[HandoffSummary] = None   # 交接摘要(给人工)


class SessionDetail(ChatSession):
    pass
