"""发件箱可靠投递验收：broker 宕机期间的消息，恢复后仍被处理。

前置：PostgreSQL + Redis + Celery worker 已起，API 已起且其 broker 指向**不可达**地址
（模拟"提交成功但入队失败"）。本脚本自身用正确的 broker 跑一次 relay 补投。

证明链：
  1. broker 不可达时 POST /api/chat/message 仍返回 200（用户请求不被连坐）；
  2. 会话停在 queued，发件箱留下一条 pending 事件（消息没丢）；
  3. relay 补投后 worker 消费，会话到达终态（补投生效）；
  4. 再跑一次 relay 不会重复处理（幂等）。
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AgentSession, OutboxEvent
from app.db.session import SessionLocal
from app.workers import outbox_relay


def _json_request(url: str, *, method: str = "GET", payload=None,
                  token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, method=method, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="supportflow-user")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    status, body = _json_request(
        f"{args.base_url}/api/auth/token", method="POST",
        payload={"username": args.username, "password": args.password})
    assert status == 200, body
    token = body["accessToken"]

    # 1 broker 不可达时发消息：请求必须成功，用户不该看到 broker 故障
    client_message_id = f"outbox-{uuid.uuid4().hex}"
    status, body = _json_request(
        f"{args.base_url}/api/chat/message", method="POST", token=token,
        payload={"content": "我要退款", "clientMessageId": client_message_id})
    assert status == 200, f"request failed during broker outage: {status} {body}"
    session_id = body["sessionId"]

    # 2 消息没丢：会话仍在排队，发件箱有一条 pending
    db = SessionLocal()
    try:
        dedup_key = f"agent.process_message:{session_id}"
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key))
        assert event is not None, "no outbox event recorded; delivery window still open"
        assert event.status == "pending", f"unexpected event status: {event.status}"
        sess = db.get(AgentSession, session_id)
        assert sess.task_status == "queued", f"unexpected task status: {sess.task_status}"

        # 3 relay 补投（本进程 broker 配置正常）。
        # 事件有一段静默期留给内联快路，这里轮询到本会话的事件确实被投出为止。
        # 断言必须盯住本会话的 dedup_key——"本轮投出了某条事件"不等于"投出了这条"。
        stats = {"claimed": 0, "sent": 0, "failed": 0}
        drained = []
        drain_deadline = time.time() + args.timeout
        while time.time() < drain_deadline:
            stats = outbox_relay.drain_once(db, get_settings())
            drained.append(stats)
            event = db.scalar(select(OutboxEvent).where(
                OutboxEvent.dedup_key == dedup_key))
            if event is not None and event.status == "sent":
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f"relay did not redeliver {dedup_key}: {drained}")
        assert event.attempts >= 1, event.attempts
    finally:
        db.close()

    deadline = time.time() + args.timeout
    terminal = None
    while time.time() < deadline:
        status, view = _json_request(
            f"{args.base_url}/api/chat/session/{session_id}", token=token)
        assert status == 200, view
        if view["taskStatus"] not in ("queued", "processing"):
            terminal = view
            break
        time.sleep(0.25)
    assert terminal is not None, "worker did not consume the redelivered message"
    assert terminal["taskStatus"] in ("final", "waiting_user_input", "need_human"), terminal

    # 4 再补投一次不会重复处理：事件已 sent，relay 不该再取到它
    db = SessionLocal()
    try:
        again = outbox_relay.drain_once(db, get_settings())
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key))
        assert event.status == "sent", event.status
        replies = [m for m in terminal["messages"] if m["sender"] == "agent"]
        assert len(replies) == 1, f"redelivery produced duplicate replies: {len(replies)}"
    finally:
        db.close()

    print(json.dumps({
        "request_survived_broker_outage": "passed",
        "event_persisted_while_queue_down": "passed",
        "relay_redelivered": {"dedup_key": dedup_key, "attempts": event.attempts},
        "terminal_status": terminal["taskStatus"],
        "agent_replies": len(replies),
        "second_drain_did_not_resend": again["sent"] == 0,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
