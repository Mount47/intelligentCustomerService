"""Baseline the complete SupportFlow schema.

Revision ID: 20260730_0001
Revises:
Create Date: 2026-07-30

This adoption migration is intentionally idempotent: existing MVP databases were created with
SQLAlchemy create_all, while clean installations start here. Future schema changes must use
explicit Alembic operations and must not mutate this baseline.
"""
from alembic import op

from app.db.models import Base

revision = "20260730_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
