"""综合场景评测器自身的回归测试；不调用真实模型。"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.eval.run_scenario_eval import (
    InstrumentedLLMClient,
    evaluate,
    load_dataset,
    validate_dataset,
)
from app.llm.stub import StubLLMClient


def test_real_scenario_dataset_is_comprehensive_and_valid():
    dataset = load_dataset()
    cases = dataset["cases"]
    turns = sum(len(case["turns"]) for case in cases)
    categories = {case["category"] for case in cases}

    assert len(cases) >= 50
    assert turns >= 60
    assert categories == {
        "semantic_understanding",
        "multi_turn_workflow",
        "business_boundaries",
        "security_and_guardrails",
        "anomaly_and_recovery",
        "multi_intent",
    }
    assert sum(case.get("severity") == "critical" for case in cases) >= 20


def test_dataset_validator_rejects_duplicate_case_ids():
    dataset = load_dataset()
    broken = deepcopy(dataset)
    broken["cases"].append(deepcopy(broken["cases"][0]))

    with pytest.raises(ValueError, match="duplicate case_id"):
        validate_dataset(broken)


def test_scenario_runner_checks_multiturn_refund_side_effects_with_stub():
    dataset = load_dataset()
    llm = InstrumentedLLMClient(StubLLMClient())

    summary, results = evaluate(
        dataset, llm, case_filter="workflow-refund-confirm",
    )

    assert summary["turns"] == 2
    assert summary["failed"] == 0
    assert summary["critical_failures"] == 0
    assert results[0]["actual"]["refund_count"] == 0
    assert results[1]["actual"]["refund_count"] == 1
    assert results[1]["actual"]["state"] == "resolved_by_agent"
