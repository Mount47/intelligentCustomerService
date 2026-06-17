"""建表。MVP 用 create_all；后续如需迁移再上 Alembic。"""
from __future__ import annotations

from app.core.logging import get_logger, setup_logging
from app.db.models import Base
from app.db.session import engine

logger = get_logger(__name__)


def init_db() -> None:
    Base.metadata.create_all(engine)
    logger.info("DB tables created (%d tables)", len(Base.metadata.tables))


if __name__ == "__main__":
    setup_logging()
    init_db()
