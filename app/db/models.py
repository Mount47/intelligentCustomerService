"""SQLAlchemy 2.0 ORM 模型 —— 11 张表（§8）。

约定：状态字段用 String + 常量（PG/SQLite 双兼容，便于测试，不上原生 enum）；
金额用 Numeric(10,2)；时间默认 func.now()。退款双唯一约束 + 消息幂等键见对应表。

注：Mapped[] 注解用 typing.Optional（SQLAlchemy 会运行时求值，PEP604 `X|None`
在 3.9 上会报错；用 Optional 兼容 3.9/3.11）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class _Created:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(_Created, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    email: Mapped[Optional[str]] = mapped_column(String(128))
    user_level: Mapped[str] = mapped_column(String(16), default="normal")  # normal|vip|...


class Order(_Created, Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    # pending_payment | paid | shipped | delivered | cancelled
    status: Mapped[str] = mapped_column(String(24))
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    # normal | fresh_food | customized_product
    product_type: Mapped[str] = mapped_column(String(24), default="normal")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    logistics: Mapped[Optional["Logistics"]] = relationship(
        back_populates="order", uselist=False
    )


class Logistics(Base):
    __tablename__ = "logistics"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(32))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(64))
    # pending | in_transit | delivered | exception
    status: Mapped[str] = mapped_column(String(24), default="pending")
    last_location: Mapped[Optional[str]] = mapped_column(String(128))
    last_update_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_exception: Mapped[bool] = mapped_column(Boolean, default=False)
    exception_reason: Mapped[Optional[str]] = mapped_column(String(255))

    order: Mapped["Order"] = relationship(back_populates="logistics")


class RefundRequest(_Created, Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        # 退款业务幂等三层之 DB 兜底（§9.2）
        UniqueConstraint("user_id", "idempotency_key", name="uq_refund_user_idem"),
        UniqueConstraint("business_key", name="uq_refund_business"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(String(255))
    # draft | pending_human | approved | rejected | refunded
    status: Mapped[str] = mapped_column(String(24), default="draft")
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    risk_level: Mapped[str] = mapped_column(String(16), default="low")  # low|medium|high
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    business_key: Mapped[str] = mapped_column(String(128), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))  # 参数指纹，判同 key 是否同请求


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"))
    category: Mapped[str] = mapped_column(String(32))           # refund|logistics|complaint|...
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high
    status: Mapped[str] = mapped_column(String(24), default="created")   # 见 state_machine.States
    sla_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(64))
    created_by_agent: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class TicketMessage(_Created, Base):
    __tablename__ = "ticket_messages"
    __table_args__ = (
        # 消息幂等（§9.1）：同一用户同一 client_message_id 只处理一次
        UniqueConstraint("user_id", "client_message_id", name="uq_msg_user_clientid"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))  # 用户消息才填
    sender_type: Mapped[str] = mapped_column(String(16))   # user | agent | human | system
    sender_id: Mapped[Optional[str]] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    client_message_id: Mapped[Optional[str]] = mapped_column(String(128))  # 幂等键


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), index=True)
    current_intent: Mapped[Optional[str]] = mapped_column(String(32))
    current_skill: Mapped[Optional[str]] = mapped_column(String(32))
    current_state: Mapped[Optional[str]] = mapped_column(String(24))
    final_status: Mapped[Optional[str]] = mapped_column(String(24))
    # 异步轮询闭环（§10）
    task_status: Mapped[str] = mapped_column(String(24), default="queued")
    error_message: Mapped[Optional[str]] = mapped_column(String(512))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    total_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # token/cost 可观测（§6/§8）
    model_name: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    rag_doc_count: Mapped[int] = mapped_column(Integer, default=0)
    history_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AgentToolCall(_Created, Base):
    __tablename__ = "agent_tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[Optional[dict]] = mapped_column(JSON)
    output_json: Mapped[Optional[dict]] = mapped_column(JSON)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(String(512))


class KnowledgeDoc(_Created, Base):
    __tablename__ = "knowledge_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QualityReview(_Created, Base):
    """表建好，v1 不写入（QualityReviewSkill 后续，ADR-1/ADR-8 #5）。"""
    __tablename__ = "quality_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id"))
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"))
    resolution_score: Mapped[Optional[int]] = mapped_column(Integer)
    tool_call_correctness: Mapped[Optional[int]] = mapped_column(Integer)
    policy_compliance: Mapped[Optional[int]] = mapped_column(Integer)
    handoff_decision: Mapped[Optional[str]] = mapped_column(String(24))
    risk_level: Mapped[Optional[str]] = mapped_column(String(16))
    suggestion: Mapped[Optional[str]] = mapped_column(Text)


class SlaRecord(_Created, Base):
    __tablename__ = "sla_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    sla_type: Mapped[str] = mapped_column(String(32))   # first_response | resolution
    deadline: Mapped[datetime] = mapped_column(DateTime)
    is_timeout: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
