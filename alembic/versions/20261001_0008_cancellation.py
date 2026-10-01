"""Preserve cancelled booking history while allowing released seats to be rebooked."""

from alembic import op
import sqlalchemy as sa

revision = '20261001_0008'
down_revision = '20260928_0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Existing bookings remain confirmed and all their claims remain active."""
    op.add_column('reservations', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reservation_seats', sa.Column('released_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint('uq_reservation_seats_event_seat', 'reservation_seats', type_='unique')
    op.create_index('uq_reservation_seats_event_seat', 'reservation_seats', ['event_id', 'seat_id'], unique=True, postgresql_where=sa.text('released_at IS NULL'))


def downgrade() -> None:
    """Refuse destructive rollback when cancellation history has been recorded."""
    # A SQL-side guard supports offline rendering and protects cancelled bookings
    # even if inconsistent data somehow exists without released claim rows.
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM reservation_seats WHERE released_at IS NOT NULL)
               OR EXISTS (SELECT 1 FROM reservations WHERE cancelled_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade while cancellation history exists';
            END IF;
        END
        $$;
    """))
    op.drop_index('uq_reservation_seats_event_seat', table_name='reservation_seats')
    op.create_unique_constraint('uq_reservation_seats_event_seat', 'reservation_seats', ['event_id', 'seat_id'])
    op.drop_column('reservation_seats', 'released_at')
    op.drop_column('reservations', 'cancelled_at')
