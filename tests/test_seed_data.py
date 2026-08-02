"""种子数据应能增量补齐完整演示用户，且重复执行不产生重复订单。"""
from datetime import datetime

from app.db.models import Logistics, Order, OrderItem, User
from scripts.seed_data import (
    SHOWCASE_ORDERS,
    SHOWCASE_USERNAME,
    _seed_showcase_user,
)


def test_showcase_user_has_complete_repeatable_data(db):
    now = datetime(2026, 7, 31, 8, 0, 0)

    user_id, rows, created = _seed_showcase_user(db, now)
    db.flush()

    user = db.get(User, user_id)
    assert user.username == SHOWCASE_USERNAME
    assert user.user_level == "vip"
    assert created == len(SHOWCASE_ORDERS)
    assert len(rows) == len(SHOWCASE_ORDERS)
    assert db.query(Order).filter_by(user_id=user_id).count() == len(SHOWCASE_ORDERS)
    assert db.query(OrderItem).count() >= len(SHOWCASE_ORDERS)

    stale = db.query(Order).filter_by(order_no="VIP-LOGI-STALE").one()
    stale_logistics = db.query(Logistics).filter_by(order_id=stale.id).one()
    assert stale_logistics.status == "in_transit"
    assert stale_logistics.last_location == "武汉转运中心"
    assert (now - stale_logistics.last_update_time).total_seconds() == 72 * 3600

    # 再执行一次只读取已有演示数据，不重复插入。
    second_user_id, second_rows, second_created = _seed_showcase_user(db, now)
    db.flush()
    assert second_user_id == user_id
    assert second_created == 0
    assert len(second_rows) == len(SHOWCASE_ORDERS)
    assert db.query(Order).filter_by(user_id=user_id).count() == len(SHOWCASE_ORDERS)
