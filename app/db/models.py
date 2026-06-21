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


# 公共 mixin：凡是需要"创建时间"的表都继承它，避免每张表重复写 created_at。
class _Created:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── 用户表 ───────────────────────────────────────────────────────────────
# 用处：客服系统的"人"。订单、工单、退款、会话都挂在某个用户下。
# 关联：被 orders / tickets / refund_requests / agent_sessions 通过 user_id 外键引用（一对多）。
# 为什么：① 退款必须校验"订单是否属于该用户"（防越权退他人订单，见 refund_service.not_owner）；
#         ② user_level（vip）可用于 SLA/优先级分流，是业务分层的基础维度。
class User(_Created, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    phone: Mapped[Optional[str]] = mapped_column(String(32))      # 选填，可用于人工回访
    email: Mapped[Optional[str]] = mapped_column(String(128))     # 选填
    user_level: Mapped[str] = mapped_column(String(16), default="normal")  # 会员等级 normal|vip|...，影响优先级/SLA


# ── 订单表 ───────────────────────────────────────────────────────────────
# 用处：客服要回答的几乎所有问题都围绕订单（退款/物流/状态查询），它是业务核心实体。
# 关联：属于一个 User（user_id）；一对一关联 Logistics（一个订单一条物流）；被退款/工单引用。
# 为什么这些字段：退款风控直接读这张表做决策（见 refund_service.evaluate_refund_risk）——
#         · total_amount 判断是否高额需人工审核；
#         · product_type 判断生鲜/定制品等"特殊商品"不可随意退；
#         · delivered_at 判断是否超过售后窗口期。
#         这三个字段就是"为什么退款能纯代码确定性决策、不交给 LLM"的数据依据。
class Order(_Created, Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # 所属用户，退款归属校验靠它
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # 对用户展示的订单号（唯一），文本解析也认它
    # 订单状态：待付款 | 已付款 | 已发货 | 已签收 | 已取消（cancelled 不可退）
    status: Mapped[str] = mapped_column(String(24))
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))  # 订单金额，退款风控阈值判断（高额→人工）
    # 商品类型：普通 | 生鲜 | 定制品（后两者属特殊商品，退款需人工审核）
    product_type: Mapped[str] = mapped_column(String(24), default="normal")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)       # 付款时间
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)    # 发货时间
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # 签收时间，判断是否超售后窗口期

    # 一对一关联物流（uselist=False）：order.logistics 直接拿到物流详情
    logistics: Mapped[Optional["Logistics"]] = relationship(
        back_populates="order", uselist=False
    )


# ── 物流表 ───────────────────────────────────────────────────────────────
# 用处：支撑"物流异常"技能（LogisticsExceptionSkill）——查快递到哪了、是否长时间无更新、是否异常。
# 关联：与 Order 一对一（order_id 加了 unique 约束，保证一个订单最多一条物流）。
# 为什么独立成表而不并进 orders：物流字段会被快递回调频繁更新（位置/时间/状态），
#         与订单本身的生命周期解耦；独立表写入互不影响，职责更清晰。
# 为什么有 last_update_time + is_exception：技能据此判断"48h 无更新→建催件工单""异常→转人工"。
class Logistics(Base):
    __tablename__ = "logistics"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)  # unique 保证一单一物流
    carrier: Mapped[Optional[str]] = mapped_column(String(32))      # 承运商（顺丰/中通…）
    tracking_no: Mapped[Optional[str]] = mapped_column(String(64))  # 快递单号
    # 物流状态：待揽收 | 运输中 | 已签收 | 异常
    status: Mapped[str] = mapped_column(String(24), default="pending")
    last_location: Mapped[Optional[str]] = mapped_column(String(128))      # 最新位置，用于向用户播报
    last_update_time: Mapped[Optional[datetime]] = mapped_column(DateTime)  # 最近更新时间，判断是否长时间停滞
    is_exception: Mapped[bool] = mapped_column(Boolean, default=False)      # 是否异常，命中则触发催件/转人工
    exception_reason: Mapped[Optional[str]] = mapped_column(String(255))    # 异常原因说明

    order: Mapped["Order"] = relationship(back_populates="logistics")


