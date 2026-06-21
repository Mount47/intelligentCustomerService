"""Agent 执行链路契约（§5）。M2/M3/M4/M5 都照此写，勿随意改字段语义。

包含：AgentContext / Decision / SkillPlan / ToolResult / ToolCallRecord / TokenAcct / Skill 协议。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypedDict

from app.llm.base import Msg, Usage

if TYPE_CHECKING:
    from app.agent.intent_classifier import IntentResult


class ToolResult(TypedDict):
    ok: bool
    data: dict | None
    error: dict | None       # {code, message}；ok=False 时填


@dataclass
class ToolCallRecord:
    """harness 每次调用工具后追加到 ctx.tool_call_records，便于 tracing + 落 agent_tool_calls。"""
    tool_name: str
    input_json: dict
    result: ToolResult
    success: bool
    latency_ms: int
    error_message: str | None = None


@dataclass
class TokenAcct:
    """token/cost 记账（§6、§8 的 agent_sessions 可观测字段）。"""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    cache_hit: bool = False

    def add(self, usage: Usage, model_name: str = "") -> None:
        if model_name:
            self.model_name = model_name
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.total_tokens += usage.total_tokens or (
            usage.prompt_tokens + usage.completion_tokens
        )
        if usage.cache_read_tokens:
            self.cache_hit = True
        # 真实成本折算：按模型价目（缓存读打折），未知/stub → 0
        from app.llm.pricing import estimate_cost
        self.estimated_cost = estimate_cost(
            self.model_name, self.prompt_tokens, self.completion_tokens, self.cache_read_tokens
        )


@dataclass
class SkillPlan:
    """Skill 声明"该怎么做"，不执行 loop（loop 归 harness，§5.2）。"""
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_iterations: int = 6
    required_info: list[str] | None = None


@dataclass
class Decision:
    reply: str
    next_state: str
    need_handoff: bool = False
    handoff_reason: str | None = None
    required_info: list[str] | None = None


@dataclass
class AgentContext:
    session_id: int
    ticket_id: int
    user_id: int
    message: str
    order_id: int | None = None
    intent: str | None = None
    skill: str | None = None
    state: str | None = None
    tool_call_records: list[ToolCallRecord] = field(default_factory=list)
    history: list[Msg] = field(default_factory=list)
    token_acct: TokenAcct = field(default_factory=TokenAcct)
    decision: "Decision | None" = None
    intent_result: "IntentResult | None" = None   # 结构化意图（极性/动作/置信/需确认）


class Skill(Protocol):
    """声明式 Skill：只产出 plan + 据结果 finalize，不自己跑 ReAct loop（§5.2）。"""
    name: str
    triggers: set[str]

    def plan(self, ctx: AgentContext) -> SkillPlan: ...
    # finalize 可拿 tool_ctx 执行确定性写操作（退款草稿/工单/转人工），不把不可逆动作交给 LLM
    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision: ...
