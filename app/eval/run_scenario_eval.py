"""面向真实模型的场景评测。

与 ``run_eval.py`` 的回归集不同，本程序支持：
- 一条 case 创建多笔可引用订单；
- 多轮补槽、确认、取消、流程打断和确认过期；
- 预期工具、禁止工具、回复事实和数据库副作用断言；
- 按 category / severity 汇总，并记录模型调用、延迟、token 与成本；
- ``--repeat`` 重复运行，观察真实模型非确定性。

默认仍使用 stub，只有显式 ``--real --confirm-paid-run`` 才调用付费模型。
场景均在临时内存数据库中执行，不污染开发数据库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.core.config import get_settings
from app.core.exceptions import ResourceAccessDenied
from app.db.models import (
    AgentToolCall,
    Base,
    KnowledgeDoc,
    Logistics,
    Order,
    OrderItem,
    RefundRequest,
    Ticket,
    User,
)
from app.llm.base import LLMClient, LLMResponse, Msg, ToolSpec
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session

_DATASET = Path(__file__).parent / "real_scenario_cases.json"
_ALLOWED_SEVERITIES = {"critical", "high", "normal"}
_ALLOWED_STATES = {
    "resolved_by_agent", "need_human", "info_required", "waiting_user_confirm",
    "closed", "access_denied",
}


class InstrumentedLLMClient:
    """不改变模型行为，只记录离线评测所需的调用次数和墙钟延迟。"""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self.model_name = getattr(client, "model_name", "")
        self.calls = 0
        self.latencies_ms: list[float] = []
        self.errors = 0

    def chat(
        self, *, system: str, messages: list[Msg],
        tools: list[ToolSpec] | None = None, stream: bool = False,
    ) -> LLMResponse:
        started = time.perf_counter()
        self.calls += 1
        try:
            return self._client.chat(system=system, messages=messages, tools=tools, stream=stream)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.latencies_ms.append((time.perf_counter() - started) * 1000)

    def snapshot(self) -> tuple[int, int, float]:
        return self.calls, self.errors, sum(self.latencies_ms)


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def load_dataset(path: Path = _DATASET) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_dataset(payload)
    return payload


def validate_dataset(payload: dict) -> None:
    """尽早拒绝拼错字段或缺少标签的数据，避免花钱后才发现评测集无效。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("dataset must be an object containing a cases list")
    if not payload["cases"]:
        raise ValueError("dataset contains no cases")

    case_ids: set[str] = set()
    for case in payload["cases"]:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every case requires a non-empty case_id")
        if case_id in case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        if not isinstance(case.get("category"), str):
            raise ValueError(f"{case_id}: category is required")
        if case.get("severity", "normal") not in _ALLOWED_SEVERITIES:
            raise ValueError(f"{case_id}: invalid severity")

        aliases: set[str] = set()
        for order in case.get("orders", []):
            alias = order.get("alias")
            if not isinstance(alias, str) or not alias or alias in aliases:
                raise ValueError(f"{case_id}: order aliases must be unique non-empty strings")
            aliases.add(alias)
            if order.get("owner", "main") not in {"main", "other"}:
                raise ValueError(f"{case_id}/{alias}: owner must be main or other")

        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{case_id}: turns must be a non-empty list")
        for index, turn in enumerate(turns, 1):
            prefix = f"{case_id}#t{index}"
            if not isinstance(turn.get("message"), str) or not turn["message"]:
                raise ValueError(f"{prefix}: message is required")
            if turn.get("attach_order") is not None and turn["attach_order"] not in aliases:
                raise ValueError(f"{prefix}: unknown attach_order alias")
            expected = turn.get("expected")
            if not isinstance(expected, dict):
                raise ValueError(f"{prefix}: expected object is required")
            if expected.get("state") not in _ALLOWED_STATES:
                raise ValueError(f"{prefix}: expected.state is invalid")
            for field in ("intents", "skills", "required_tools", "any_tools", "forbidden_tools",
                          "reply_all", "reply_any", "reply_none"):
                if field in expected and not isinstance(expected[field], list):
                    raise ValueError(f"{prefix}: expected.{field} must be a list")


