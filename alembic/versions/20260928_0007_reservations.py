"""Add confirmed reservations with atomic retry identity and unique seat claims."""

from alembic import op
import sqlalchemy as sa

revision = '20260928_0007'
down_revision = '20260928_0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve existing records and enforce booking integrity in PostgreSQL."""
    op.create_table('reservations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('id', 'event_id', name='uq_reservations_id_event'),
        sa.UniqueConstraint('user_id', 'event_id', 'idempotency_key', name='uq_reservations_retry'),
    )
    op.create_index('ix_reservations_user_id', 'reservations', ['user_id'])
    op.create_table('reservation_seats',
        sa.Column('reservation_id', sa.Uuid(), nullable=False),
        sa.Column('seat_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('reservation_id', 'seat_id'),
        sa.UniqueConstraint('event_id', 'seat_id', name='uq_reservation_seats_event_seat'),
        sa.ForeignKeyConstraint(['reservation_id', 'event_id'], ['reservations.id', 'reservations.event_id'], ondelete='RESTRICT', name='fk_reservation_seats_reservation'),
        sa.ForeignKeyConstraint(['event_id', 'seat_id'], ['event_seats.event_id', 'event_seats.seat_id'], ondelete='RESTRICT', name='fk_reservation_seats_membership'),
    )


def downgrade() -> None:
    """Remove bookings only; preserve accounts, events, and their seat membership."""
    op.drop_table('reservation_seats')
    op.drop_index('ix_reservations_user_id', table_name='reservations')
    op.drop_table('reservations')