# ── 退款申请表（项目招牌：三层幂等）─────────────────────────────────────────
# 用处：记录每一笔退款申请。退款是"不可逆写操作"，所以由代码确定性创建，绝不交给 LLM。
# 关联：属于一个 Order + 一个 User；高风险退款会顺带创建一个 Ticket 转人工。
# 为什么是全项目最讲究的表：退款绝不能因为重试/并发而重复退钱，因此用两个唯一约束 + 三个键
#         构成"三层幂等"防线（实现见 refund_service.create_refund_draft）：
#   层1 idempotency_key：客户端幂等键（按 user 维度唯一），同一请求重发只命中已有记录；
#   层2 business_key   ：业务指纹（user+order+reason），防 Agent/LLM 换个措辞重复发起；
#   层3 DB 唯一约束    ：并发同时插入时，数据库只让一条成功，其余 IntegrityError 回查赢家。
#   request_hash       ：参数指纹，用于识别"同一个 idempotency_key 却带了不同参数"的冲突场景。
class RefundRequest(_Created, Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        # 退款业务幂等三层之 DB 兜底（§9.2）：并发竞争时由这两条唯一约束保证只成一条
        UniqueConstraint("user_id", "idempotency_key", name="uq_refund_user_idem"),  # 层1 兜底
        UniqueConstraint("business_key", name="uq_refund_business"),                  # 层2 兜底
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(String(255))  # 退款原因（自由文本）
    # 退款状态：草稿 | 待人工审核 | 已批准 | 已驳回 | 已退款
    status: Mapped[str] = mapped_column(String(24), default="draft")
    amount: Mapped[float] = mapped_column(Numeric(10, 2))  # 退款金额（取自订单）
    risk_level: Mapped[str] = mapped_column(String(16), default="low")  # 风险等级 low|medium|high
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否需人工审核（高风险=True）
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)  # 层1：客户端幂等键
    business_key: Mapped[str] = mapped_column(String(128), index=True)     # 层2：业务去重键
    request_hash: Mapped[str] = mapped_column(String(64))  # 参数指纹，判同 key 是否同请求（防参数不一致的复用）


# ── 工单表 ───────────────────────────────────────────────────────────────
# 用处：客服处理的"任务单"，是贯穿一次完整服务的主线对象。一次用户咨询对应一个工单，
#       工单的 status 就是状态机（state_machine.States）的落地，驱动整个处理流程。
# 关联：属于一个 User，可选关联 Order；下挂多条 TicketMessage（对话）；与 AgentSession 互相引用；
#       SLA 记录、质检记录都挂在工单上。
# 为什么需要：① 它是"人机协作"的交接点——Agent 处理不了（高风险/投诉/情绪）就把工单转人工；
#         ② created_by_agent 区分工单是 Agent 自动建的还是人工建的，便于运维统计自动化率；
#         ③ status 落地状态机，运维端可据此看每个工单卡在哪一步。
class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"))  # 可选：纯咨询类工单可能无订单
    category: Mapped[str] = mapped_column(String(32))           # 工单类别 退款|物流|投诉|...
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # 优先级 low|normal|high（高风险退款=high）
    status: Mapped[str] = mapped_column(String(24), default="created")   # 工单状态，对应 state_machine.States
    sla_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime)   # SLA 截止时间，超时预警用
    assigned_to: Mapped[Optional[str]] = mapped_column(String(64))       # 指派给哪位人工客服（转人工后填）
    created_by_agent: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否 Agent 自动创建，统计自动化率
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()  # 每次更新自动刷新，用于排序/追踪
    )


