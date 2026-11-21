import hashlib

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from src.config.settings import IS_MYSQL, engine
from src.constants.workflow import BookingStatus, ProgrammeStatus, Role
from src.models.tables import (audit, auditoriums, bookings, cases, evidence,
                               programme_slots, programme_versions, runs,
                               schema, users)
from src.services.domain import PROGRAMME, SPEC
from src.services.security import hash_password
from src.utils.encoding import canonical
from src.utils.time import desk_stamp, utc_time


def keep_existing(conn, table, key, values):
    if IS_MYSQL:
        statement = mysql_insert(table).values(**values).on_duplicate_key_update(id=table.c.id)
    else:
        statement = postgres_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
    conn.execute(statement)


def seed_auditorium(conn):
    keep_existing(conn, auditoriums, 'code', {
        'code': 'SHA-01', 'name': '上海影城 1 号厅', 'territory': '上海',
        'medium': '影院', 'created_at': utc_time()})
    auditorium = conn.execute(select(auditoriums).where(auditoriums.c.code == 'SHA-01')).mappings().one()
    now = utc_time()
    exists = conn.execute(select(func.count()).select_from(programme_versions)
                          .where(programme_versions.c.auditorium_id == auditorium['id'])).scalar_one()
    if not exists:
        version_id = conn.execute(programme_versions.insert().values(
            auditorium_id=auditorium['id'], version=1, status=ProgrammeStatus.PUBLISHED,
            note='演示初始节目单',
            payload_digest=hashlib.sha256(canonical(PROGRAMME['seed_programme'])).hexdigest(),
            unplaced={'unplaced': [], 'summary': None}, created_by='demo',
            created_at=now)).inserted_primary_key[0]
        conn.execute(programme_slots.insert(), [{
            'version_id': version_id,
            'screening_id': slot['screening_id'],
            'film_id': slot['film_id'],
            'title': slot['title'],
            'territory': slot['territory'],
            'medium': slot['medium'],
            'starts_at': desk_stamp(slot['starts_at'], '场次开始时间'),
            'ends_at': desk_stamp(slot['ends_at'], '场次结束时间'),
            'original_starts_at': desk_stamp(slot['starts_at'], '场次开始时间'),
            'original_ends_at': desk_stamp(slot['ends_at'], '场次结束时间'),
            'delay_minutes': 0,
        } for slot in PROGRAMME['seed_programme']])
    booking = PROGRAMME.get('seed_booking')
    if booking:
        known = conn.execute(select(func.count()).select_from(bookings).where(
            bookings.c.auditorium_id == auditorium['id'],
            bookings.c.idempotency_key == 'demo-booking')).scalar_one()
        if not known:
            conn.execute(bookings.insert().values(
                auditorium_id=auditorium['id'], reference=booking['reference'],
                starts_at=desk_stamp(booking['starts_at'], '包场开始时间'),
                ends_at=desk_stamp(booking['ends_at'], '包场结束时间'),
                status=BookingStatus.CONFIRMED, idempotency_key='demo-booking',
                created_by='demo', created_at=now))
    return auditorium


def seed():
    with engine.begin() as conn:
        for role in Role:
            keep_existing(conn, users, 'username', {'username': role.value, 'role': role.value,
                'password_hash': hash_password('Demo-Walkthrough-2026')})
        keep_existing(conn, cases, 'slug', {'slug': 'demo', 'title': SPEC['name'],
            'payload': SPEC['sample'], 'author': 'demo', 'created_at': utc_time()})
        seed_auditorium(conn)
        return {table.name: conn.execute(select(func.count()).select_from(table)).scalar_one()
                for table in (users, cases, runs, evidence, audit, auditoriums,
                              programme_versions, programme_slots, bookings)}


if __name__ == '__main__':
    schema.create_all(engine)
    print(seed())
