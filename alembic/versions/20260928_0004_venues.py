"""Add venue ownership and independent venue-manager permission."""

from alembic import op
import sqlalchemy as sa

revision = "20260928_0004"
down_revision = "20260928_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend existing accounts and create venues without changing events."""
    op.add_column("users", sa.Column("is_venue_manager", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "venues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(1000), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_venues_owner_id_users", ondelete="RESTRICT"),
    )
    op.create_index("ix_venues_owner_id", "venues", ["owner_id"])


def downgrade() -> None:
    """Remove this feature's records and flag while preserving users and events."""
    op.drop_index("ix_venues_owner_id", table_name="venues")
    op.drop_table("venues")
    op.drop_column("users", "is_venue_manager")