def _short_order_no(case_id: str, alias: str, repetition: int) -> str:
    digest = hashlib.sha1(f"{case_id}:{alias}:{repetition}".encode()).hexdigest()[:10].upper()
    return f"EV{repetition:02d}-{digest}"


def _make_order(
    db: Session, users: dict[str, User], case_id: str, spec: dict, repetition: int,
) -> Order:
    now = datetime.utcnow()
    owner = users[spec.get("owner", "main")]
    status = spec.get("status", "paid")
    delivered_days = spec.get("delivered_days_ago")
    order = Order(
        user_id=owner.id,
        order_no=_short_order_no(case_id, spec["alias"], repetition),
        status="delivered" if delivered_days is not None else status,
        total_amount=spec.get("amount", 128.0),
        product_type=spec.get("product_type", "normal"),
        paid_at=now - timedelta(days=12),
        shipped_at=(now - timedelta(days=8)) if status in {"shipped", "delivered"} or delivered_days is not None else None,
        delivered_at=(now - timedelta(days=float(delivered_days))) if delivered_days is not None else None,
    )
    db.add(order)
    db.flush()

    items = spec.get("items") or [
        {"product_name": "纯棉短袖", "quantity": 1, "unit_price": spec.get("amount", 128.0)}
    ]
    for item in items:
        db.add(OrderItem(
            order_id=order.id,
            product_name=item["product_name"],
            quantity=item.get("quantity", 1),
            unit_price=item.get("unit_price", 1.0),
        ))

    logistics = spec.get("logistics")
    if logistics is not None:
        db.add(Logistics(
            order_id=order.id,
            status=logistics.get("status", "in_transit"),
            carrier=logistics.get("carrier", "中通快递"),
            tracking_no=f"YT{order.id:010d}",
            last_location=logistics.get("last_location", "华东转运中心"),
            last_update_time=now - timedelta(hours=float(logistics.get("hours_ago", 2))),
            is_exception=bool(logistics.get("is_exception", False)),
            exception_reason=logistics.get("reason"),
        ))
    db.flush()
    return order


def _seed_knowledge(db: Session) -> None:
    db.add_all([
        KnowledgeDoc(
            title="售后退款政策", category="refund_policy", version="v2026.1", enabled=True,
            content="普通商品签收七天内可申请售后；高金额、生鲜和定制商品需要人工审核。",
        ),
        KnowledgeDoc(
            title="物流异常处理规范", category="logistics_policy", version="v2026.1", enabled=True,
            content="物流超过48小时未更新可发起催件；显示签收但用户未收到应转人工核实。",
        ),
    ])


def _render_message(template: str, orders: dict[str, Order]) -> str:
    rendered = template
    for alias, order in orders.items():
        rendered = rendered.replace(f"{{{{order_no:{alias}}}}}", order.order_no)
        rendered = rendered.replace(f"{{{{order_id:{alias}}}}}", str(order.id))
    if "{{order_" in rendered:
        raise ValueError(f"unresolved order placeholder in message: {rendered}")
    return rendered


def _apply_before(db: Session, ticket_id: int | None, orders: dict[str, Order], before: dict) -> None:
    if not before:
        return
    if "set_order_amount" in before:
        for alias, amount in before["set_order_amount"].items():
            orders[alias].total_amount = amount
    if before.get("corrupt_pending_context"):
        if ticket_id is None:
            raise ValueError("cannot corrupt pending context before a ticket exists")
        db.get(Ticket, ticket_id).pending_context = {"broken": True}
    if "pending_action_age_seconds" in before:
        if ticket_id is None:
            raise ValueError("cannot age pending action before a ticket exists")
        ticket = db.get(Ticket, ticket_id)
        pending = dict(ticket.pending_action or {})
        if not pending:
            raise ValueError("cannot age a missing pending action")
        pending["created_at"] = (
            datetime.utcnow() - timedelta(seconds=float(before["pending_action_age_seconds"]))
        ).isoformat()
        ticket.pending_action = pending
    db.flush()


def _contains_all(text: str, needles: list[str]) -> bool:
    return all(needle in text for needle in needles)


def _contains_any(text: str, needles: list[str]) -> bool:
    return not needles or any(needle in text for needle in needles)


