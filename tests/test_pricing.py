"""真实成本折算。"""
from app.agent.context import TokenAcct
from app.llm.base import Usage
from app.llm.pricing import estimate_cost


def test_estimate_known_model():
    # claude-opus 输入 5 / 输出 25 (USD/1M)
    assert estimate_cost("claude-opus-4-8", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-opus-4-8", 0, 1_000_000) == 25.0


def test_estimate_cache_discount():
    # 100万输入全是缓存读 → 5 * 0.1 = 0.5
    assert estimate_cost("claude-opus-4-8", 1_000_000, 0, cache_read_tokens=1_000_000) == 0.5


def test_unknown_and_stub_zero():
    assert estimate_cost("stub-llm", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("", 999, 999) == 0.0


def test_token_acct_accumulates_cost():
    acct = TokenAcct()
    acct.add(Usage(prompt_tokens=1_000_000, completion_tokens=0), "qwen-plus")
    # qwen-plus 输入 0.4/1M
    assert acct.estimated_cost == 0.4
    acct.add(Usage(completion_tokens=1_000_000), "qwen-plus")  # 输出 1.2/1M
    assert acct.estimated_cost == round(0.4 + 1.2, 6)
