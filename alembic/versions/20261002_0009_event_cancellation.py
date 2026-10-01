"""Track organizer event cancellation and explain reservation cancellation history."""

from alembic import op
import sqlalchemy as sa

revision = '20261002_0009'
down_revision = '20261001_0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve existing customer cancellations and leave existing events active."""
    op.add_column('events', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reservations', sa.Column('cancellation_reason', sa.String(32), nullable=True))
    op.execute("UPDATE reservations SET cancellation_reason = 'customer' WHERE cancelled_at IS NOT NULL")
    # Explicit IS NOT NULL avoids PostgreSQL CHECK accepting an unknown result.
    op.create_check_constraint('ck_reservations_cancellation', 'reservations',
        "(cancelled_at IS NULL AND cancellation_reason IS NULL) OR "
        "(cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL AND "
        "cancellation_reason IN ('customer', 'event_cancelled'))")


def downgrade() -> None:
    """Allow rollback only when it cannot erase organizer cancellation history."""
    # A server-side guard also renders correctly through Alembic's offline mode.
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM events WHERE cancelled_at IS NOT NULL)
               OR EXISTS (SELECT 1 FROM reservations WHERE cancellation_reason = 'event_cancelled') THEN
                RAISE EXCEPTION 'Cannot downgrade while event cancellation history exists';
            END IF;
        END
        $$;
    """))
    op.drop_constraint('ck_reservations_cancellation', 'reservations', type_='check')
    op.drop_column('reservations', 'cancellation_reason')
    op.drop_column('events', 'cancelled_at')
