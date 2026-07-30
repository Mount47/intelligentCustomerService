"""真实 PostgreSQL + Redis + Celery worker 验收。

前置：docker compose 全栈已启动并执行 seed_data。
验证：登录/RBAC、Redis 固定窗口真实 INCR、API 入队、Celery worker 消费、trace header 保持。
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import redis

from app.core.ratelimit import allow_request


def _json_request(url: str, *, method: str = "GET", payload=None, token: str | None = None,
                  trace_id: str | None = None) -> tuple[int, dict, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if trace_id:
        headers["X-Trace-ID"] = trace_id
    request = Request(
        url,
        method=method,
        headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urlopen(request, timeout=10) as response:
            headers_out = {key.lower(): value for key, value in response.headers.items()}
            return response.status, json.loads(response.read() or b"{}"), headers_out
    except HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        headers_out = {key.lower(): value for key, value in exc.headers.items()}
        return exc.code, body, headers_out


def _login(base: str, username: str, password: str) -> str:
    status, body, _ = _json_request(
        f"{base}/api/auth/token", method="POST",
        payload={"username": username, "password": password})
    assert status == 200, body
    return body["accessToken"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--worker-log",
        help="可选：等待并断言 worker 日志中出现 API 传入的 trace_id",
    )
    args = parser.parse_args()

    user_token = _login(args.base_url, "demo", "supportflow-user")
    admin_token = _login(args.base_url, "admin", "supportflow-admin")

    status, _, _ = _json_request(f"{args.base_url}/api/admin/metrics", token=user_token)
    assert status == 403, f"user unexpectedly accessed admin metrics: {status}"
    status, _, _ = _json_request(f"{args.base_url}/api/admin/metrics", token=admin_token)
    assert status == 200

    redis_client = redis.Redis.from_url(args.redis_url, decode_responses=True)
    bucket = f"e2e:{uuid.uuid4().hex}"
    decisions = [allow_request(bucket, 2, window_sec=30, client=redis_client)[0]
                 for _ in range(3)]
    assert decisions == [True, True, False], decisions

    trace_id = f"e2e-{uuid.uuid4().hex[:16]}"
    client_message_id = f"e2e-{uuid.uuid4().hex}"
    status, body, headers = _json_request(
        f"{args.base_url}/api/chat/message",
        method="POST",
        token=user_token,
        trace_id=trace_id,
        payload={
            "content": "帮我查一下我的订单",
            "clientMessageId": client_message_id,
        },
    )
    assert status == 200, body
    assert headers.get("x-trace-id") == trace_id
    session_id = body["sessionId"]

    deadline = time.time() + args.timeout
    terminal = None
    while time.time() < deadline:
        status, view, _ = _json_request(
            f"{args.base_url}/api/chat/session/{session_id}", token=user_token)
        assert status == 200, view
        if view["taskStatus"] not in ("queued", "processing"):
            terminal = view
            break
        time.sleep(0.25)
    assert terminal is not None, "Celery worker did not consume the queued session"
    assert terminal["taskStatus"] in ("final", "waiting_user_input", "need_human"), terminal

    worker_trace = "not_checked"
    if args.worker_log:
        log_path = Path(args.worker_log)
        trace_deadline = time.time() + 5
        while time.time() < trace_deadline:
            if log_path.exists() and trace_id in log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                worker_trace = "passed"
                break
            time.sleep(0.1)
        assert worker_trace == "passed", (
            f"API trace_id {trace_id!r} was not found in worker log {log_path}"
        )

    print(json.dumps({
        "auth_rbac": "passed",
        "redis_fixed_window": decisions,
        "session_id": session_id,
        "celery_terminal_status": terminal["taskStatus"],
        "trace_id": trace_id,
        "worker_trace": worker_trace,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
