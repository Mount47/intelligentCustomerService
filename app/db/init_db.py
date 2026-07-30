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
    table_names = inspector.get_table_names()
    if "users" in table_names:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        user_additions = {
            "password_hash": "VARCHAR(255)",
            "role": "VARCHAR(16) NOT NULL DEFAULT 'user'",
            "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        }
        for name, ddl in user_additions.items():
            if name not in user_columns:
                with bind.begin() as conn:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                logger.info("DB schema upgraded: users.%s added", name)
    if "tickets" not in table_names:
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
    if "agent_sessions" in table_names:
        session_columns = {c["name"] for c in inspector.get_columns("agent_sessions")}
        if "source_message_id" not in session_columns:
            with bind.begin() as conn:
                # 旧开发库先补可空列；新库由 ORM metadata 建 FK/唯一索引。
                conn.execute(text(
                    "ALTER TABLE agent_sessions ADD COLUMN source_message_id INTEGER"
                ))
            logger.info("DB schema upgraded: agent_sessions.source_message_id added")


def init_db() -> None:
    Base.metadata.create_all(engine)
    ensure_mvp_schema_compat(engine)
    logger.info("DB tables created (%d tables)", len(Base.metadata.tables))


if __name__ == "__main__":
    setup_logging()
    init_db()
