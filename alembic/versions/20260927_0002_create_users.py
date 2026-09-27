"""Add local accounts without changing existing events."""

from alembic import op
import sqlalchemy as sa

revision = "20260927_0002"
down_revision = "20260927_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store only password hashes and enforce unique account identifiers."""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )


def downgrade() -> None:
    """Remove accounts while leaving the events table intact."""
    op.drop_table("users")
