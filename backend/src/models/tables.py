from sqlalchemy import (JSON, Column, ForeignKey, Integer, MetaData, String,
                        Table, Text, UniqueConstraint)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))

# ---- Auditorium programme switching ---------------------------------------
# One row per venue/screen combined with its distribution medium.  Programme
# versions are append-only; exactly one version per auditorium is active at a
# time.  Confirmed private bookings block the auditorium independently of the
# programme version so a booking and a programme switch can race safely.
auditoriums = Table('auditoriums', schema,
    Column('id', Integer, primary_key=True),
    Column('code', String(40), nullable=False, unique=True),
    Column('name', String(120), nullable=False),
    Column('territory', String(80), nullable=False),
    Column('medium', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))

bookings = Table('auditorium_bookings', schema,
    Column('id', Integer, primary_key=True),
    Column('auditorium_id', ForeignKey('auditoriums.id'), nullable=False, index=True),
    Column('reference', String(120), nullable=False),
    Column('starts_at', String(32), nullable=False),
    Column('ends_at', String(32), nullable=False),
    Column('status', String(20), nullable=False),
    Column('idempotency_key', String(80)),
    Column('created_by', String(40), nullable=False),
    Column('created_at', String(32), nullable=False),
    UniqueConstraint('auditorium_id', 'idempotency_key', name='uq_booking_idempotency'))

programme_versions = Table('programme_versions', schema,
    Column('id', Integer, primary_key=True),
    Column('auditorium_id', ForeignKey('auditoriums.id'), nullable=False, index=True),
    Column('version', Integer, nullable=False),
    Column('status', String(20), nullable=False, index=True),
    Column('note', String(200)),
    Column('payload_digest', String(64), nullable=False),
    Column('unplaced', JSON, nullable=False),
    Column('created_by', String(40), nullable=False),
    Column('created_at', String(32), nullable=False),
    UniqueConstraint('auditorium_id', 'version', name='uq_programme_version'))

programme_slots = Table('programme_slots', schema,
    Column('id', Integer, primary_key=True),
    Column('version_id', ForeignKey('programme_versions.id'), nullable=False, index=True),
    Column('screening_id', String(80), nullable=False),
    Column('film_id', String(80), nullable=False),
    Column('title', String(160), nullable=False),
    Column('territory', String(80), nullable=False),
    Column('medium', String(40), nullable=False),
    Column('starts_at', String(32), nullable=False),
    Column('ends_at', String(32), nullable=False),
    Column('original_starts_at', String(32), nullable=False),
    Column('original_ends_at', String(32), nullable=False),
    Column('delay_minutes', Integer, nullable=False),
    UniqueConstraint('version_id', 'screening_id', name='uq_programme_slot'))

# A receipt makes a publish call replayable: retrying with the same key never
# creates a second version or a second occupancy.
publish_receipts = Table('publish_receipts', schema,
    Column('id', Integer, primary_key=True),
    Column('auditorium_id', ForeignKey('auditoriums.id'), nullable=False, index=True),
    Column('idempotency_key', String(80), nullable=False),
    Column('payload_digest', String(64), nullable=False),
    Column('version_id', ForeignKey('programme_versions.id'), nullable=False),
    Column('response', JSON, nullable=False),
    Column('created_at', String(32), nullable=False),
    UniqueConstraint('auditorium_id', 'idempotency_key', name='uq_publish_idempotency'))
