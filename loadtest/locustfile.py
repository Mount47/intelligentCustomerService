"""接入层压测（§17、ADR-12）。压 POST /api/chat/message —— 落库+入队的削峰能力，
不是 LLM 吞吐。worker 用 LLM_PROVIDER=stub（可配 STUB_DELAY_MS）异步消费。

跑法（先 docker compose 起全栈 + seed）：
  locust -f loadtest/locustfile.py --host http://localhost:8000
  # 无头三档：
  locust -f loadtest/locustfile.py --host http://localhost:8000 \
         --headless -u 100 -r 20 -t 60s
  # 同时观察削峰：watch -n1 'curl -s localhost:8000/api/admin/metrics'

判定：入队成功率>99%、错误率<1%、P95<300ms（接入层；绝对值依赖机器）。
"""
import os
import random

from locust import HttpUser, between, task

MESSAGES = [
    "我要退款",
    "我的快递到哪了",
    "你们的售后政策是怎样的",
    "我要开发票",
    "这个商品有质量问题我要投诉",
    "我要转人工",
]


class ChatUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        index = random.randint(1, int(os.getenv("LOADTEST_USER_COUNT", "10")))
        username = f"user{index:02d}"
        password = os.getenv("LOADTEST_PASSWORD", "supportflow-user")
        response = self.client.post(
            "/api/auth/token",
            json={"username": username, "password": password},
            name="POST /auth/token",
        )
        if response.status_code != 200:
            raise RuntimeError(f"loadtest login failed: {response.status_code} {response.text}")
        self.token = response.json()["accessToken"]

    @task(5)
    def send_message(self) -> None:
        payload = {
            "content": random.choice(MESSAGES),
            "clientMessageId": f"load-{random.getrandbits(96):024x}",
        }
        with self.client.post("/api/chat/message", json=payload,
                              headers={"Authorization": f"Bearer {self.token}"},
                              catch_response=True, name="POST /chat/message") as r:
            if r.status_code == 200 and r.json().get("sessionId") \
                    and r.json().get("taskStatus") == "queued":
                r.success()                      # 入队成功
            else:
                r.failure(f"enqueue failed: {r.status_code} {r.text[:120]}")

    @task(1)
    def health(self) -> None:
        # 轻量读压，顺带观察接入层在写负载下的健康
        self.client.get("/health", name="GET /health")
