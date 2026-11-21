"""Publisher, booking and concurrency behaviour against PostgreSQL.

Set DB_HOST/DB_PORT if the demo database is not on ``db:5432``.  The fixture
recreates the schema, so point it at a disposable database.
"""

import hashlib
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from src.config.settings import engine
from src.constants.workflow import ProgrammeStatus
from src.models.tables import (auditoriums, bookings, programme_slots,
                               programme_versions, publish_receipts, schema)
from src.services import programme
from src.utils.encoding import canonical

AUDITORIUM = {"code": "T-01", "name": "测试影厅", "territory": "上海", "medium": "影院"}


def draft(screenings, rights=(), max_delay=60, note=""):
    return {"note": note, "max_delay_minutes": max_delay,
            "screenings": list(screenings), "rights": list(rights)}


def screening(sid, start, end, film="F-01"):
    return {"screening_id": sid, "film_id": film, "title": f"片 {sid}",
            "territory": "上海", "medium": "影院", "starts_at": start, "ends_at": end}


def right(rid, start, end, film="F-01"):
    return {"right_id": rid, "film_id": film, "territory": "上海",
            "medium": "影院", "starts_at": start, "ends_at": end}


@pytest.fixture()
def auditorium_id():
    schema.drop_all(engine)
    schema.create_all(engine)
    with engine.begin() as conn:
        identity = conn.execute(auditoriums.insert().values(
            **AUDITORIUM, created_at='2026-01-01T00:00:00+00:00')).inserted_primary_key[0]
    yield identity
    schema.drop_all(engine)


def counts():
    with engine.connect() as conn:
        return {
            'versions': conn.execute(select(func.count()).select_from(programme_versions)).scalar_one(),
            'slots': conn.execute(select(func.count()).select_from(programme_slots)).scalar_one(),
            'bookings': conn.execute(select(func.count()).select_from(bookings)).scalar_one(),
            'receipts': conn.execute(select(func.count()).select_from(publish_receipts)).scalar_one(),
        }


def test_publish_switches_the_whole_programme_at_once(auditorium_id):
    first = programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T22:00+08:00")]),
        'operator', 'key-1')
    second = programme.publish(auditorium_id, draft(
        [screening("B", "2026-12-04T20:00+08:00", "2026-12-04T21:00+08:00", film="F-02")],
        [right("R2", "2026-12-04T20:00+08:00", "2026-12-04T21:00+08:00", film="F-02")],
        note="第二版"), 'operator', 'key-2')
    assert (first['version'], second['version']) == (1, 2)
    detail = programme.detail(auditorium_id)
    assert detail['programme']['version'] == 2
    assert [row['screening_id'] for row in detail['programme']['slots']] == ['B']
    assert detail['programme']['note'] == '第二版'
    statuses = {row['version']: row['status'] for row in detail['versions']}
    assert statuses == {1: ProgrammeStatus.SUPERSEDED, 2: ProgrammeStatus.PUBLISHED}


def test_detail_exposes_unplaced_screenings_for_the_programme_page(auditorium_id):
    programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00"),
         screening("B", "2026-12-04T20:00+08:00", "2026-12-04T21:00+08:00", film="F-99")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
        'operator', 'key-1')
    payload = programme.detail(auditorium_id)['programme']
    assert [row['screening_id'] for row in payload['slots']] == ['A']
    assert [row['screening_id'] for row in payload['unplaced']] == ['B']
    assert payload['unplaced'][0]['reason']


