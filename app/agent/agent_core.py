"""AgentCore harness —— 统一执行管道（§5.4）。

ReAct loop 归这里统一跑（不散落各 Skill）；guardrails / 状态机闸门 / token 记账集中一处。
M1.5 骨架：无工具、stub LLM 一轮 end_turn；M3 接 ToolRegistry，M4/M5 接真实 LLM 与业务 Skill。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from app.agent.context import AgentContext, Decision, ToolCallRecord
from app.agent.guardrails import Guardrails
from app.agent.intent_classifier import (
    ActionType,
    Confidence,
    ConfirmSignal,
    HybridIntentClassifier,
    IntentResult,
    Intents,
    Polarity,
    parse_confirmation,
)
from app.agent.skill_router import SkillRouter
from app.agent.state_machine import StateMachine, States
from app.agent.task_planner import OutcomeCoordinator, TaskOutcome, TaskPlanner
from app.core.logging import get_logger
from app.llm.base import LLMClient, Msg, ToolSpec
from app.llm.errors import ContextWindowExceeded
from app.llm.stub import StubLLMClient

if TYPE_CHECKING:
    from app.tools.base import ToolContext

logger = get_logger(__name__)

# P0 硬边界固定话术（确定性，不经 LLM 自由发挥）
_OUT_OF_SCOPE_REPLY = (
    "抱歉，这超出了我的售后服务范围。我可以帮您处理订单查询、退款退货、物流问题等"
    "售后事务，请问有什么可以帮您？"
)
_CLARIFY_REPLY = "您是想查询订单、咨询退款政策，还是提交售后申请？请补充一下，我好为您处理。"
_ABORT_INFO_CUES = ("算了", "取消", "不用了", "不办了")
_ORDER_QUERY_CUES = ("查", "查询", "看看", "状态", "买了什么", "买了啥")
_INFO_TO_SLOT = {"订单号": "order_id"}


class AgentCore:
    def __init__(
        self,
        llm: LLMClient,
        classifier: RuleIntentClassifier,
        router: SkillRouter,
        state_machine: StateMachine,
        guardrails: Guardrails,
        tool_registry=None,   # M3 接入；骨架为 None
        task_planner: TaskPlanner | None = None,
        outcome_coordinator: OutcomeCoordinator | None = None,
    ) -> None:
        self.llm = llm
        self.classifier = classifier
        self.router = router
        self.sm = state_machine
        self.guardrails = guardrails
        self.tools = tool_registry
        self.task_planner = task_planner or TaskPlanner()
        self.outcome_coordinator = outcome_coordinator or OutcomeCoordinator()

    def handle(self, ctx: AgentContext, tool_ctx: "ToolContext | None" = None) -> Decision:
        # 0 初始状态
        if ctx.state is None:
            ctx.state = States.CREATED

        try:
            return self._handle(ctx, tool_ctx)
        except ContextWindowExceeded:
            # 包装层已做过一次有效压缩重试；再失败不原样重试、不继续业务 finalize。
            logger.warning("ticket=%s context still exceeds limit; hand off", ctx.ticket_id)
            ctx.pending_context = None
            if not self.sm.can(ctx.state, States.NEED_HUMAN):
                raise
            return self._finish(ctx, Decision(
                "当前对话内容较长，系统暂时无法安全继续处理，已为您转接人工客服。",
                States.NEED_HUMAN,
                need_handoff=True,
                handoff_reason="context_window_exceeded",
            ))

    def _handle(self, ctx: AgentContext, tool_ctx: "ToolContext | None" = None) -> Decision:

        # 确认握手轮：用专用 parser 强约束（不走通用分类；fail-safe）
        if ctx.state == States.WAITING_USER_CONFIRM:
            return self._handle_confirm(ctx, tool_ctx)
        if ctx.state == States.INFO_REQUIRED and ctx.pending_context:
            return self._handle_required_info(ctx, tool_ctx)

        # 4 意图识别：先得到结构化集合；单意图继续复用原路径，多意图交给线性任务规划器。
        multi_result = self.classifier.classify_multi_intent(ctx.message, history=ctx.history)
        ctx.multi_intent_result = multi_result
        if multi_result.requires_clarification or multi_result.is_multi:
            ctx.intent = "multi_intent"
            ctx.skill = "task_planner"
            ctx.state = self.sm.transition(
                ctx.state, States.INTENT_DETECTED, ticket_id=ctx.ticket_id
            )
            return self._handle_multi_intent(ctx, multi_result, tool_ctx)

        intent_result = multi_result.intents[0]
        ctx.intent = intent_result.intent.value
        ctx.intent_result = intent_result
        ctx.state = self.sm.transition(ctx.state, States.INTENT_DETECTED, ticket_id=ctx.ticket_id)

        # ① scope 硬闸：超范围 → 固定回复，短路（不跑业务 LLM/工具）
        if intent_result.intent == Intents.OUT_OF_SCOPE:
            ctx.skill = "general"
            return self._finish(ctx, Decision(_OUT_OF_SCOPE_REPLY, States.RESOLVED_BY_AGENT))

        # ③ 低置信澄清：不进业务技能、不自由发挥、不误执行
        if intent_result.confidence == Confidence.LOW:
            ctx.skill = "general"
            return self._finish(ctx, Decision(_CLARIFY_REPLY, States.INFO_REQUIRED,
                                              required_info=["明确诉求"]))

        # 5 路由 Skill；6 取 plan
        skill = self.router.route(intent_result.intent)
        ctx.skill = skill.name
        plan = skill.plan(ctx)

        # 6' 缺信息 → 短路 loop，索取信息
        if plan.required_info:
            self._remember_required_info(ctx, skill.name, intent_result, plan.required_info)
            decision = Decision(
                reply="为继续处理，请补充：" + "、".join(plan.required_info),
                next_state=States.INFO_REQUIRED,
                required_info=plan.required_info,
            )
        else:
            # 7 harness 统一跑 ReAct loop（真执行工具 + 回灌）
            self._run_loop(ctx, plan, tool_ctx)
            # 8 Skill 据结果产出决策（可经 tool_ctx 执行确定性写操作）
            decision = skill.finalize(ctx, tool_ctx)

        return self._finish(ctx, decision)

    def _handle_multi_intent(self, ctx: AgentContext, multi_result,
                             tool_ctx: "ToolContext | None") -> Decision:
        task_plan = self.task_planner.plan(
            multi_result, has_order=ctx.order_id is not None
        )
        if task_plan.requires_clarification:
            reason = task_plan.reason or "这些诉求需要拆开处理"
            return self._finish(ctx, Decision(
                f"{reason}。请一次只围绕同一订单说明最多三个诉求，且只提交一个退款或退货申请。",
                States.INFO_REQUIRED,
                required_info=["明确本轮诉求"],
            ))

        ctx.multi_intent_mode = True
        outcomes: list[TaskOutcome] = []
        base_history = list(ctx.history)
        for task in task_plan.tasks:
            result = task.intent_result
            ctx.intent_result = result
            ctx.intent = result.intent.value
            skill = self.router.route(result.intent)
            ctx.skill = skill.name
            skill_plan = skill.plan(ctx)
            if skill_plan.required_info:
                decision = Decision(
                    "为继续处理，请补充：" + "、".join(skill_plan.required_info),
                    States.INFO_REQUIRED,
                    required_info=skill_plan.required_info,
                )
            else:
                # 各任务共享用户历史和订单事实，但不把上一任务的模型草稿塞给下一任务。
                ctx.history = list(base_history)
                self._run_loop(ctx, skill_plan, tool_ctx)
                decision = skill.finalize(ctx, tool_ctx)
            outcome = TaskOutcome(
                intent=result.intent,
                skill=skill.name,
                reply=decision.reply,
                state=decision.next_state,
                need_handoff=decision.need_handoff,
                handoff_reason=decision.handoff_reason,
            )
            outcomes.append(outcome)
            # 只读任务一旦发现投诉、丢件或高风险，后续写任务必须停止。
            if decision.need_handoff or decision.next_state == States.NEED_HUMAN:
                break

        ctx.history = base_history
        ctx.task_outcomes = outcomes
        decision = self.outcome_coordinator.combine(outcomes)
        ctx.intent = "multi_intent"
        ctx.skill = "task_planner"
        return self._finish(ctx, decision)

    def _remember_required_info(self, ctx: AgentContext, skill_name: str,
                                result: IntentResult, required_info: list[str]) -> None:
        slots = [_INFO_TO_SLOT.get(item, item) for item in required_info]
        ctx.pending_context = {
            "intent": result.intent.value,
            "skill": skill_name,
            "required_slots": slots,
            "collected_slots": {},
            "intent_result": {
                "intent": result.intent.value,
                "polarity": result.polarity.value,
                "action_type": result.action_type.value,
                "confidence": result.confidence.value,
                "requires_confirmation": result.requires_confirmation,
                "reason": result.reason,
            },
            "created_at": datetime.utcnow().isoformat(),
        }

    def _handle_required_info(self, ctx: AgentContext,
                              tool_ctx: "ToolContext | None") -> Decision:
        """补齐 INFO_REQUIRED 槽位；明确新意图可打断旧流程。"""
        pending = dict(ctx.pending_context or {})
        try:
            pending_intent = Intents(pending["intent"])
            raw_slots = pending["required_slots"]
            if not isinstance(raw_slots, list) or not all(isinstance(s, str) for s in raw_slots):
                raise TypeError("required_slots must be a string list")
            required_slots = list(raw_slots)
        except (KeyError, TypeError, ValueError):
            # 持久化上下文损坏时不猜旧流程，清理后按新请求重新识别。
            logger.warning("ticket=%s invalid pending_context; restart classification", ctx.ticket_id)
            ctx.pending_context = None
            return self.handle(ctx, tool_ctx)

        current = self.classifier.classify_intent(ctx.message, history=ctx.history)
        if self._is_explicit_new_intent(ctx, pending_intent, current, required_slots):
            logger.info("ticket=%s pending intent %s interrupted by %s", ctx.ticket_id,
                        pending_intent.value, current.intent.value)
            ctx.pending_context = None
            # INFO_REQUIRED -> INTENT_DETECTED 由正常 handle 统一执行。
            return self.handle(ctx, tool_ctx)

        if current.intent == Intents.CANCEL_REFUND or (
                current.intent == Intents.GENERAL_POLICY_QUERY
                and any(cue in (ctx.message or "") for cue in _ABORT_INFO_CUES)):
            ctx.intent = pending_intent.value
            ctx.skill = str(pending.get("skill") or "general")
            ctx.pending_context = None
            return self._finish(ctx, Decision("已取消当前处理，如有其他问题可以继续告诉我。",
                                              States.CLOSED))

        collected = dict(pending.get("collected_slots") or {})
        if "order_id" in required_slots and ctx.order_id is not None:
            collected["order_id"] = ctx.order_id
        missing = [slot for slot in required_slots if collected.get(slot) is None]
        if missing:
            pending["collected_slots"] = collected
            ctx.pending_context = pending
            ctx.intent = pending_intent.value
            ctx.skill = str(pending.get("skill") or "general")
            labels = ["订单号" if slot == "order_id" else slot for slot in missing]
            return self._finish(ctx, Decision(
                "还需要您补充：" + "、".join(labels), States.INFO_REQUIRED,
                required_info=labels))

        ctx.intent_result = self._restore_intent_result(pending, pending_intent)
        ctx.intent = pending_intent.value
        skill = self.router.route(pending_intent)
        ctx.skill = skill.name
        plan = skill.plan(ctx)
        if plan.required_info:
            # Skill 版本变化后可能新增槽位；合并并继续等待，不能带缺参硬跑。
            extra = [_INFO_TO_SLOT.get(item, item) for item in plan.required_info]
            pending["required_slots"] = list(dict.fromkeys(required_slots + extra))
            pending["collected_slots"] = collected
            ctx.pending_context = pending
            return self._finish(ctx, Decision(
                "还需要您补充：" + "、".join(plan.required_info), States.INFO_REQUIRED,
                required_info=plan.required_info))

        # 槽位已补齐：显式走状态机的恢复路径，再继续原 Skill。
        ctx.pending_context = None
        ctx.state = self.sm.transition(ctx.state, States.INFO_COLLECTED, ticket_id=ctx.ticket_id)
        ctx.state = self.sm.transition(ctx.state, States.TOOL_EXECUTING, ticket_id=ctx.ticket_id)
        self._run_loop(ctx, plan, tool_ctx)
        return self._finish(ctx, skill.finalize(ctx, tool_ctx))

    def _is_explicit_new_intent(self, ctx: AgentContext, pending_intent: Intents,
                                current: IntentResult, required_slots: list[str]) -> bool:
        if current.confidence == Confidence.LOW or current.intent == Intents.GENERAL_POLICY_QUERY:
            return False
        if current.intent == pending_intent or current.intent == Intents.CANCEL_REFUND:
            return False
        # “订单 SO-xxx”是补订单号，不是订单查询；出现明确查询动词才算打断。
        if current.intent == Intents.ORDER_QUERY and "order_id" in required_slots \
                and ctx.order_id is not None:
            return any(cue in (ctx.message or "") for cue in _ORDER_QUERY_CUES)
        return True

    @staticmethod
    def _restore_intent_result(pending: dict, fallback_intent: Intents) -> IntentResult:
        raw = pending.get("intent_result") or {}
        try:
            return IntentResult(
                intent=Intents(raw.get("intent", fallback_intent.value)),
                polarity=Polarity(raw.get("polarity", Polarity.NEUTRAL.value)),
                action_type=ActionType(raw.get("action_type", ActionType.NONE.value)),
                confidence=Confidence(raw.get("confidence", Confidence.MEDIUM.value)),
                requires_confirmation=bool(raw.get("requires_confirmation", False)),
                reason=str(raw.get("reason", "跨轮补槽恢复")),
            )
        except (TypeError, ValueError):
            return IntentResult(fallback_intent, reason="跨轮补槽恢复（字段降级）")

    def _handle_confirm(self, ctx: AgentContext, tool_ctx: "ToolContext | None") -> Decision:
        # ② 确认态强约束：强确认→执行 pending_action / 强取消→清理 / 其他→保持等待
        sig = parse_confirmation(ctx.message)
        if sig == ConfirmSignal.UNCLEAR:
            ctx.intent, ctx.skill = Intents.REFUND_CONFIRMATION.value, "refund_handling"
            return self._finish(ctx, Decision(
                "请回复『确认』以继续，或『取消』放弃本次申请。", States.WAITING_USER_CONFIRM))
        if self._confirm_contains_new_request(ctx, tool_ctx):
            ctx.intent, ctx.skill = Intents.REFUND_CONFIRMATION.value, "refund_handling"
            return self._finish(ctx, Decision(
                "确认退款或退货时不能同时夹带新诉求。请先单独回复『确认』或『取消』，"
                "完成后再查询其他事项。",
                States.WAITING_USER_CONFIRM,
            ))
        intent = (Intents.REFUND_CONFIRMATION if sig == ConfirmSignal.CONFIRM
                  else Intents.CANCEL_REFUND)
        ctx.intent = intent.value
        ctx.intent_result = IntentResult(
            intent,
            Polarity.POSITIVE if sig == ConfirmSignal.CONFIRM else Polarity.NEGATIVE,
            ActionType.CONFIRM if sig == ConfirmSignal.CONFIRM else ActionType.CANCEL,
            Confidence.HIGH, False, f"确认态 parser: {sig.value}")
        skill = self.router.route(intent)
        ctx.skill = skill.name
        plan = skill.plan(ctx)
        self._run_loop(ctx, plan, tool_ctx)
        decision = skill.finalize(ctx, tool_ctx)
        return self._finish(ctx, decision)

    def _confirm_contains_new_request(self, ctx: AgentContext,
                                      tool_ctx: "ToolContext | None") -> bool:
        """确认协议只允许当前 pending_action 对应的退款族表达。"""
        if tool_ctx is None:
            return False
        from app.db.models import Ticket

        ticket = tool_ctx.db.get(Ticket, ctx.ticket_id)
        pending_type = (ticket.pending_action or {}).get("type") if ticket else None
        allowed = {
            Intents.RETURN_REQUEST if pending_type == Intents.RETURN_REQUEST.value
            else Intents.REFUND_REQUEST
        }
        candidates = set(self.classifier.rule.recall(ctx.message))
        non_refund = candidates - {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST}
        if non_refund:
            return True
        text = ctx.message or ""
        # “帮我退吧”是合法确认；只有明确切换退款/退货种类才视为夹带第二个写诉求。
        if allowed == {Intents.REFUND_REQUEST}:
            return any(cue in text for cue in ("退货", "退回", "寄回"))
        return any(cue in text for cue in ("退款", "退钱", "退费"))

    def _finish(self, ctx: AgentContext, decision: Decision) -> Decision:
        # 9 guardrails 回复后校验（绝不绕过）；10 状态机集中转移
        decision.reply = self.guardrails.postcheck(decision.reply)
        ctx.state = self.sm.transition(ctx.state, decision.next_state, ticket_id=ctx.ticket_id)
        ctx.decision = decision
        return decision

    def _run_loop(self, ctx: AgentContext, plan, tool_ctx: "ToolContext | None") -> None:
        # 据 plan.allowed_tools 从 registry 构造工具规格（白名单约束 LLM 可调工具）
        specs: list[ToolSpec] | None = None
        if plan.allowed_tools and self.tools is not None:
            specs = self.tools.specs(plan.allowed_tools)
        allowed = set(plan.allowed_tools or [])
        messages = list(ctx.history) + [Msg(role="user", content=ctx.message)]
        # 上下文注入（#5）：把已知 order_id/user_id 给 LLM，调工具时填对参数、不瞎猜
        system = plan.system_prompt + _context_note(ctx)

        for _ in range(max(1, plan.max_iterations)):
            resp = self.llm.chat(system=system, messages=messages, tools=specs)
            ctx.token_acct.add(resp.usage, self.llm.model_name)

            if resp.stop_reason == "tool_use" and resp.tool_calls:
                # 回放 assistant 的工具调用，再逐个执行并回灌结果
                messages.append(Msg(role="assistant", content=resp.text,
                                    tool_calls=resp.tool_calls))
                for tc in resp.tool_calls:
                    result = self._exec_tool(ctx, tool_ctx, allowed, tc)
                    messages.append(Msg(role="tool",
                                        content=json.dumps(result, ensure_ascii=False),
                                        tool_call_id=tc.id))
                continue
            # end_turn / max_tokens / refusal → 收尾
            if resp.text:
                ctx.history.append(Msg(role="assistant", content=resp.text))
            break

    def _exec_tool(self, ctx: AgentContext, tool_ctx, allowed: set, tc) -> dict:
        """执行单个工具调用，返回 ToolResult，并把记录追加到 ctx.tool_call_records。"""
        # 白名单外的工具拒绝执行（防 LLM 越权调用）
        if tc.name not in allowed:
            result = {"ok": False, "data": None,
                      "error": {"code": "tool_not_allowed", "message": f"工具 {tc.name} 不在本技能白名单"}}
            record = ToolCallRecord(tc.name, tc.arguments, result, False, 0, "not allowed")
            ctx.tool_call_records.append(record)
            if tool_ctx is not None and self.tools is not None:
                self.tools.audit(tool_ctx, record)
            return result
        # 危险写工具绝不由 LLM 执行；确定性写操作只在 Skill.finalize 内发生。
        if not self.guardrails.precheck(tc.name, tc.arguments):
            message = f"危险工具 {tc.name} 只能由确定性业务流程执行"
            result = {"ok": False, "data": None,
                      "error": {"code": "dangerous_tool_blocked", "message": message}}
            record = ToolCallRecord(tc.name, tc.arguments, result, False, 0, message)
            ctx.tool_call_records.append(record)
            if tool_ctx is not None and self.tools is not None:
                self.tools.audit(tool_ctx, record)
            return result
        if tool_ctx is None or self.tools is None:
            result = {"ok": False, "data": None,
                      "error": {"code": "no_tool_context", "message": "缺少工具执行上下文"}}
            ctx.tool_call_records.append(
                ToolCallRecord(tc.name, tc.arguments, result, False, 0, "no tool context"))
            return result
        # 上下文参数绑定（#5 硬保证 + 越权防护）：user_id/order_id 用会话真值覆盖 LLM 所填，
        # LLM 填错/越权(传他人 id)都无效。只绑工具 schema 声明了的参数，避免 unexpected kwarg。
        args = self._bind_context_args(ctx, tc.name, tc.arguments)
        result, record = self.tools.execute(tool_ctx, tc.name, args)
        ctx.tool_call_records.append(record)
        return result

    def _bind_context_args(self, ctx: AgentContext, name: str, args: dict | None) -> dict:
        bound = dict(args or {})
        props = self.tools.schema_props(name)
        if "user_id" in props:                       # 强制会话用户，防 LLM 越权查他人
            bound["user_id"] = ctx.user_id
        if "order_id" in props and ctx.order_id is not None:  # 强制本次会话订单
            bound["order_id"] = ctx.order_id
        return bound


def _context_note(ctx: AgentContext) -> str:
    """把后端已知的标识拼进系统提示，供 LLM 调工具时填参（#5 上下文注入）。

    退款决策仍由 finalize 用 ctx 真值确定性执行；这里只是让 loop 内的只读工具调用
    填对 order_id/user_id、避免瞎猜导致 FAILED，并让 LLM 的解释有据可依。
    """
    parts = [f"user_id={ctx.user_id}"]
    if ctx.order_id is not None:
        parts.append(f"本次咨询订单 order_id={ctx.order_id}")
    return "\n\n【当前上下文】" + "；".join(parts) + "（调用工具需要 id 时用这些值，勿编造）"


def build_default_agent(llm: LLMClient | None = None, tool_registry=None) -> AgentCore:
    """默认装配。骨架/测试用 stub LLM；M4/M5 换 registry 选真实 provider。

    tool_registry 默认装载全部业务工具（M3）；harness 据 SkillPlan.allowed_tools 取规格。
    """
    if tool_registry is None:
        from app.tools.registry import build_tool_registry
        tool_registry = build_tool_registry()
    llm = llm or StubLLMClient()
    # 注册业务 Skill（未认领的意图落兜底 GeneralSkill）
    from app.skills.logistics_exception.handler import LogisticsExceptionSkill
    from app.skills.order_query import OrderQuerySkill
    from app.skills.refund_handling.handler import RefundHandlingSkill
    router = SkillRouter(skills=[RefundHandlingSkill(), LogisticsExceptionSkill(), OrderQuerySkill()])
    return AgentCore(
        llm=llm,
        classifier=HybridIntentClassifier(llm=llm),  # 规则快路 + 同一 LLM 兜底
        router=router,
        state_machine=StateMachine(),
        guardrails=Guardrails(),
        tool_registry=tool_registry,
        task_planner=TaskPlanner(),
        outcome_coordinator=OutcomeCoordinator(),
    )
