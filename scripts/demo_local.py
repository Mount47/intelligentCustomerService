"""本地一键体验 —— 无需 docker / redis / celery / 服务器。

直接用 sqlite + 跑 Agent，打印每条消息的「回复 / 思考步骤 / token」。

用法：
  # 1) 桩模型（零依赖，验证业务链路）
  DATABASE_URL=sqlite:///./_demo.db LLM_PROVIDER=stub python -m scripts.demo_local

  # 2) 接真实模型（DeepSeek 示例，OpenAI 兼容，本地友好）
  DATABASE_URL=sqlite:///./_demo.db \
  LLM_PROVIDER=deepseek LLM_MODEL=deepseek-chat \
  OPENAI_API_KEY=sk-xxx OPENAI_BASE_URL=https://api.deepseek.com/v1 \
  python -m scripts.demo_local

  # 3) 接 Claude
  DATABASE_URL=sqlite:///./_demo.db LLM_PROVIDER=claude LLM_MODEL=claude-opus-4-8 \
  ANTHROPIC_API_KEY=sk-ant-xxx python -m scripts.demo_local

注：退款/物流的业务决策由 Skill.finalize 确定性执行，桩模型下也能跑出真实业务行为；
真实模型还会在「思考步骤」里出现工具调用（查订单/查政策…）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.agent.agent_core import build_default_agent
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.init_db import init_db
from app.db.models import Logistics, Order, User
from app.db.session import SessionLocal
from app.llm.registry import build_llm_client
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session


def _seed(db) -> dict:
    u = User(username="demo_user", phone="13800000000")
    db.add(u)
    db.flush()
    now = datetime.utcnow()
    normal = Order(user_id=u.id, order_no="DEMO-NORMAL", status="paid",
                   total_amount=120, product_type="normal", paid_at=now)
    big = Order(user_id=u.id, order_no="DEMO-BIG", status="paid",
                total_amount=999, product_type="normal", paid_at=now)
    shipped = Order(user_id=u.id, order_no="DEMO-SHIP", status="shipped",
                    total_amount=80, product_type="normal", paid_at=now,
                    shipped_at=now - timedelta(days=3))
    db.add_all([normal, big, shipped])
    db.flush()
    db.add(Logistics(order_id=shipped.id, status="in_transit", carrier="顺丰",
                     last_update_time=now - timedelta(hours=60),  # 超48h → 异常
                     last_location="上海转运中心"))
    db.commit()
    return {"user": u.id, "normal": normal.id, "big": big.id, "shipped": shipped.id}


def _run_one(db, agent, user_id: int, content: str, order_id: int | None) -> None:
    sess, _ = chat_service.accept_message(
        db, ChatMessageIn(user_id=user_id, content=content, order_id=order_id))
    run_agent_session(db, sess.id, agent=agent)
    view = chat_service.get_session_view(db, sess.id)
    print("\n" + "=" * 64)
    print(f"用户：{content}  (order_id={order_id})")
    print(f"意图={view.current_intent}  技能={view.current_skill}  "
          f"状态={view.final_status}  task={view.task_status}")
    print("思考过程：", " → ".join(
        f"{s.label}" + (f"({s.detail})" if s.detail else "") for s in view.steps))
    print(f"回复：{view.latest_reply}")
    print(f"token：{view.tokens.model} total={view.tokens.total}")


def main() -> None:
    setup_logging()
    s = get_settings()
    print(f"[demo] provider={s.llm_provider} model={s.llm_model} db={s.database_url}")
    init_db()
    with SessionLocal() as db:
        ids = _seed(db)
        agent = build_default_agent(build_llm_client(s))
        _run_one(db, agent, ids["user"], "我要退款，不想要了", ids["normal"])   # 低风险→自动草稿
        _run_one(db, agent, ids["user"], "这个太贵了我要退款", ids["big"])        # 高风险→转人工
        _run_one(db, agent, ids["user"], "我的快递怎么还没动", ids["shipped"])     # 物流异常→催件
        _run_one(db, agent, ids["user"], "你们的售后政策是怎样的", None)          # 兜底
    print("\n" + "=" * 64 + "\n[demo] done.")


if __name__ == "__main__":
    main()