def _assert_turn(
    expected: dict, *, view, tool_calls: list[AgentToolCall], refund_count: int,
    access_denied: bool, deduplicated: bool,
) -> tuple[list[str], dict]:
    failures: list[str] = []
    actual_state = "access_denied" if access_denied else view.final_status
    intent = None if access_denied else view.current_intent
    skill = None if access_denied else view.current_skill
    reply = "" if access_denied else (view.latest_reply or "")

    intents = expected.get("intents")
    if intents and intent not in intents:
        failures.append(f"intent expected one of {intents}, got {intent!r}")
    skills = expected.get("skills")
    if skills and skill not in skills:
        failures.append(f"skill expected one of {skills}, got {skill!r}")
    if "skill" in expected and skill != expected["skill"]:
        failures.append(f"skill expected {expected['skill']!r}, got {skill!r}")
    if actual_state != expected["state"]:
        failures.append(f"state expected {expected['state']!r}, got {actual_state!r}")
    if "handoff" in expected and (actual_state == "need_human") != expected["handoff"]:
        failures.append(f"handoff expected {expected['handoff']}, got {actual_state == 'need_human'}")

    tool_names = [call.tool_name for call in tool_calls]
    successful_tools = [call.tool_name for call in tool_calls if call.success]
    failed_tools = [call.tool_name for call in tool_calls if not call.success]
    required = expected.get("required_tools", [])
    missing = [name for name in required if name not in successful_tools]
    if missing:
        failures.append(f"required tools not called successfully: {missing}")
    any_tools = expected.get("any_tools", [])
    if any_tools and not any(name in successful_tools for name in any_tools):
        failures.append(f"none of expected alternative tools succeeded: {any_tools}")
    forbidden_called = [name for name in expected.get("forbidden_tools", []) if name in tool_names]
    if forbidden_called:
        failures.append(f"forbidden tools called: {forbidden_called}")
    if expected.get("all_tools_must_succeed") and failed_tools:
        failures.append(f"tools failed: {failed_tools}")
    if "max_tool_calls" in expected and len(tool_names) > expected["max_tool_calls"]:
        failures.append(f"tool calls {len(tool_names)} exceed max {expected['max_tool_calls']}")

    if "refund_count" in expected and refund_count != expected["refund_count"]:
        failures.append(f"refund count expected {expected['refund_count']}, got {refund_count}")
    if "deduplicated" in expected and deduplicated != expected["deduplicated"]:
        failures.append(f"deduplicated expected {expected['deduplicated']}, got {deduplicated}")
    if not _contains_all(reply, expected.get("reply_all", [])):
        failures.append(f"reply misses required text: {expected.get('reply_all')}")
    if not _contains_any(reply, expected.get("reply_any", [])):
        failures.append(f"reply misses all alternative text: {expected.get('reply_any')}")
    forbidden_reply = [text for text in expected.get("reply_none", []) if text in reply]
    if forbidden_reply:
        failures.append(f"reply contains forbidden text: {forbidden_reply}")

    return failures, {
        "intent": intent,
        "skill": skill,
        "state": actual_state,
        "reply": reply,
        "tools": tool_names,
        "successful_tools": successful_tools,
        "failed_tools": failed_tools,
        "refund_count": refund_count,
        "deduplicated": deduplicated,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * p)))
    return round(ordered[index], 2)


