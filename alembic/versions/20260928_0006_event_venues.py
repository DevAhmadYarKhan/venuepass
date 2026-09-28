"""Link events to venues and freeze their physical seat membership."""

from alembic import op
import sqlalchemy as sa

revision = '20260928_0006'
down_revision = '20260928_0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Require an empty legacy event table rather than guessing venue mappings."""
    # Execute the guard in PostgreSQL rather than querying from Python. Offline
    # migrations emit this block for execution later, without a live connection.
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM events) THEN
                RAISE EXCEPTION 'Existing events require explicit venue mapping before this migration';
            END IF;
        END
        $$;
    """))
    op.add_column('events', sa.Column('venue_id', sa.Uuid(), nullable=False))
    op.create_foreign_key('fk_events_venue', 'events', 'venues', ['venue_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_events_venue_id', 'events', ['venue_id'])
    op.drop_column('events', 'venue')
    op.create_unique_constraint('uq_events_id_venue', 'events', ['id', 'venue_id'])
    op.create_unique_constraint('uq_seats_id_venue', 'seats', ['id', 'venue_id'])
    op.create_table('venue_organizers',
        sa.Column('venue_id', sa.Uuid(), nullable=False),
        sa.Column('organizer_id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('venue_id', 'organizer_id'),
        sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organizer_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_table('event_seats',
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('seat_id', sa.Uuid(), nullable=False),
        sa.Column('venue_id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('event_id', 'seat_id'),
        sa.ForeignKeyConstraint(['event_id', 'venue_id'], ['events.id', 'events.venue_id'], ondelete='RESTRICT', name='fk_event_seats_event_venue'),
        sa.ForeignKeyConstraint(['seat_id', 'venue_id'], ['seats.id', 'seats.venue_id'], ondelete='RESTRICT', name='fk_event_seats_seat_venue'),
    )


def downgrade() -> None:
    """Restore venue text from current venue names; discard grants and membership."""
    op.drop_table('event_seats')
    op.drop_table('venue_organizers')
    op.drop_constraint('uq_seats_id_venue', 'seats', type_='unique')
    op.drop_constraint('uq_events_id_venue', 'events', type_='unique')
    op.add_column('events', sa.Column('venue', sa.String(255), nullable=True))
    op.execute(sa.text('UPDATE events SET venue = venues.name FROM venues WHERE events.venue_id = venues.id'))
    op.alter_column('events', 'venue', nullable=False)
    op.drop_index('ix_events_venue_id', table_name='events')
    op.drop_constraint('fk_events_venue', 'events', type_='foreignkey')
    op.drop_column('events', 'venue_id')
