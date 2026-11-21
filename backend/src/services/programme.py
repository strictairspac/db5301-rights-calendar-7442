"""Batch programme switching for a single auditorium.

A draft lists screenings that may stay at their original slot or move forward
by at most ``max_delay_minutes``.  A screening only qualifies when its whole
run is covered by one or more merged rights windows (same film, territory and
medium, ``*`` allowed on the rights side) and it does not overlap a confirmed
private booking.  Among the qualifying screenings the planner keeps as many as
possible and, on a tie, the smallest total delay.

Publishing is a single database transaction guarded by a row lock on the
auditorium.  Bookings take the same lock, so a booking and a programme switch
are ordered by whoever commits first; a failed write leaves the previous
programme active.  An idempotency receipt makes retries replay the original
result instead of occupying the auditorium twice.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import func, select

from src.config.settings import engine
from src.constants.workflow import BookingStatus, ProgrammeStatus
from src.models.tables import (auditoriums, bookings, programme_slots,
                               programme_versions, publish_receipts)
from src.services.workflow import log
from src.utils.encoding import canonical
from src.utils.time import desk_time, parse_timestamp, utc_time

MAX_DELAY_LIMIT = 24 * 60
MAX_SCREENINGS = 200
MAX_RIGHTS = 2000
SEARCH_NODE_LIMIT = 20_000
SEARCH_SECONDS = 1.0
SCOPE_FIELDS = ("film_id", "territory", "medium")


def _minute(value, label):
    return int(parse_timestamp(value, label).timestamp() // 60)


def _desk(minute):
    return desk_time(datetime.fromtimestamp(minute * 60, timezone.utc))


def _merge_halves(ranges):
    """Merge closed-open [start, end) minute spans."""
    ordered = sorted((lo, hi) for lo, hi in ranges if lo < hi)
    merged = []
    for lo, hi in ordered:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _merge_minutes(ranges):
    """Merge inclusive spans of start minutes."""
    ordered = sorted((lo, hi) for lo, hi in ranges if lo <= hi)
    merged = []
    for lo, hi in ordered:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _subtract(ranges, blocks):
    """Remove inclusive start-minute blocks from inclusive start-minute ranges."""
    blocks = _merge_minutes(blocks)
    result = []
    for lo, hi in ranges:
        cursor = lo
        for block_start, block_end in blocks:
            if block_end < cursor:
                continue
            if block_start > hi:
                break
            if block_start > cursor:
                result.append((cursor, min(hi, block_start - 1)))
            cursor = max(cursor, block_end + 1)
            if cursor > hi:
                break
        if cursor <= hi:
            result.append((cursor, hi))
    return result


# ---- input validation ------------------------------------------------------


def _text(record, field, label):
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label}的{field}须为有效非空文本")
    return value.strip()


def _read_screenings(payload, auditorium):
    records = payload.get("screenings")
    if not isinstance(records, list):
        raise ValueError("待校核排片须为列表")
    if len(records) > MAX_SCREENINGS:
        raise ValueError(f"单次改版最多 {MAX_SCREENINGS} 场")
    screenings = []
    identifiers = set()
    for number, record in enumerate(records, start=1):
        label = f"待校核排片第 {number} 行"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        screening_id = _text(record, "screening_id", label)
        if screening_id in identifiers:
            raise ValueError(f"{label}的场次编号重复（{screening_id}）")
        identifiers.add(screening_id)
        film_id = _text(record, "film_id", label)
        title = _text(record, "title", label)
        territory = _text(record, "territory", label)
        medium = _text(record, "medium", label)
        if "*" in (territory, medium):
            raise ValueError(f"{label}须填写具体地域和媒介")
        if (territory, medium) != (auditorium["territory"], auditorium["medium"]):
            raise ValueError(
                f"{label}的地域和媒介须与影厅一致"
                f"（{auditorium['territory']}·{auditorium['medium']}）")
        start = _minute(record.get("starts_at"), f"{label}开始时间")
        end = _minute(record.get("ends_at"), f"{label}结束时间")
        if end <= start:
            raise ValueError(f"{label}的结束时间须晚于开始时间")
        screenings.append({"screening_id": screening_id, "film_id": film_id,
                           "title": title, "territory": territory, "medium": medium,
                           "start": start, "duration": end - start})
    return screenings


def _read_rights(payload):
    records = payload.get("rights")
    if not isinstance(records, list):
        raise ValueError("已生效授权窗口须为列表")
    if len(records) > MAX_RIGHTS:
        raise ValueError(f"单次最多导入 {MAX_RIGHTS} 条授权")
    rights = []
    identifiers = set()
    for number, record in enumerate(records, start=1):
        label = f"已生效授权窗口第 {number} 行"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        right_id = _text(record, "right_id", label)
        if right_id in identifiers:
            raise ValueError(f"{label}的授权编号重复（{right_id}）")
        identifiers.add(right_id)
        row = {"right_id": right_id}
        for field in SCOPE_FIELDS:
            row[field] = _text(record, field, label)
        start = _minute(record.get("starts_at"), f"{label}开始时间")
        end = _minute(record.get("ends_at"), f"{label}结束时间")
        if end <= start:
            raise ValueError(f"{label}的结束时间须晚于开始时间")
        row.update(start=start, end=end)
        rights.append(row)
    return rights


def read_draft(payload, auditorium):
    if not isinstance(payload, dict):
        raise ValueError("改版草案须为对象")
    max_delay = payload.get("max_delay_minutes")
    if type(max_delay) is not int or not 0 <= max_delay <= MAX_DELAY_LIMIT:
        raise ValueError(f"最多顺延分钟须为 0 到 {MAX_DELAY_LIMIT} 的整数")
    note = payload.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise ValueError("改版说明最多 200 个字符")
    return (_read_screenings(payload, auditorium), _read_rights(payload),
            max_delay, (note or "").strip())


# ---- planning --------------------------------------------------------------


def _allowed_starts(screening, rights, max_delay, booking_rows):
    """Inclusive start minutes that keep the whole run inside merged rights and
    clear of confirmed bookings."""
    spans = [(row["start"], row["end"]) for row in rights
             if row["film_id"] == screening["film_id"]
             and row["territory"] in (screening["territory"], "*")
             and row["medium"] in (screening["medium"], "*")]
    original = screening["start"]
    duration = screening["duration"]
    limit = original + max_delay
    windows = []
    for start, end in _merge_halves(spans):
        lo = max(original, start)
        hi = min(limit, end - duration)
        if lo <= hi:
            windows.append((lo, hi))
    blocked = [(row["start"] - duration + 1, row["end"] - 1) for row in booking_rows]
    return _subtract(_merge_minutes(windows), blocked)


def _earliest(item, cursor=0):
    for lo, hi in item["allowed"]:
        start = max(lo, cursor)
        if start <= hi:
            return start
    return None


def _optimize(items):
    """Branch and bound over left-justified placements.

    For a fixed set of screenings, shifting each one left — to the earliest
    allowed start at or after the previous run — shortens every delay and never
    creates overlap, so a schedule is fully described by the current cursor and
    the set of placed screenings.  The search enumerates orderings, keeps the
    schedule with the most placed screenings and, on a tie, the smallest total
    delay.  It is exact until the node or time budget runs out; the caller is
    told whether the returned schedule is proven optimal.
    """
    count = len(items)
    best = {"placed": 0, "delay": 0, "choice": {}}
    # Greedy by earliest finishing window seeds the search with a strong bound;
    # interval scheduling by earliest deadline maximises the count when delays
    # are free, and preferring earlier deadlines leaves the most room behind.
    def deadline(index):
        item = items[index]
        return (item["allowed"][-1][1] + item["duration"], item["start"]) if item["allowed"] \
            else (float('inf'), item["start"])

    cursor = 0
    for index in sorted(range(count), key=deadline):
        start = _earliest(items[index], cursor)
        if start is not None:
            best["choice"][index] = start
            best["placed"] += 1
            best["delay"] += start - items[index]["start"]
            cursor = start + items[index]["duration"]
    memo = {}
    nodes = 0
    deadline = monotonic() + SEARCH_SECONDS
    truncated = False

    def lower_bound(remaining, cursor):
        total = 0
        for index in remaining:
            start = _earliest(items[index], cursor)
            if start is not None:
                total += start - items[index]["start"]
        return total

    def search(cursor, remaining, choice, delay):
        nonlocal nodes, truncated
        if truncated:
            return
        nodes += 1
        if nodes > SEARCH_NODE_LIMIT or (nodes & 1023 == 0 and monotonic() > deadline):
            truncated = True
            return
        placed = len(choice)
        if placed > best["placed"] or (placed == best["placed"] and delay < best["delay"]):
            best.update(placed=placed, delay=delay, choice=dict(choice))
        if placed + len(remaining) < best["placed"]:
            return
        if (placed + len(remaining) == best["placed"]
                and delay + lower_bound(remaining, cursor) >= best["delay"]):
            # Ties are already recorded, so only a strictly shorter delay can win.
            return
        key = (cursor, remaining)
        previous = memo.get(key)
        if previous is not None and previous[0] >= placed and previous[1] <= delay:
            return
        memo[key] = (placed, delay)
        candidates = []
        for index in remaining:
            start = _earliest(items[index], cursor)
            if start is not None:
                candidates.append((start, index))
        candidates.sort()
        for _, index in candidates:
            start = _earliest(items[index], cursor)
            item = items[index]
            choice[index] = start
            search(start + item["duration"], remaining - {index}, choice,
                   delay + start - item["start"])
            del choice[index]

    search(0, frozenset(range(count)), {}, 0)
    return best["choice"], not truncated


def plan(draft, auditorium, booking_rows):
    screenings, rights, max_delay, note = read_draft(draft, auditorium)
    items = [{
        **screening,
        "allowed": _allowed_starts(screening, rights, max_delay, booking_rows),
    } for screening in screenings]
    choice, optimal = _optimize(items)
    placed = []
    unplaced = []
    for index, item in enumerate(items):
        if index in choice:
            start = choice[index]
            placed.append({
                "screening_id": item["screening_id"],
                "film_id": item["film_id"],
                "title": item["title"],
                "territory": item["territory"],
                "medium": item["medium"],
                "original_start": _desk(item["start"]),
                "original_end": _desk(item["start"] + item["duration"]),
                "starts_at": _desk(start),
                "ends_at": _desk(start + item["duration"]),
                "delay_minutes": start - item["start"],
            })
        else:
            if not item["allowed"]:
                reason = "顺延上限内没有覆盖整场的授权"
            else:
                reason = "与已排场次或已确认包场冲突，未能排入"
            unplaced.append({
                "screening_id": item["screening_id"],
                "film_id": item["film_id"],
                "title": item["title"],
                "territory": item["territory"],
                "medium": item["medium"],
                "original_start": _desk(item["start"]),
                "original_end": _desk(item["start"] + item["duration"]),
                "earliest_start": _desk(item["allowed"][0][0]) if item["allowed"] else None,
                "reason": reason,
            })
    placed.sort(key=lambda row: (row["starts_at"], row["screening_id"]))
    unplaced.sort(key=lambda row: (row["original_start"], row["screening_id"]))
    summary = {
        "草案场次": len(items),
        "排入场次": len(placed),
        "未排场次": len(unplaced),
        "顺延场次": sum(1 for row in placed if row["delay_minutes"]),
        "顺延分钟": sum(row["delay_minutes"] for row in placed),
        "包场数": len(booking_rows),
        "最优解": optimal,
    }
    return {"note": note, "max_delay_minutes": max_delay, "summary": summary,
            "slots": placed, "unplaced": unplaced}


# ---- reads -----------------------------------------------------------------


def _auditorium(conn, identity, lock=False):
    query = select(auditoriums).where(auditoriums.c.id == identity)
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该影厅')
    return dict(row)


def _booking_rows(conn, identity, lock=False):
    query = (select(bookings).where(bookings.c.auditorium_id == identity,
                                    bookings.c.status == BookingStatus.CONFIRMED)
             .order_by(bookings.c.starts_at, bookings.c.id))
    if lock:
        query = query.with_for_update()
    return [{"id": row["id"], "reference": row["reference"],
             "start": _minute(row["starts_at"], '包场开始时间'),
             "end": _minute(row["ends_at"], '包场结束时间'),
             "starts_at": row["starts_at"], "ends_at": row["ends_at"]}
            for row in conn.execute(query).mappings()]


def _current_version(conn, identity):
    row = conn.execute(select(programme_versions)
                       .where(programme_versions.c.auditorium_id == identity,
                              programme_versions.c.status == ProgrammeStatus.PUBLISHED)
                       .order_by(programme_versions.c.version.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def _version_slots(conn, version_id):
    return [dict(row) for row in conn.execute(
        select(programme_slots).where(programme_slots.c.version_id == version_id)
        .order_by(programme_slots.c.starts_at, programme_slots.c.id)).mappings()]


def _stored_unplaced(version):
    payload = version.get("unplaced")
    if isinstance(payload, dict):
        return payload.get("unplaced", []), payload.get("summary")
    return [], None


def _programme_payload(conn, version):
    if version is None:
        return None
    unplaced, summary = _stored_unplaced(version)
    return {
        "id": version["id"],
        "version": version["version"],
        "note": version["note"],
        "created_by": version["created_by"],
        "created_at": version["created_at"],
        "summary": summary,
        "slots": [{
            "screening_id": row["screening_id"],
            "film_id": row["film_id"],
            "title": row["title"],
            "territory": row["territory"],
            "medium": row["medium"],
            "original_start": row["original_starts_at"],
            "original_end": row["original_ends_at"],
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "delay_minutes": row["delay_minutes"],
        } for row in _version_slots(conn, version["id"])],
        "unplaced": unplaced,
    }


def detail(identity):
    with engine.connect() as conn:
        auditorium = _auditorium(conn, identity)
        programme = _programme_payload(conn, _current_version(conn, identity))
        versions = [dict(row) for row in conn.execute(
            select(programme_versions.c.id, programme_versions.c.version,
                   programme_versions.c.status, programme_versions.c.note,
                   programme_versions.c.created_by, programme_versions.c.created_at)
            .where(programme_versions.c.auditorium_id == identity)
            .order_by(programme_versions.c.version.desc())).mappings()]
        booking_rows = [dict(row) for row in conn.execute(
            select(bookings).where(bookings.c.auditorium_id == identity)
            .order_by(bookings.c.starts_at.desc(), bookings.c.id.desc()).limit(100)).mappings()]
        return {"auditorium": auditorium, "programme": programme,
                "versions": versions, "bookings": booking_rows}


def listing():
    with engine.connect() as conn:
        rows = conn.execute(
            select(auditoriums.c.id, auditoriums.c.code, auditoriums.c.name,
                   auditoriums.c.territory, auditoriums.c.medium,
                   programme_versions.c.version, programme_versions.c.created_at)
            .outerjoin(programme_versions,
                       (programme_versions.c.auditorium_id == auditoriums.c.id)
                       & (programme_versions.c.status == ProgrammeStatus.PUBLISHED))
            .order_by(auditoriums.c.id)).mappings()
        return [{"id": row["id"], "code": row["code"], "name": row["name"],
                 "territory": row["territory"], "medium": row["medium"],
                 "version": row["version"], "published_at": row["created_at"]}
                for row in rows]


# ---- bookings --------------------------------------------------------------


def create_booking(identity, form, actor, idempotency_key):
    key = (idempotency_key or '').strip()
    if not key or len(key) > 80:
        raise HTTPException(400, '缺少有效的幂等键')
    reference = (form.get('reference') or '').strip()
    if not reference or len(reference) > 120 or "\x00" in reference:
        raise HTTPException(422, '包场名称须为 1 到 120 个字符')
    try:
        start = _minute(form.get('starts_at'), '包场开始时间')
        end = _minute(form.get('ends_at'), '包场结束时间')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if end <= start:
        raise HTTPException(422, '包场结束时间须晚于开始时间')
    with engine.begin() as conn:
        _auditorium(conn, identity, lock=True)
        existing = conn.execute(select(bookings).where(
            bookings.c.auditorium_id == identity,
            bookings.c.idempotency_key == key)).mappings().first()
        if existing:
            return {**dict(existing), 'replayed': True}
        current = _current_version(conn, identity)
        if current:
            for slot in _version_slots(conn, current['id']):
                slot_start = _minute(slot['starts_at'], '场次开始时间')
                slot_end = _minute(slot['ends_at'], '场次结束时间')
                if start < slot_end and end > slot_start:
                    raise HTTPException(
                        409, f"该时段与现行节目单《{slot['title']}》"
                            f"（{slot['starts_at']} 至 {slot['ends_at']}）重叠")
        for row in _booking_rows(conn, identity, lock=True):
            if start < row['end'] and end > row['start']:
                raise HTTPException(409, f'该时段与已确认包场「{row["reference"]}」重叠')
        booking_id = conn.execute(bookings.insert().values(
            auditorium_id=identity, reference=reference,
            starts_at=_desk(start), ends_at=_desk(end),
            status=BookingStatus.CONFIRMED, idempotency_key=key,
            created_by=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'confirm_booking', booking_id)
        row = conn.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
        return {**dict(row), 'replayed': False}


def cancel_booking(identity, booking_id, actor):
    with engine.begin() as conn:
        _auditorium(conn, identity, lock=True)
        row = conn.execute(select(bookings).where(
            bookings.c.id == booking_id,
            bookings.c.auditorium_id == identity)).mappings().first()
        if row is None:
            raise HTTPException(404, '找不到该包场登记')
        if row['status'] != BookingStatus.CANCELLED:
            conn.execute(bookings.update().where(bookings.c.id == booking_id)
                         .values(status=BookingStatus.CANCELLED))
            log(conn, actor, 'cancel_booking', booking_id)
        row = conn.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
        return dict(row)


# ---- publishing ------------------------------------------------------------


def preview(identity, draft):
    with engine.connect() as conn:
        auditorium = _auditorium(conn, identity)
        booking_rows = _booking_rows(conn, identity)
    try:
        return plan(draft, auditorium, booking_rows)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from None


def publish(identity, draft, actor, idempotency_key):
    key = (idempotency_key or '').strip()
    if not key or len(key) > 80:
        raise HTTPException(400, '缺少有效的幂等键')
    digest = hashlib.sha256(canonical(draft)).hexdigest()
    with engine.begin() as conn:
        auditorium = _auditorium(conn, identity, lock=True)
        receipt = conn.execute(select(publish_receipts).where(
            publish_receipts.c.auditorium_id == identity,
            publish_receipts.c.idempotency_key == key)).mappings().first()
        if receipt:
            if receipt['payload_digest'] != digest:
                raise HTTPException(409, '该幂等键已用于另一份改版草案')
            return {**receipt['response'], 'replayed': True}
        try:
            result = plan(draft, auditorium, _booking_rows(conn, identity))
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from None
        latest = conn.execute(select(func.max(programme_versions.c.version))
                              .where(programme_versions.c.auditorium_id == identity)).scalar_one()
        version_number = (latest or 0) + 1
        conn.execute(programme_versions.update().where(
            programme_versions.c.auditorium_id == identity,
            programme_versions.c.status == ProgrammeStatus.PUBLISHED)
            .values(status=ProgrammeStatus.SUPERSEDED))
        version_id = conn.execute(programme_versions.insert().values(
            auditorium_id=identity, version=version_number,
            status=ProgrammeStatus.PUBLISHED, note=result['note'] or None,
            payload_digest=digest,
            unplaced={'unplaced': result['unplaced'], 'summary': result['summary']},
            created_by=actor, created_at=utc_time())).inserted_primary_key[0]
        if result['slots']:
            conn.execute(programme_slots.insert(), [{
                'version_id': version_id,
                'screening_id': slot['screening_id'],
                'film_id': slot['film_id'],
                'title': slot['title'],
                'territory': slot['territory'],
                'medium': slot['medium'],
                'starts_at': slot['starts_at'],
                'ends_at': slot['ends_at'],
                'original_starts_at': slot['original_start'],
                'original_ends_at': slot['original_end'],
                'delay_minutes': slot['delay_minutes'],
            } for slot in result['slots']])
        response = {
            'auditorium': {'id': auditorium['id'], 'code': auditorium['code'],
                           'name': auditorium['name']},
            'version': version_number,
            'published_at': utc_time(),
            'note': result['note'],
            'summary': result['summary'],
            'slots': result['slots'],
            'unplaced': result['unplaced'],
            'replayed': False,
        }
        conn.execute(publish_receipts.insert().values(
            auditorium_id=identity, idempotency_key=key, payload_digest=digest,
            version_id=version_id, response=response, created_at=utc_time()))
        log(conn, actor, 'publish_programme', version_id)
        return response