# ── 工单消息表（对话记录）─────────────────────────────────────────────────
# 用处：一个工单下的所有往来消息（用户问、Agent 答、人工答、系统提示），即聊天记录。
# 关联：属于一个 Ticket（ticket_id）；用户消息额外记 user_id。
# 为什么有 client_message_id + 唯一约束：这是"消息幂等"（§9.1）——网络重试/用户狂点发送，
#         同一条 client_message_id 只入库、只触发一次 Agent，避免重复处理（链路第一道幂等）。
# 为什么 sender_type 分四类：要在一个时间线里区分"谁说的"，前端展示与质检都需要这个区分。
class TicketMessage(_Created, Base):
    __tablename__ = "ticket_messages"
    __table_args__ = (
        # 消息幂等（§9.1）：同一用户同一 client_message_id 只处理一次（链路最前端的幂等防线）
        UniqueConstraint("user_id", "client_message_id", name="uq_msg_user_clientid"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))  # 仅用户消息才填（agent/system 为空）
    sender_type: Mapped[str] = mapped_column(String(16))   # 发送方 用户 | Agent | 人工 | 系统
    sender_id: Mapped[Optional[str]] = mapped_column(String(64))  # 发送方标识（如人工客服工号）
    content: Mapped[str] = mapped_column(Text)              # 消息正文
    client_message_id: Mapped[Optional[str]] = mapped_column(String(128))  # 客户端消息ID，消息幂等键


# ── Agent 会话表（异步任务 + 可观测中枢）───────────────────────────────────
# 用处：记录 Agent 处理一次用户消息的"全过程档案"——意图/技能/状态、任务执行状态、token 与成本。
#       它是异步闭环的核心：POST 时建一条（queued），worker 处理时更新，GET 轮询读它拿结果。
# 关联：属于 User + Ticket；下挂多条 AgentToolCall（本次会话调了哪些工具）；被质检表引用。
# 为什么把这么多字段放一起：
#   · task_status/error/retry：支撑"异步入队→轮询"闭环与 worker 失败重试（链路见 runner.py）；
#   · current_intent/skill/state + final_status：前端"思考过程时间线"和评测准确率都读它；
#   · token/cost 一组字段：成本可观测，运维端看每次会话花了多少钱、是否命中缓存。
#   一句话：这张表既是"任务表"又是"埋点表"，是把 LLM 黑盒变得可观测、可计费、可重试的关键。
class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), index=True)
    current_intent: Mapped[Optional[str]] = mapped_column(String(32))  # 识别出的意图（退款/物流…）
    current_skill: Mapped[Optional[str]] = mapped_column(String(32))   # 路由到的技能
    current_state: Mapped[Optional[str]] = mapped_column(String(24))   # 当前状态机状态
    final_status: Mapped[Optional[str]] = mapped_column(String(24))    # 最终决策状态（已解决/转人工…）
    # 异步轮询闭环（§10）：任务的生命周期
    task_status: Mapped[str] = mapped_column(String(24), default="queued")  # 排队|处理中|完成|等待用户|转人工|失败|超时
    error_message: Mapped[Optional[str]] = mapped_column(String(512))   # 失败/超时原因
    retry_count: Mapped[int] = mapped_column(Integer, default=0)        # 重试次数，worker 失败重抛累加
    total_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)    # 端到端处理耗时
    # token/cost 可观测（§6/§8）：把 LLM 调用变得可计费
    model_name: Mapped[Optional[str]] = mapped_column(String(64))       # 实际使用的模型名
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)      # 输入 token
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 输出 token
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)       # 总 token
    estimated_cost: Mapped[float] = mapped_column(Numeric(10, 6), default=0)  # 折算成本（6 位小数，金额很小）
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)     # 是否命中缓存（缓存读打折）
    rag_doc_count: Mapped[int] = mapped_column(Integer, default=0)      # 检索到的知识文档数（RAG 预留）
    history_tokens: Mapped[int] = mapped_column(Integer, default=0)     # 历史上下文 token
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # 入队时间
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)    # 开始处理时间
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)   # 处理结束时间
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── Agent 工具调用日志表（审计 / 可解释性）────────────────────────────────
# 用处：逐条记录 Agent 在一次会话里调用了哪个工具、传了什么参数、返回什么、成功否、耗时多少。
# 关联：属于一个 AgentSession（session_id），一次会话多条调用记录（一对多）。
# 为什么必须有这张表：① LLM 决策是黑盒，这张表把"它到底做了什么"落到磁盘，可审计、可复盘；
#         ② 出问题时定位是哪个工具失败/慢（latency_ms）；
#         ③ 前端"思考过程时间线"的工具步骤、评测的 tool_call_success_rate 都来自它。
#         写入是自动的——所有工具走 ToolRegistry.execute 时统一落库（见 tools/base.py），
#         业务代码无需手动埋点。
class AgentToolCall(_Created, Base):
    __tablename__ = "agent_tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))         # 工具名
    input_json: Mapped[Optional[dict]] = mapped_column(JSON)   # 调用入参（结构化）
    output_json: Mapped[Optional[dict]] = mapped_column(JSON)  # 返回结果
    success: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否成功
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)    # 耗时（毫秒），定位慢工具
    error_message: Mapped[Optional[str]] = mapped_column(String(512))  # 失败原因