def _summarize(results: list[dict], model_name: str, repetition_count: int) -> dict:
    turns = len(results)
    passed = sum(item["passed"] for item in results)
    critical = [item for item in results if item["severity"] == "critical"]
    categories: dict[str, dict] = {}
    for category, rows in sorted(_group_by(results, "category").items()):
        categories[category] = {
            "turns": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "pass_rate": round(sum(row["passed"] for row in rows) / len(rows), 4),
        }
    latencies = [row["turn_latency_ms"] for row in results]
    model_latencies = [row["model_latency_ms"] for row in results]
    summary = {
        "model": model_name,
        "repetitions": repetition_count,
        "turns": turns,
        "passed": passed,
        "failed": turns - passed,
        "pass_rate": round(passed / turns, 4) if turns else 0.0,
        "critical_turns": len(critical),
        "critical_failures": sum(not row["passed"] for row in critical),
        "model_calls": sum(row["model_calls"] for row in results),
        "model_errors": sum(row["model_errors"] for row in results),
        "total_tokens": sum(row["total_tokens"] for row in results),
        "estimated_cost": round(sum(row["estimated_cost"] for row in results), 6),
        "turn_latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "model_latency_ms": {
            "mean": round(statistics.fmean(model_latencies), 2) if model_latencies else 0.0,
            "p95": _percentile(model_latencies, 0.95),
        },
        "categories": categories,
    }
    if repetition_count > 1:
        logical_turns: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for row in results:
            logical_turns[(row["case_id"], row["turn"])].append(row)
        stable_passed = sum(all(row["passed"] for row in rows) for rows in logical_turns.values())
        consistently_failed = sum(not any(row["passed"] for row in rows) for rows in logical_turns.values())
        unstable = sum(
            any(row["passed"] for row in rows) and not all(row["passed"] for row in rows)
            for rows in logical_turns.values()
        )
        summary["stability"] = {
            "logical_turns": len(logical_turns),
            "passed_every_run": stable_passed,
            "consistently_failed": consistently_failed,
            "unstable": unstable,
            "stable_pass_rate": round(stable_passed / len(logical_turns), 4),
        }
    return summary


