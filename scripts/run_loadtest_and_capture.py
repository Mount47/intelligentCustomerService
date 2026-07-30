"""运行 Locust，并同步采集管理员 queue_depth 曲线与最终 RPS/P95/P99。"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url, method="POST", headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode())
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _get_json(url: str, token: str) -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--spawn-rate", type=int, default=10)
    parser.add_argument("--duration", default="30s")
    parser.add_argument("--output-root", default="loadtest/results")
    parser.add_argument(
        "--worker-concurrency", type=int, default=2,
        help="仅写入归档元数据；应与被测 Celery worker 保持一致",
    )
    parser.add_argument(
        "--stub-delay-ms", type=int, default=100,
        help="仅写入归档元数据；应与被测 API 的 STUB_DELAY_MS 保持一致",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = Path(args.output_root) / stamp
    output.mkdir(parents=True, exist_ok=False)
    prefix = output / "locust"
    token = _post_json(
        f"{args.host}/api/auth/token",
        {"username": "admin", "password": "supportflow-admin"},
    )["accessToken"]

    stop = threading.Event()
    samples: list[dict] = []

    def sample_metrics() -> None:
        while not stop.is_set():
            try:
                metrics = _get_json(f"{args.host}/api/admin/metrics", token)
                samples.append({
                    "timestamp": datetime.now().isoformat(),
                    "queue_depth": metrics.get("queueDepth"),
                    "active_sessions": metrics.get("activeSessions"),
                })
            except Exception as exc:  # noqa: BLE001
                samples.append({
                    "timestamp": datetime.now().isoformat(),
                    "queue_depth": None,
                    "active_sessions": None,
                    "error": str(exc),
                })
            stop.wait(1)

    sampler = threading.Thread(target=sample_metrics, daemon=True)
    sampler.start()
    command = [
        str(Path(".venv/bin/locust")),
        "-f", "loadtest/locustfile.py",
        "--host", args.host,
        "--headless",
        "-u", str(args.users),
        "-r", str(args.spawn_rate),
        "-t", args.duration,
        "--csv", str(prefix),
        "--only-summary",
    ]
    started = time.time()
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    stop.set()
    sampler.join(timeout=2)

    with (output / "queue_depth.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["timestamp", "queue_depth", "active_sessions", "error"],
            extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)

    aggregate = {}
    chat_stats = {}
    stats_file = Path(f"{prefix}_stats.csv")
    if stats_file.exists():
        with stats_file.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        aggregate = next((row for row in rows if row.get("Name") == "Aggregated"), {})
        chat_stats = next((row for row in rows if row.get("Name") == "POST /chat/message"), {})
    summary = {
        "started_at": datetime.fromtimestamp(started).isoformat(),
        "elapsed_seconds": round(time.time() - started, 3),
        "users": args.users,
        "spawn_rate": args.spawn_rate,
        "duration": args.duration,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "worker_concurrency": args.worker_concurrency,
            "stub_delay_ms": args.stub_delay_ms,
            "queue_depth_source": "Celery broker Redis LLEN(celery)",
        },
        "exit_code": completed.returncode,
        "rps": aggregate.get("Requests/s"),
        "failures_per_second": aggregate.get("Failures/s"),
        "p95_ms": aggregate.get("95%"),
        "p99_ms": aggregate.get("99%"),
        "request_count": aggregate.get("Request Count"),
        "failure_count": aggregate.get("Failure Count"),
        "chat_rps": chat_stats.get("Requests/s"),
        "chat_p95_ms": chat_stats.get("95%"),
        "chat_p99_ms": chat_stats.get("99%"),
        "chat_request_count": chat_stats.get("Request Count"),
        "chat_failure_count": chat_stats.get("Failure Count"),
        "max_queue_depth": max(
            (sample["queue_depth"] for sample in samples
             if isinstance(sample.get("queue_depth"), int)),
            default=None,
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "locust.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output / "locust.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False, indent=2))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
