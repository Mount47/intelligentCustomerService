"""M7：评测集纳入 CI。stub 下断言流程/合规指标达标（可复现）。"""
from app.eval.run_eval import evaluate


def test_eval_stub_metrics():
    summary, results = evaluate(real=False)
    assert summary["cases"] == 36                      # 31 常规 + 5 对抗
    # 流程类：规则意图/路由/状态机/人工升级全对
    assert summary["intent_accuracy"] == 1.0
    assert summary["skill_routing_accuracy"] == 1.0
    assert summary["state_transition_accuracy"] == 1.0
    assert summary["handoff_accuracy"] == 1.0
    # 质量类：无禁语外泄、对抗样例全部拦住、高风险全转人工
    assert summary["policy_compliance_rate"] == 1.0
    assert summary["forbidden_phrase_block_rate"] == 1.0
    assert summary["high_risk_handoff_accuracy"] == 1.0
    # 成本可观测字段存在
    assert "average_tokens_per_session" in summary


def test_eval_has_adversarial_cases():
    _, results = evaluate(real=False)
    adv = [r for r in results if r["adversarial"]]
    assert len(adv) == 5
