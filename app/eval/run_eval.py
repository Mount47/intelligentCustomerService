"""评测跑分（§16）。默认 stub（可复现/零成本/CI 友好），可 --real 用 .env 真实 provider。

诚实边界（写进 README）：当前评测验证 **规则意图识别 / Skill 路由 / 工具调用 / 状态机流转 /
人工升级策略 / guardrails 合规** 的正确性与覆盖度；LLM 语义泛化属后续迭代。

用法：
  python -m app.eval.run_eval            # stub
  python -m app.eval.run_eval --real     # 用 .env 的 LLM_PROVIDER（如 qwen）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.agent.guardrails import FORBIDDEN_PHRASES
from app.core.exceptions import ResourceAccessDenied
from app.db.models import AgentToolCall, Base, Logistics, Order, RefundRequest, User
from app.eval.judge import build_judge
from app.eval.metrics import summarize
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session

_CASES = Path(__file__).parent / "test_cases.json"


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_order(db, main_uid: int, other_uid: int, spec: dict, case_id: str) -> int:
    uid = other_uid if spec.get("other_owner") else main_uid
    now = datetime.utcnow()
    o = Order(user_id=uid, order_no=f"EVAL-{case_id}", status=spec.get("status", "paid"),
              total_amount=spec.get("amount", 100),
              product_type=spec.get("product_type", "normal"), paid_at=now)
    dd = spec.get("delivered_days_ago")
    if dd is not None:
        o.delivered_at = now - timedelta(days=dd)
        o.status = "delivered"
    db.add(o)
    db.flush()
    lg = spec.get("logistics")
    if lg:
        db.add(Logistics(order_id=o.id, status=lg.get("status", "in_transit"), carrier="顺丰",
                         last_update_time=now - timedelta(hours=lg.get("hours_ago", 2)),
                         last_location="转运中心", is_exception=lg.get("is_exception", False),
                         exception_reason=lg.get("reason")))
        db.flush()
    return o.id


def evaluate(real: bool = False, judge_real: bool = False) -> tuple[dict, list[dict]]:
    """real：Agent 用真实模型；judge_real：第二层 LLM-Judge 用真实模型打分。

    默认双 stub（不调 API、确定性、CI 友好）。judge 始终运行：stub 据断言信号折算质量分，
    real 让裁判读回复正文做语义打分（盲评，prompt 不含生产模型身份）。
    """
    cases = json.loads(_CASES.read_text(encoding="utf-8"))
    Session = _session_factory()

    if real:
        from app.llm.registry import build_llm_client
        agent = build_default_agent(build_llm_client())
    else:
        agent = build_default_agent()  # stub

    judge = build_judge(real=judge_real)
    results: list[dict] = []
    with Session() as db:
        main = User(username="evald")
        other = User(username="other")
        db.add_all([main, other])
        db.flush()

        for c in cases:
            order_id = None
            if c.get("order") is not None:
                order_id = _make_order(db, main.id, other.id, c["order"], c["case_id"])
            # 单轮 case 视作 1 轮；多轮 case 用 turns 列表，同一 ticket 顺序跑（验确认流/去重/续接）
            turns = c.get("turns") or [c]
            ticket_id = None
            for ti, turn in enumerate(turns):
                exp = {**c, **turn}   # turn 字段覆盖 case 级默认
                tag = c["case_id"] if len(turns) == 1 else f"{c['case_id']}#t{ti + 1}"
                r, ticket_id = _eval_turn(db, agent, judge, exp, tag, main.id,
                                          order_id, ticket_id)
                results.append(r)

    return summarize(results), results


def _eval_turn(db, agent, judge, exp: dict, tag: str, user_id: int,
               order_id: int | None, ticket_id: int | None):
    """跑一轮对话并产出一行结果。期望字段缺省即不断言（多轮里某些轮只关心 state）。"""
    try:
        sess, _ = chat_service.accept_message(db, ChatMessageIn(
            user_id=user_id, content=exp["user_message"], order_id=order_id, ticket_id=ticket_id))
    except ResourceAccessDenied:
        # IDOR 在接入层提前拒绝比“进入 Agent 后转人工”更安全。评测把它作为独立终态，
        # 不伪造 session/intent/tool 调用，同时保留统一指标结构。
        expected = bool(exp.get("expected_access_denied"))
        reply = "请求的订单或工单无法通过归属校验。"
        r = {
            "case_id": tag, "adversarial": bool(exp.get("adversarial")),
            "should_handoff": exp.get("should_handoff", False),
            "intent_ok": expected, "skill_ok": expected, "state_ok": expected,
            "handoff_ok": expected and not exp.get("should_handoff", False),
            "count_ok": True, "forbidden_hits": [], "tool_calls_total": 0,
            "tool_calls_ok": 0, "total_tokens": 0, "cost": 0.0,
            "final_status": "access_denied",
            "predicted": {"intent": None, "skill": None, "state": "access_denied"},
            "expected": {"intent": exp.get("expected_intent"), "skill": exp.get("expected_skill"),
                         "state": exp.get("expected_final_status")},
        }
        r["judge"] = judge.score(exp, reply, r)
        return r, ticket_id
    run_agent_session(db, sess.id, agent=agent)
    view = chat_service.get_session_view(db, sess.id)

    reply = view.latest_reply or ""
    forbidden = list(exp.get("forbidden_phrases", [])) + list(FORBIDDEN_PHRASES)
    hits = [p for p in forbidden if p in reply]
    pred_handoff = view.final_status == "need_human"
    calls_total = db.scalar(select(func.count()).select_from(AgentToolCall)
                            .where(AgentToolCall.session_id == sess.id)) or 0
    calls_ok = db.scalar(select(func.count()).select_from(AgentToolCall)
                         .where(AgentToolCall.session_id == sess.id,
                                AgentToolCall.success.is_(True))) or 0
    # 期望缺省 → 该项不参与判定（视为通过），让多轮里只关心状态的轮不被强判意图/技能
    accept = exp.get("accept_intents") or ([exp["expected_intent"]] if "expected_intent" in exp else None)
    # 会话级去重硬验：跑完本轮后【本订单】的退款单数应等于期望（防同订单重复建草稿）。
    # 按 order_id 过滤而非 user_id——评测共用一个 main 用户跨 case，按用户数会串。
    refund_count = db.scalar(select(func.count()).select_from(RefundRequest)
                             .where(RefundRequest.order_id == order_id)) or 0
    count_ok = ("expected_refund_count" not in exp) or (refund_count == exp["expected_refund_count"])

    r = {
        "case_id": tag,
        "adversarial": bool(exp.get("adversarial")),
        "should_handoff": exp.get("should_handoff", False),
        "intent_ok": accept is None or view.current_intent in accept,
        "skill_ok": "expected_skill" not in exp or view.current_skill == exp["expected_skill"],
        "state_ok": "expected_final_status" not in exp or view.final_status == exp["expected_final_status"],
        "handoff_ok": pred_handoff == exp.get("should_handoff", False),
        "count_ok": count_ok,
        "forbidden_hits": hits,
        "tool_calls_total": calls_total,
        "tool_calls_ok": calls_ok,
        "total_tokens": view.token_usage.total_tokens,
        "cost": view.token_usage.estimated_cost,
        "final_status": view.final_status,
        "predicted": {"intent": view.current_intent, "skill": view.current_skill,
                      "state": view.final_status},
        "expected": {"intent": exp.get("expected_intent"), "skill": exp.get("expected_skill"),
                     "state": exp.get("expected_final_status")},
    }
    r["judge"] = judge.score(exp, reply, r)
    return r, sess.ticket_id


def main() -> int:
    real = "--real" in sys.argv
    judge_real = "--judge-real" in sys.argv      # 第二层裁判用真实模型（会调 API）
    summary, results = evaluate(real=real, judge_real=judge_real)

    fails = [r for r in results
             if not (r["intent_ok"] and r["skill_ok"] and r["state_ok"]
                     and r["handoff_ok"] and r.get("count_ok", True) and not r["forbidden_hits"])]
    jt = "REAL" if judge_real else "STUB"
    print(f"\n=== 评测 (agent={'REAL' if real else 'STUB'} / judge={jt}) — {summary['cases']} cases ===")
    for k, v in summary.items():
        if k != "cases":
            print(f"  {k:32s}: {v}")
    if fails:
        print(f"\n  FAILS ({len(fails)}):")
        for r in fails:
            print(f"   - {r['case_id']}: expected={r['expected']} got={r['predicted']}"
                  f" handoff_ok={r['handoff_ok']} forbidden={r['forbidden_hits']}")
    else:
        print("\n  ALL PASS ✅")

    # 质量分最低的几条（裁判视角，定位"流程对但答得不好"的 case）
    low = sorted(results, key=lambda r: r["judge"]["overall"])[:5]
    print("\n  质量分最低 5 条（judge）：")
    for r in low:
        j = r["judge"]
        print(f"   - {r['case_id']}: overall={j['overall']} "
              f"acc={j['accuracy']} help={j['helpfulness']} comp={j['compliance']} tone={j['tone']}"
              f"  {j.get('reason','')[:40]}")
    return 0


if __name__ == "__main__":
    from app.core.logging import setup_logging
    setup_logging("WARNING")
    raise SystemExit(main())
