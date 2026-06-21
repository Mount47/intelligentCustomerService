"""P1 对话记忆：runner 把本工单历史灌进 ctx.history，LLM 跨轮可见上下文。"""
from app.agent.context import Decision
from app.db.models import AgentSession, Ticket, TicketMessage, User
from app.workers.runner import _HISTORY_MAX_MSGS, _load_history, run_agent_session


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


def test_load_history_windowed(db):
    u, t = _conversation(db)
    for i in range(_HISTORY_MAX_MSGS + 5):
        _mk(db, "user", f"m{i}", u.id)
    cur = TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user", content="cur")
    db.add(cur)
    db.flush()
    hist = _load_history(db, t.id, cur.id)
    assert len(hist) == _HISTORY_MAX_MSGS                 # 只保留最近 N 条
    assert hist[-1].content == f"m{_HISTORY_MAX_MSGS + 4}"  # 截掉最早的


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
