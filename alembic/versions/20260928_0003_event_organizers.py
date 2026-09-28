"""Require event ownership and add organizer permission.

Existing disposable events are deleted; downgrade cannot restore those records.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260928_0003"
down_revision = "20260927_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve users, discard ownerless events, and enforce future ownership."""
    op.add_column("users", sa.Column("is_organizer", sa.Boolean(), server_default=sa.false(), nullable=False))
    # Explicitly agreed cleanup: old records have no reliable creator to backfill.
    op.execute(sa.text("DELETE FROM events"))
    op.add_column("events", sa.Column("organizer_id", sa.Uuid(), nullable=False))
    op.create_foreign_key("fk_events_organizer_id_users", "events", "users", ["organizer_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_events_organizer_id", "events", ["organizer_id"])


def downgrade() -> None:
    """Remove ownership requirements without deleting users or remaining events."""
    op.drop_index("ix_events_organizer_id", table_name="events")
    op.drop_constraint("fk_events_organizer_id_users", "events", type_="foreignkey")
    op.drop_column("events", "organizer_id")
    op.drop_column("users", "is_organizer")