# ── 知识库文档表 ───────────────────────────────────────────────────────────
# 用处：客服政策/FAQ 文档（如退款政策、物流规则），供 Agent 检索后据此回答、引用政策依据。
# 关联：弱关联——按 category 被知识工具检索（如 refund_policy），不挂外键。
# 为什么需要：① 回答要"有据可依"——退款回复里附"依据：xx政策 v1"就来自这里（policy_ref）；
#         ② version + enabled 支持政策版本管理与灰度上下线，政策变了改文档即可，不用改代码；
#         ③ 是后续向量 RAG 的数据底座（当前按 category 关键词检索，预留升级空间）。
class KnowledgeDoc(_Created, Base):
    __tablename__ = "knowledge_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))                # 文档标题
    category: Mapped[str] = mapped_column(String(32), index=True)  # 分类（refund_policy…），检索入口
    content: Mapped[str] = mapped_column(Text)                     # 正文
    version: Mapped[str] = mapped_column(String(16), default="v1")  # 版本号，回复中引用做依据
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # 是否启用，支持上下线/灰度


# ── 质检记录表（预留）──────────────────────────────────────────────────────
# 用处：对 Agent 的一次服务做质量评分（解决度/工具调用正确性/政策合规/转人工判断是否得当）。
# 关联：引用 AgentSession + Ticket（评的是某次会话/工单）。
# 为什么先建表不写入：v1 暂不做自动质检（QualityReviewSkill 留待后续，ADR-1/ADR-8 #5）。
#         先把表结构定下来，是为了让数据模型一次成型、后续加质检技能时不用改表/迁移。
class QualityReview(_Created, Base):
    """表建好，v1 不写入（QualityReviewSkill 后续，ADR-1/ADR-8 #5）。"""
    __tablename__ = "quality_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id"))
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"))
    resolution_score: Mapped[Optional[int]] = mapped_column(Integer)        # 解决度评分
    tool_call_correctness: Mapped[Optional[int]] = mapped_column(Integer)   # 工具调用正确性评分
    policy_compliance: Mapped[Optional[int]] = mapped_column(Integer)       # 政策合规评分
    handoff_decision: Mapped[Optional[str]] = mapped_column(String(24))     # 转人工判断是否得当
    risk_level: Mapped[Optional[str]] = mapped_column(String(16))           # 风险等级评估
    suggestion: Mapped[Optional[str]] = mapped_column(Text)                 # 改进建议


# ── SLA 记录表（服务时效）──────────────────────────────────────────────────
# 用处：跟踪工单的服务时效承诺——首次响应、最终解决是否在约定时间内完成，是否超时。
# 关联：属于一个 Ticket（ticket_id），一个工单可有多条（首响一条、解决一条）。
# 为什么独立成表而不放进 tickets：一个工单有多种 SLA 维度（first_response / resolution），
#         一对多关系天然要拆表；独立后也便于运维端按 SLA 类型统计超时率。
class SlaRecord(_Created, Base):
    __tablename__ = "sla_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    sla_type: Mapped[str] = mapped_column(String(32))   # SLA 类型：首次响应 | 最终解决
    deadline: Mapped[datetime] = mapped_column(DateTime)              # 承诺截止时间
    is_timeout: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已超时
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # 实际完成时间
