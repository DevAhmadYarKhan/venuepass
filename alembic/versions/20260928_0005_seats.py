"""Create physical seats without changing existing venue or event data."""

from alembic import op
import sqlalchemy as sa

revision = "20260928_0005"
down_revision = "20260928_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enforce venue membership, positive numbers, and unique seat identities."""
    op.create_table(
        "seats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(100), nullable=False),
        sa.Column("row", sa.String(100), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("venue_id", "section", "row", "number", name="uq_seats_identity"),
        sa.CheckConstraint("number > 0", name="ck_seats_number_positive"),
    )


def downgrade() -> None:
    """Remove seats while preserving venues and other existing records."""
    op.drop_table("seats")
