"""建表。MVP 用 create_all，并为已存在的开发库补少量兼容列。"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.core.logging import get_logger, setup_logging
from app.db.models import Base
from app.db.session import engine

logger = get_logger(__name__)


def ensure_mvp_schema_compat(bind=engine) -> None:
    """create_all 不会给旧表加列；在正式引入 Alembic 前补开发库兼容迁移。"""
    inspector = inspect(bind)
    if "tickets" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("tickets")}
    if "pending_context" not in columns:
        with bind.begin() as conn:
            # JSON 同时可用于 PostgreSQL 与 SQLite；列名固定，不接收外部输入。
            conn.execute(text("ALTER TABLE tickets ADD COLUMN pending_context JSON"))
        logger.info("DB schema upgraded: tickets.pending_context added")
    if "version" not in columns:
        with bind.begin() as conn:
            conn.execute(text(
                "ALTER TABLE tickets ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            ))
        logger.info("DB schema upgraded: tickets.version added")


def init_db() -> None:
    Base.metadata.create_all(engine)
    ensure_mvp_schema_compat(engine)
    logger.info("DB tables created (%d tables)", len(Base.metadata.tables))


if __name__ == "__main__":
    setup_logging()
    init_db()