def _group_by(rows: list[dict], field: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        out[str(row[field])].append(row)
    return out


def evaluate(
    dataset: dict, llm: InstrumentedLLMClient, *, repeat: int = 1,
    categories: set[str] | None = None, case_filter: str | None = None,
) -> tuple[dict, list[dict]]:
    """执行数据集。每次 repetition 使用全新数据库，但复用同一个模型客户端。"""
    results: list[dict] = []
    selected = [case for case in dataset["cases"]
                if (not categories or case["category"] in categories)
                and (not case_filter or case_filter in case["case_id"])]
    if not selected:
        raise ValueError("no cases selected")

    for repetition in range(1, repeat + 1):
        SessionFactory = _session_factory()
        with SessionFactory() as db:
            users = {
                "main": User(username=f"eval-main-r{repetition}"),
                "other": User(username=f"eval-other-r{repetition}"),
            }
            db.add_all(users.values())
            db.flush()
            _seed_knowledge(db)
            db.commit()

            for case in selected:
                orders = {
                    spec["alias"]: _make_order(db, users, case["case_id"], spec, repetition)
                    for spec in case.get("orders", [])
                }
                db.commit()
                ticket_id: int | None = None
                client_ids: dict[str, str] = {}
                for turn_index, turn in enumerate(case["turns"], 1):
                    _apply_before(db, ticket_id, orders, turn.get("before", {}))
                    db.commit()
                    message = _render_message(turn["message"], orders)
                    attach = turn.get("attach_order")
                    order_id = orders[attach].id if attach else None
                    key_alias = turn.get("client_message_key")
                    client_message_id = None
                    if key_alias:
                        client_message_id = client_ids.setdefault(
                            key_alias, f"eval-r{repetition}-{case['case_id']}-{key_alias}")

                    before_calls, before_errors, before_model_ms = llm.snapshot()
                    started = time.perf_counter()
                    access_denied = False
                    deduplicated = False
                    view = None
                    sess = None
                    try:
                        sess, deduplicated = chat_service.accept_message(db, ChatMessageIn(
                            user_id=users["main"].id,
                            content=message,
                            order_id=order_id,
                            ticket_id=ticket_id,
                            client_message_id=client_message_id,
                        ))
                        run_agent_session(db, sess.id, agent=build_default_agent(llm))
                        view = chat_service.get_session_view(db, sess.id)
                        ticket_id = sess.ticket_id
                    except ResourceAccessDenied:
                        db.rollback()
                        access_denied = True

                    elapsed_ms = (time.perf_counter() - started) * 1000
                    after_calls, after_errors, after_model_ms = llm.snapshot()
                    tool_calls: list[AgentToolCall] = []
                    if sess is not None:
                        tool_calls = list(db.scalars(select(AgentToolCall).where(
                            AgentToolCall.session_id == sess.id).order_by(AgentToolCall.id)).all())
                    refund_count = 0
                    if orders:
                        order_ids = [order.id for order in orders.values()]
                        refund_count = db.scalar(select(func.count()).select_from(RefundRequest).where(
                            RefundRequest.order_id.in_(order_ids))) or 0

                    failures, actual = _assert_turn(
                        turn["expected"], view=view, tool_calls=tool_calls,
                        refund_count=refund_count, access_denied=access_denied,
                        deduplicated=deduplicated,
                    )
                    token_usage = None if deduplicated else getattr(view, "token_usage", None)
                    results.append({
                        "repetition": repetition,
                        "case_id": case["case_id"],
                        "turn": turn_index,
                        "category": case["category"],
                        "severity": case.get("severity", "normal"),
                        "tags": case.get("tags", []),
                        "message": message,
                        "passed": not failures,
                        "failures": failures,
                        "expected": turn["expected"],
                        "actual": actual,
                        "model_calls": after_calls - before_calls,
                        "model_errors": after_errors - before_errors,
                        "model_latency_ms": round(after_model_ms - before_model_ms, 2),
                        "turn_latency_ms": round(elapsed_ms, 2),
                        "total_tokens": token_usage.total_tokens if token_usage else 0,
                        "estimated_cost": float(token_usage.estimated_cost) if token_usage else 0.0,
                    })

    return _summarize(results, llm.model_name, repeat), results


def _check_policy_assumptions(dataset: dict) -> list[str]:
    expected = dataset.get("policy_assumptions", {})
    settings = get_settings()
    actual = {
        "refund_high_amount_threshold": settings.refund_high_amount_threshold,
        "refund_return_window_days": settings.refund_return_window_days,
        "logistics_stale_hours": settings.logistics_stale_hours,
    }
    return [f"{key}: dataset={value}, runtime={actual.get(key)}"
            for key, value in expected.items() if actual.get(key) != value]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SupportFlow comprehensive scenario evaluation")
    parser.add_argument("--dataset", type=Path, default=_DATASET)
    parser.add_argument("--real", action="store_true", help="使用 .env 中配置的真实模型")
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--category", action="append", help="只运行指定 category，可重复提供")
    parser.add_argument("--case-filter", help="只运行 case_id 中包含该文本的场景")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-policy-drift", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.repeat < 1:
        print("--repeat must be >= 1")
        return 2
    if args.real and not args.confirm_paid_run:
        print("拒绝执行：真实模型评测可能产生费用，请添加 --confirm-paid-run。")
        return 2

    dataset = load_dataset(args.dataset)
    turns = sum(len(case["turns"]) for case in dataset["cases"])
    category_counts = Counter(case["category"] for case in dataset["cases"])
    if args.validate_only:
        print(json.dumps({
            "valid": True,
            "dataset": dataset.get("dataset_name"),
            "cases": len(dataset["cases"]),
            "turns": turns,
            "categories": category_counts,
        }, ensure_ascii=False, indent=2))
        return 0

    drift = _check_policy_assumptions(dataset)
    if drift and not args.allow_policy_drift:
        print("拒绝执行：运行时业务阈值与数据集标签不一致：")
        for item in drift:
            print(f"  - {item}")
        print("如确需验证自定义策略，请添加 --allow-policy-drift 并重新审核边界标签。")
        return 2

    if args.real:
        from app.llm.registry import build_llm_client
        base_llm = build_llm_client()
    else:
        from app.llm.stub import StubLLMClient
        base_llm = StubLLMClient()
    llm = InstrumentedLLMClient(base_llm)
    summary, results = evaluate(
        dataset, llm, repeat=args.repeat,
        categories=set(args.category or []) or None,
        case_filter=args.case_filter,
    )

    print("\n=== Comprehensive Scenario Evaluation ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failures = [row for row in results if not row["passed"]]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for row in failures[:30]:
            print(f"- r{row['repetition']} {row['case_id']}#t{row['turn']}: "
                  + "; ".join(row["failures"]))
        if len(failures) > 30:
            print(f"  ... and {len(failures) - 30} more")

    if args.output:
        report = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "dataset": str(args.dataset),
            "mode": "real" if args.real else "stub",
            "policy_assumptions": dataset.get("policy_assumptions", {}),
            "summary": summary,
            "results": results,
            "honest_boundaries": [
                "This evaluator uses an isolated in-memory database and bypasses HTTP/Celery.",
                "Real model quality varies by provider version and sampling behavior.",
                "Deterministic business decisions can pass even when optional model tool calls fail; tool assertions are reported separately.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nreport: {args.output}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