def test_retry_with_the_same_key_replays_without_a_second_version(auditorium_id):
    body = draft([screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
                 [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")])
    first = programme.publish(auditorium_id, body, 'operator', 'retry-key')
    second = programme.publish(auditorium_id, body, 'operator', 'retry-key')
    assert second['replayed'] is True
    assert second['version'] == first['version'] == 1
    assert counts()['versions'] == 1
    assert programme.detail(auditorium_id)['programme']['version'] == 1


def test_reused_key_with_a_different_draft_is_rejected(auditorium_id):
    programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
        'operator', 'shared')
    with pytest.raises(HTTPException) as caught:
        programme.publish(auditorium_id, draft(
            [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00")],
            [right("R1", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00")]),
            'operator', 'shared')
    assert caught.value.status_code == 409
    assert counts()['versions'] == 1


def test_failed_write_keeps_the_previous_programme(auditorium_id, monkeypatch):
    programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
        'operator', 'stable')

    class Exploding:
        def insert(self):
            raise RuntimeError('模拟写入失败')

    real = programme.programme_slots
    monkeypatch.setattr(programme, 'programme_slots', Exploding())
    with pytest.raises(RuntimeError):
        programme.publish(auditorium_id, draft(
            [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")],
            [right("R2", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")]),
            'operator', 'broken')
    monkeypatch.setattr(programme, 'programme_slots', real)

    detail = programme.detail(auditorium_id)
    assert detail['programme']['version'] == 1
    assert [row['screening_id'] for row in detail['programme']['slots']] == ['A']
    assert counts()['versions'] == 1
    assert counts()['receipts'] == 1
    # The failed key was never recorded, so the retry can publish normally.
    retried = programme.publish(auditorium_id, draft(
        [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")],
        [right("R2", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")]),
        'operator', 'broken')
    assert retried['version'] == 2


def test_confirmed_booking_removes_the_overlapping_candidate(auditorium_id):
    programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
        'operator', 'base')
    booking = programme.create_booking(auditorium_id, {
        "reference": "企业包场", "starts_at": "2026-12-04T21:00+08:00",
        "ends_at": "2026-12-04T21:30+08:00"}, 'operator', 'booking-1')
    assert booking['status'] == 'confirmed'
    # A 30-minute delay is not enough to clear the 30-minute booking, so the
    # screening is reported unplaced while the booking keeps its slot.
    result = programme.preview(auditorium_id, draft(
        [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")],
        [right("R2", "2026-12-04T21:00+08:00", "2026-12-04T23:00+08:00", film="F-02")],
        max_delay=29))
    assert result['slots'] == []
    assert result['unplaced'][0]['screening_id'] == 'B'
    # With enough delay the screening moves to the first free minute after it.
    shifted = programme.preview(auditorium_id, draft(
        [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-02")],
        [right("R2", "2026-12-04T21:00+08:00", "2026-12-04T23:00+08:00", film="F-02")],
        max_delay=30))
    assert shifted['slots'][0]['starts_at'] == '2026-12-04T21:30+08:00'
    assert shifted['slots'][0]['delay_minutes'] == 30


def test_booking_over_the_current_programme_is_rejected(auditorium_id):
    programme.publish(auditorium_id, draft(
        [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
        'operator', 'base')
    with pytest.raises(HTTPException) as caught:
        programme.create_booking(auditorium_id, {
            "reference": "临时包场", "starts_at": "2026-12-04T19:30+08:00",
            "ends_at": "2026-12-04T20:30+08:00"}, 'operator', 'booking-2')
    assert caught.value.status_code == 409
    assert counts()['bookings'] == 0


def test_booking_retry_is_idempotent(auditorium_id):
    body = {"reference": "企业包场", "starts_at": "2026-12-04T21:00+08:00",
            "ends_at": "2026-12-04T21:30+08:00"}
    first = programme.create_booking(auditorium_id, body, 'operator', 'same')
    second = programme.create_booking(auditorium_id, body, 'operator', 'same')
    assert first['id'] == second['id'] and second['replayed'] is True
    assert counts()['bookings'] == 1


def test_cancelled_booking_no_longer_blocks_the_slot(auditorium_id):
    booking = programme.create_booking(auditorium_id, {
        "reference": "企业包场", "starts_at": "2026-12-04T21:00+08:00",
        "ends_at": "2026-12-04T22:00+08:00"}, 'operator', 'b-1')
    programme.cancel_booking(auditorium_id, booking['id'], 'operator')
    result = programme.preview(auditorium_id, draft(
        [screening("B", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00")],
        [right("R2", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00")]))
    assert result['summary']['排入场次'] == 1


def test_booking_confirmed_first_keeps_its_slot_over_a_racing_publish(auditorium_id, monkeypatch):
    """A booking that commits before the switch makes the switch avoid it."""
    booking_inside = threading.Event()
    release_booking = threading.Event()
    original = programme._current_version
    calls = {'booking': 0}

    def paused_current_version(conn, identity):
        # Only the booking path calls this while holding the auditorium lock.
        if calls['booking'] == 0:
            calls['booking'] += 1
            booking_inside.set()
            assert release_booking.wait(10)
        return original(conn, identity)

    monkeypatch.setattr(programme, '_current_version', paused_current_version)
    errors = []

    def book():
        try:
            programme.create_booking(auditorium_id, {
                "reference": "抢先包场", "starts_at": "2026-12-04T19:00+08:00",
                "ends_at": "2026-12-04T20:00+08:00"}, 'operator', 'race-booking')
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    thread = threading.Thread(target=book)
    thread.start()
    assert booking_inside.wait(10)
    published = {}

    def switch():
        try:
            published['result'] = programme.publish(auditorium_id, draft(
                [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
                [right("R1", "2026-12-04T18:00+08:00", "2026-12-04T22:00+08:00")]),
                'operator', 'race-publish')
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    publisher = threading.Thread(target=switch)
    publisher.start()
    publisher.join(0.4)
    assert publisher.is_alive(), '发布应先等待包场事务结束'
    release_booking.set()
    thread.join(10)
    publisher.join(10)
    monkeypatch.setattr(programme, '_current_version', original)

    assert not errors
    programme.detail(auditorium_id)
    assert counts()['bookings'] == 1
    # The booking owns 19:00–20:00; the switch may only place A at or after
    # 20:00, so it appears with a 60-minute delay and never overlaps the booking.
    slot = published['result']['slots'][0]
    assert slot['screening_id'] == 'A'
    assert slot['starts_at'] == '2026-12-04T20:00+08:00'
    assert slot['delay_minutes'] == 60


def test_publish_confirmed_first_blocks_a_racing_booking(auditorium_id, monkeypatch):
    """A switch that commits first makes the overlapping booking fail."""
    publish_inside = threading.Event()
    release_publish = threading.Event()
    original = programme.plan

    def paused_plan(*args, **kwargs):
        publish_inside.set()
        assert release_publish.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(programme, 'plan', paused_plan)
    errors = []
    outcome = {}

    def switch():
        outcome['result'] = programme.publish(auditorium_id, draft(
            [screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")],
            [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00")]),
            'operator', 'race-publish')

    publisher = threading.Thread(target=switch)
    publisher.start()
    assert publish_inside.wait(10)

    def book():
        try:
            programme.create_booking(auditorium_id, {
                "reference": "迟到的包场", "starts_at": "2026-12-04T19:30+08:00",
                "ends_at": "2026-12-04T20:30+08:00"}, 'operator', 'race-booking')
            outcome['booking'] = 'accepted'
        except HTTPException as exc:
            outcome['booking'] = exc.status_code

    booker = threading.Thread(target=book)
    booker.start()
    booker.join(0.4)
    assert booker.is_alive(), '包场应等待发布事务结束'
    release_publish.set()
    publisher.join(10)
    booker.join(10)
    monkeypatch.setattr(programme, 'plan', original)

    assert not errors
    assert outcome['result']['version'] == 1
    assert outcome['booking'] == 409
    assert counts()['bookings'] == 0


def test_publish_rejects_a_screening_for_another_auditorium(auditorium_id):
    with pytest.raises(HTTPException) as caught:
        programme.preview(auditorium_id, draft(
            [dict(screening("A", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00"),
                  territory="杭州")], []))
    assert caught.value.status_code == 422
    assert '地域和媒介' in caught.value.detail
