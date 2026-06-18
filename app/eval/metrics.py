"""评测指标汇总（§16、§23.3）。分流程类 / 质量类 / 成本类。"""
from __future__ import annotations


def _rate(items: list, pred) -> float:
    items = list(items)
    return round(sum(1 for x in items if pred(x)) / len(items), 3) if items else 1.0


def summarize(results: list[dict]) -> dict:
    n = len(results)
    total_calls = sum(r["tool_calls_total"] for r in results)
    ok_calls = sum(r["tool_calls_ok"] for r in results)
    adv = [r for r in results if r["adversarial"]]
    adv_handoff = [r for r in adv if r["should_handoff"]]
    return {
        "cases": n,
        # 流程类
        "intent_accuracy": _rate(results, lambda r: r["intent_ok"]),
        "skill_routing_accuracy": _rate(results, lambda r: r["skill_ok"]),
        "state_transition_accuracy": _rate(results, lambda r: r["state_ok"]),
        "handoff_accuracy": _rate(results, lambda r: r["handoff_ok"]),
        "tool_call_success_rate": round(ok_calls / total_calls, 3) if total_calls else 1.0,
        # 质量类
        "policy_compliance_rate": _rate(results, lambda r: not r["forbidden_hits"]),
        "forbidden_phrase_block_rate": _rate(adv, lambda r: not r["forbidden_hits"]),
        "high_risk_handoff_accuracy": _rate(adv_handoff, lambda r: r["handoff_ok"]),
        "resolution_rate": _rate(results, lambda r: r["final_status"] == "resolved_by_agent"),
        # 成本类
        "average_tokens_per_session": round(sum(r["total_tokens"] for r in results) / n, 1) if n else 0,
        "average_cost_per_session": round(sum(r["cost"] for r in results) / n, 6) if n else 0,
    }
