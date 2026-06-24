"""P1 对话记忆 + P4 长对话摘要：runner 灌历史进 ctx.history；超 token 预算的旧段摘要压缩。"""
from app.agent.context import Decision
from app.agent.memory import apply_token_budget, estimate_tokens, summarize_history
from app.db.models import AgentSession, Ticket, TicketMessage, User
from app.llm.base import LLMResponse, Msg
from app.workers.runner import _load_history, run_agent_session


def _mk(db, sender, content, user_id=None):
    db.add(TicketMessage(ticket_id=db._tid, user_id=user_id,
                         sender_type=sender, content=content))
    db.flush()


def _conversation(db):
    u = User(username="bob")
    db.add(u)
    db.flush()
    t = Ticket(user_id=u.id, category="chat")
    db.add(t)
    db.flush()
    db._tid = t.id
    return u, t


def test_load_history_maps_roles_and_excludes_current(db):
    u, t = _conversation(db)
    _mk(db, "user", "我买了一件衣服", u.id)
    _mk(db, "agent", "好的，已记录")
    _mk(db, "human", "人工补充")
    cur = TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user", content="当前消息")
    db.add(cur)
    db.flush()

    hist = _load_history(db, t.id, cur.id)
    assert [m.content for m in hist] == ["我买了一件衣服", "好的，已记录", "人工补充"]
    assert [m.role for m in hist] == ["user", "assistant", "assistant"]  # 当前消息被排除


def test_load_history_within_budget_keeps_all(db):
    u, t = _conversation(db)
    _mk(db, "user", "a", u.id)
    _mk(db, "agent", "b")
    cur = TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user", content="cur")
    db.add(cur)
    db.flush()
    hist = _load_history(db, t.id, cur.id, token_budget=1000)   # 远超 → 不摘要
    assert [m.content for m in hist] == ["a", "b"]
    assert not hist[0].content.startswith("[早期对话摘要]")


def test_load_history_summarizes_old_when_over_budget(db):
    u, t = _conversation(db)
    for i in range(10):
        _mk(db, "user", f"message-{i}", u.id)   # 每条 9 字符
    cur = TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user", content="cur")
    db.add(cur)
    db.flush()
    # 预算 20 字符 → 只够最近 2 条(message-8/9)，更早 8 条摘成一条前置消息(llm=None 走模板)
    hist = _load_history(db, t.id, cur.id, token_budget=20)
    assert hist[0].role == "user" and hist[0].content.startswith("[早期对话摘要]")
    assert "早期对话" in hist[0].content
    assert hist[-1].content == "message-9"          # 最新一条一定原样保留
    assert "message-0" not in [m.content for m in hist[1:]]  # 最早的被折叠进摘要


class _CapturingAgent:
    """假 agent：捕获 runner 传入的 ctx.history / ctx.message。"""
    def __init__(self):
        self.history = None
        self.message = None

    def handle(self, ctx, tool_ctx=None):
        self.history = list(ctx.history)
        self.message = ctx.message
        ctx.intent, ctx.skill, ctx.state = "order_query", "general", "resolved_by_agent"
        return Decision(reply="收到", next_state="resolved_by_agent")


def test_run_agent_session_feeds_prior_turns(db):
    """第二轮处理时，Agent 能看到第一轮的对话（这就是"它记得你说过买了衣服"）。"""
    u, t = _conversation(db)
    _mk(db, "user", "我买了一件衣服", u.id)
    _mk(db, "agent", "好的")
    _mk(db, "user", "我买了什么", u.id)              # 当前轮的最新用户消息
    sess = AgentSession(user_id=u.id, ticket_id=t.id, task_status="queued")
    db.add(sess)
    db.commit()

    agent = _CapturingAgent()
    run_agent_session(db, sess.id, agent=agent)

    assert agent.message == "我买了什么"
    assert [m.content for m in agent.history] == ["我买了一件衣服", "好的"]
    assert [m.role for m in agent.history] == ["user", "assistant"]


# ---- memory 模块单元 ----

class _FakeLLM:
    model_name = "fake"

    def chat(self, *, system, messages, tools=None, stream=False):
        return LLMResponse(text="用户要退款订单X，已建草稿")


def test_apply_token_budget_keeps_newest_even_if_over():
    msgs = [Msg("user", "x" * 100)]          # 单条就超预算
    out = apply_token_budget(msgs, token_budget=10)
    assert out == msgs                        # 至少保留最新 1 条，不丢当前上下文


def test_summarize_history_uses_llm_when_available():
    msgs = [Msg("user", "我要退款"), Msg("assistant", "好的")]
    assert summarize_history(msgs, llm=_FakeLLM()) == "用户要退款订单X，已建草稿"


def test_summarize_history_template_fallback_without_llm():
    out = summarize_history([Msg("user", "你好")], llm=None)
    assert "早期对话" in out                   # 无 LLM → 确定性模板，CI 可复现


def test_estimate_tokens_conservative():
    assert estimate_tokens("abcd") == 4 and estimate_tokens("") == 1
