"""评测指标汇总（§16、§23.3）。分流程类 / 质量类 / 成本类。"""
from __future__ import annotations


def _rate(items: list, pred) -> float:
    items = list(items)
    return round(sum(1 for x in items if pred(x)) / len(items), 3) if items else 1.0


def _avg(items: list, key) -> float:
    items = list(items)
    return round(sum(key(x) for x in items) / len(items), 2) if items else 0.0


def summarize(results: list[dict]) -> dict:
    n = len(results)
    total_calls = sum(r["tool_calls_total"] for r in results)
    ok_calls = sum(r["tool_calls_ok"] for r in results)
    adv = [r for r in results if r["adversarial"]]
    adv_handoff = [r for r in adv if r["should_handoff"]]
    out = {
        "cases": n,
        # 流程类
        "intent_accuracy": _rate(results, lambda r: r["intent_ok"]),
        "skill_routing_accuracy": _rate(results, lambda r: r["skill_ok"]),
        "state_transition_accuracy": _rate(results, lambda r: r["state_ok"]),
        "handoff_accuracy": _rate(results, lambda r: r["handoff_ok"]),
        "tool_call_success_rate": round(ok_calls / total_calls, 3) if total_calls else 1.0,
        # 质量类（断言派生）
        "policy_compliance_rate": _rate(results, lambda r: not r["forbidden_hits"]),
        "forbidden_phrase_block_rate": _rate(adv, lambda r: not r["forbidden_hits"]),
        "high_risk_handoff_accuracy": _rate(adv_handoff, lambda r: r["handoff_ok"]),
        # 注：resolution_rate 是代理指标——"该转人工/该追问"的 case 本就不 resolved，
        # 故偏低不等于质量差；真正的质量看下面 LLM-Judge 的语义打分。
        "resolution_rate": _rate(results, lambda r: r["final_status"] == "resolved_by_agent"),
        # 成本类
        "average_tokens_per_session": round(sum(r["total_tokens"] for r in results) / n, 1) if n else 0,
        "average_cost_per_session": round(sum(r["cost"] for r in results) / n, 6) if n else 0,
    }
    # 质量类（LLM-Judge 语义打分，第二层）。仅当结果带 judge 字段时汇总。
    judged = [r for r in results if r.get("judge")]
    if judged:
        out["judge_overall"] = _avg(judged, lambda r: r["judge"]["overall"])
        out["judge_accuracy"] = _avg(judged, lambda r: r["judge"]["accuracy"])
        out["judge_helpfulness"] = _avg(judged, lambda r: r["judge"]["helpfulness"])
        out["judge_compliance"] = _avg(judged, lambda r: r["judge"]["compliance"])
        out["judge_tone"] = _avg(judged, lambda r: r["judge"]["tone"])
        # 裁判输出解析失败率（监控裁判自身健康度）
        out["judge_parse_fail_rate"] = _rate(judged, lambda r: not r["judge"].get("parse_ok", True))
    return out
