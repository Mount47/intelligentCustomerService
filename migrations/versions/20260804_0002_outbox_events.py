"""Add outbox_events for transactional message delivery.

Revision ID: 20260804_0002
Revises: 20260730_0001
Create Date: 2026-08-04

消除"数据库已提交、消息队列投递失败"的窗口：用户消息/会话与待投递事件同事务落库，
独立 relay 负责补投。基线迁移不可变更，这里使用显式 Alembic 操作。
"""
import sqlalchemy as sa
from alembic import op

revision = "20260804_0002"
down_revision = "20260730_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 早期库可能已由 create_all 基线建出该表；存在则跳过，保持迁移可重入。
    if sa.inspect(bind).has_table("outbox_events"):
        return
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedup_key", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_outbox_events_dedup_key", "outbox_events",
                    ["dedup_key"], unique=True)
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_available_at", "outbox_events", ["available_at"])
    # relay 的取件查询就是 (status, available_at)；复合索引让积压时也不退化成全表扫。
    op.create_index("ix_outbox_events_status_available_at", "outbox_events",
                    ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_status_available_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_available_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_dedup_key", table_name="outbox_events")
    op.drop_table("outbox_events")
