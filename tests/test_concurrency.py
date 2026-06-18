"""M9：退款幂等并发硬验 —— 多线程同时打同一退款，断言只成一条（§9.2 层3 DB 兜底）。

默认用文件 sqlite（验逻辑正确性，sqlite 写串行但 UNIQUE + IntegrityError 回查路径仍被走到）。
设 TEST_DATABASE_URL=postgresql+psycopg://... 则跑真并发（postgres 多连接同时写）。
"""
import os
import threading

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Order, RefundRequest, User
from app.services import refund_service


def _engine(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return create_engine(url)
    return create_engine(
        f"sqlite:///{tmp_path / 'conc.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def test_concurrent_same_refund_creates_one_row(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as s:
        u = User(username="conc")
        s.add(u)
        s.flush()
        o = Order(user_id=u.id, order_no="CONC-1", status="paid",
                  total_amount=100, product_type="normal")
        s.add(o)
        s.commit()
        uid, oid = u.id, o.id

    n = 8
    barrier = threading.Barrier(n)
    results: list[dict] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()                      # 所有线程同一刻发起，制造最大争用
        sess = Session()
        try:
            r = refund_service.create_refund_draft(
                sess, user_id=uid, order_id=oid,
                refund_reason="不想要了", idempotency_key="K-SAME")
            with lock:
                results.append(r)
        except Exception as exc:            # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            sess.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发出现异常: {errors}"
    with Session() as s:
        count = s.scalar(select(func.count()).select_from(RefundRequest)
                         .where(RefundRequest.user_id == uid))
    assert count == 1                        # 只成一条
    assert len({r["refund_request_id"] for r in results}) == 1   # 所有请求拿到同一条
    assert sum(1 for r in results if r["created"]) == 1          # 仅一个真正创建，其余 dedup/race
