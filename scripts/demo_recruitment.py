"""秋招 5 分钟演示：只展示 SupportFlow 最硬核的业务与工程能力。

无需 API Key、Redis、Celery 或 Docker：使用内存 SQLite + 可复现的工具调用模型，
依次验证跨轮补槽、退款二次确认、消息幂等、高风险转人工、物流异常、IDOR 拦截
和工具调用审计。任一关键断言失败，脚本会直接退出非零。

运行：
    python -m scripts.demo_recruitment
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.core.exceptions import ResourceAccessDenied
from app.core.logging import setup_logging
from app.db.models import AgentToolCall, Base, Logistics, Order, RefundRequest, User
from app.llm.base import LLMResponse, Msg, ToolCall, ToolSpec, Usage
from app.observability.metrics import compute_metrics
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session


class DemoToolLLM:
    """离线、确定性地发起一次白名单内只读工具调用，方便演示完整 harness。"""

    model_name = "demo-tool-llm"

    def __init__(self) -> None:
        self._call_id = 0

    def chat(self, *, system: str, messages: list[Msg],
             tools: list[ToolSpec] | None = None, stream: bool = False) -> LLMResponse:
        usage = Usage(prompt_tokens=12, completion_tokens=5, total_tokens=17)
        # 工具结果已回灌时结束本轮；否则从 Skill 白名单里选择第一个只读工具。
        if tools and (not messages or messages[-1].role != "tool"):
            spec = tools[0]
            args: dict = {}
            props = spec.input_schema.get("properties", {})
            order_match = re.search(r"order_id=(\d+)", system)
            user_match = re.search(r"user_id=(\d+)", system)
            if "order_id" in props and order_match:
                args["order_id"] = int(order_match.group(1))
            if "user_id" in props and user_match:
                args["user_id"] = int(user_match.group(1))
            if "query" in props:
                args["query"] = next(
                    (m.content for m in reversed(messages) if m.role == "user"), "售后政策")
            required = set(spec.input_schema.get("required", []))
            if required <= set(args):
                self._call_id += 1
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(
                        id=f"demo-call-{self._call_id}", name=spec.name, arguments=args)],
                    stop_reason="tool_use",
                    usage=usage,
                )
        return LLMResponse(text="只读事实核对完成。", stop_reason="end_turn", usage=usage)


def _run_turn(db, agent, *, label: str, user_id: int, content: str,
              order_id: int | None = None, ticket_id: int | None = None,
              client_message_id: str | None = None, expected_state: str | None = None):
    sess, dedup = chat_service.accept_message(db, ChatMessageIn(
        user_id=user_id,
        content=content,
        order_id=order_id,
        ticket_id=ticket_id,
        client_message_id=client_message_id,
    ))
    if not dedup:
        run_agent_session(db, sess.id, agent=agent)
    view = chat_service.get_session_view(db, sess.id)
    assert view is not None
    if expected_state:
        assert view.final_status == expected_state, (label, view.final_status, expected_state)
    tool_names = list(db.scalars(select(AgentToolCall.tool_name).where(
        AgentToolCall.session_id == sess.id).order_by(AgentToolCall.id)).all())
    print(f"\n[{label}] {content}")
    print(f"  intent={view.current_intent} skill={view.current_skill} state={view.final_status}")
    print(f"  tools={tool_names or '无（确定性状态处理）'}")
    print(f"  reply={view.latest_reply}")
    return sess, view, dedup


def _seed(db):
    now = datetime.utcnow()
    user = User(username="candidate_demo")
    attacker = User(username="attacker_demo")
    db.add_all([user, attacker])
    db.flush()
    low = Order(user_id=user.id, order_no="AUTUMN-REFUND-LOW", status="paid",
                total_amount=120, product_type="normal", paid_at=now)
    high = Order(user_id=user.id, order_no="AUTUMN-REFUND-HIGH", status="paid",
                 total_amount=999, product_type="normal", paid_at=now)
    shipped = Order(user_id=user.id, order_no="AUTUMN-LOGISTICS", status="shipped",
                    total_amount=80, product_type="normal", paid_at=now,
                    shipped_at=now - timedelta(days=3))
    db.add_all([low, high, shipped])
    db.flush()
    db.add(Logistics(
        order_id=shipped.id,
        status="in_transit",
        carrier="顺丰",
        last_location="上海转运中心",
        last_update_time=now - timedelta(hours=60),
    ))
    db.commit()
    return user, attacker, low, high, shipped


def main() -> None:
    setup_logging("WARNING")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as db:
        user, attacker, low, high, shipped = _seed(db)
        agent = build_default_agent(DemoToolLLM())

        first, _, _ = _run_turn(
            db, agent, label="1/6 跨轮补槽", user_id=user.id,
            content="我要退款", client_message_id="autumn-1",
            expected_state="info_required")
        _, pending, _ = _run_turn(
            db, agent, label="2/6 订单绑定+只读核对", user_id=user.id,
            content=low.order_no, ticket_id=first.ticket_id, client_message_id="autumn-2",
            expected_state="waiting_user_confirm")
        confirmed, _, _ = _run_turn(
            db, agent, label="3/6 二次确认后确定性写入", user_id=user.id,
            content="确认，提交退款", ticket_id=pending.ticket_id,
            client_message_id="autumn-confirm", expected_state="resolved_by_agent")

        # 同一客户端消息重放：直接复用原 session，不再次执行 Agent，不重复建退款。
        replay, dedup = chat_service.accept_message(db, ChatMessageIn(
            user_id=user.id,
            content="确认，提交退款",
            ticket_id=confirmed.ticket_id,
            client_message_id="autumn-confirm",
        ))
        refund_count = db.scalar(select(func.count()).select_from(RefundRequest).where(
            RefundRequest.order_id == low.id))
        assert dedup is True and replay.id == confirmed.id and refund_count == 1
        print("\n[4/6 消息幂等] 相同 client_message_id 重放：dedup=true，退款申请仍为 1 条")

        _run_turn(
            db, agent, label="5/6 高风险确定性转人工", user_id=user.id,
            content="这个订单太贵了，我要退款", order_id=high.id,
            client_message_id="autumn-high", expected_state="need_human")
        _run_turn(
            db, agent, label="6/6 物流异常催件", user_id=user.id,
            content="我的快递三天没动了", order_id=shipped.id,
            client_message_id="autumn-logistics", expected_state="resolved_by_agent")

        try:
            chat_service.accept_message(db, ChatMessageIn(
                user_id=attacker.id, content="查询这个订单", order_id=low.id))
        except ResourceAccessDenied:
            print("\n[安全边界] 非订单所有者查询：403 ResourceAccessDenied（IDOR 已拦截）")
        else:
            raise AssertionError("IDOR protection did not reject another user's order")

        metrics = compute_metrics(db)
        print("\n[可观测结果]")
        print(f"  sessions={metrics['totals']['sessions']} tickets={metrics['totals']['tickets']} "
              f"tool_calls={metrics['totals']['tool_calls']}")
        print(f"  tool_success_rate={metrics['agent']['tool_call_success_rate']} "
              f"total_tokens={metrics['cost']['total_tokens']}")
        print("\nPASS：秋招核心链路全部通过。")


if __name__ == "__main__":
    main()
