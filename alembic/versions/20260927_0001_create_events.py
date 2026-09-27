"""Create the initial events schema.

Revision ID: 20260927_0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "20260927_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create event storage with capacity and chronological constraints."""
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("venue", sa.String(255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("capacity > 0", name="ck_events_capacity_positive"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_events_end_after_start"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove event storage when reverting the initial schema."""
    op.drop_table("events")
