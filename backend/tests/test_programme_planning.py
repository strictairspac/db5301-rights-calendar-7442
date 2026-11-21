"""Planner invariants that do not need the database."""

import itertools

import pytest

from src.services.programme import (_allowed_starts, _desk, _minute, _optimize,
                                    plan, read_draft)

AUDITORIUM = {"id": 1, "code": "T-1", "name": "测试影厅",
              "territory": "上海", "medium": "影院"}


def screening(sid, start, duration, film="F-01"):
    return {"screening_id": sid, "film_id": film, "title": f"片 {sid}",
            "territory": "上海", "medium": "影院",
            "starts_at": start, "ends_at": _desk(_minute(start, 'x') + duration)}


def right(rid, start, end, film="F-01", territory="上海", medium="影院"):
    return {"right_id": rid, "film_id": film, "territory": territory,
            "medium": medium, "starts_at": start, "ends_at": end}


def draft(screenings, rights=(), max_delay=60, note=None):
    payload = {"max_delay_minutes": max_delay, "screenings": list(screenings),
               "rights": list(rights)}
    if note is not None:
        payload["note"] = note
    return payload


def build_items(payload):
    from src.services.programme import _allowed_starts, read_draft
    aud = AUDITORIUM
    screenings, rights, max_delay, _ = read_draft(payload, aud)
    return [dict(item, allowed=_allowed_starts(item, rights, max_delay, []))
            for item in screenings]


def check_schedule(items, choice):
    """Assert the chosen placements are authorized, ordered and within delay."""
    previous_end = None
    for index in sorted(choice, key=lambda i: choice[i]):
        item = items[index]
        start = choice[index]
        assert any(lo <= start <= hi for lo, hi in item["allowed"])
        assert start >= item["start"]
        if previous_end is not None:
            assert start >= previous_end
        previous_end = start + item["duration"]


def brute_force(items):
    """Best (placed, -delay) by trying every subset and every order."""
    best = (0, 0)
    count = len(items)
    for size in range(count, 0, -1):
        for subset in itertools.combinations(range(count), size):
            found = False
            for order in itertools.permutations(subset):
                if _fits(items, order):
                    found = True
                    break
            if found:
                delay = min(_delay(items, order) for order in
                            itertools.permutations(subset) if _fits(items, order))
                return size, delay
    return best


def _fits(items, order):
    cursor = 0
    for index in order:
        item = items[index]
        start = next((max(lo, cursor) for lo, hi in item["allowed"]
                      if max(lo, cursor) <= hi), None)
        if start is None:
            return False
        cursor = start + item["duration"]
    return True


def _delay(items, order):
    cursor = 0
    total = 0
    for index in order:
        item = items[index]
        start = next(max(lo, cursor) for lo, hi in item["allowed"] if max(lo, cursor) <= hi)
        total += start - item["start"]
        cursor = start + item["duration"]
    return total


def test_delay_never_starts_before_original():
    payload = draft([screening("A", "2026-12-04T19:00+08:00", 60)],
                    [right("R", "2026-12-04T17:00+08:00", "2026-12-04T22:00+08:00")],
                    max_delay=30)
    items = build_items(payload)
    choice, optimal = _optimize(items)
    assert optimal is True
    assert choice == {0: items[0]["start"]}


def test_rights_wildcards_and_merging_cover_whole_run():
    payload = draft(
        [screening("A", "2026-12-04T19:00+08:00", 60)],
        [right("R1", "2026-12-04T18:30+08:00", "2026-12-04T19:30+08:00", territory="*"),
         right("R2", "2026-12-04T19:30+08:00", "2026-12-04T20:30+08:00", medium="*")])
    items = build_items(payload)
    assert any(lo <= items[0]["start"] <= hi for lo, hi in items[0]["allowed"])


def test_other_territory_and_medium_do_not_grant_rights():
    payload = draft(
        [screening("A", "2026-12-04T19:00+08:00", 60)],
        [right("R1", "2026-12-04T17:00+08:00", "2026-12-04T23:00+08:00", territory="杭州"),
         right("R2", "2026-12-04T17:00+08:00", "2026-12-04T23:00+08:00", medium="线上")])
    items = build_items(payload)
    assert items[0]["allowed"] == []
    assert _optimize(items)[0] == {}


def test_booking_blocks_starts_that_would_overlap():
    payload = draft([screening("A", "2026-12-04T19:00+08:00", 60)],
                    [right("R", "2026-12-04T19:00+08:00", "2026-12-04T21:00+08:00")],
                    max_delay=60)
    screenings, rights, max_delay, _ = read_draft(payload, AUDITORIUM)
    booking = [{"start": _minute("2026-12-04T19:30+08:00", 'x'),
                "end": _minute("2026-12-04T20:00+08:00", 'x')}]
    allowed = _allowed_starts(screenings[0], rights, max_delay, booking)
    starts = [s for lo, hi in allowed for s in range(lo, hi + 1)]
    assert all(not (s < booking[0]["end"] and s + 60 > booking[0]["start"])
               for s in starts)


def test_planner_keeps_the_most_and_then_minimises_delay():
    payload = draft(
        [screening("A", "2026-12-04T19:00+08:00", 60),
         screening("B", "2026-12-04T20:00+08:00", 60, film="F-02"),
         screening("C", "2026-12-04T21:00+08:00", 60, film="F-03")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00", film="F-01"),
         right("R2", "2026-12-04T20:00+08:00", "2026-12-04T21:00+08:00", film="F-02"),
         right("R3", "2026-12-04T21:00+08:00", "2026-12-04T22:00+08:00", film="F-03")],
        max_delay=0)
    result = plan(payload, AUDITORIUM, [])
    assert result["summary"]["排入场次"] == 3
    assert result["summary"]["顺延分钟"] == 0
    assert result["unplaced"] == []


def test_planner_drops_the_screening_that_cannot_fit():
    payload = draft(
        [screening("A", "2026-12-04T19:00+08:00", 60),
         screening("B", "2026-12-04T19:30+08:00", 60, film="F-99")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00", film="F-01")],
        max_delay=0)
    result = plan(payload, AUDITORIUM, [])
    assert [row["screening_id"] for row in result["slots"]] == ["A"]
    assert [row["screening_id"] for row in result["unplaced"]] == ["B"]
    assert "授权" in result["unplaced"][0]["reason"]


def test_schedule_orders_by_shifted_start_not_original_order():
    payload = draft(
        [screening("LATE", "2026-12-04T20:00+08:00", 30, film="F-02"),
         screening("EARLY", "2026-12-04T19:00+08:00", 60, film="F-01")],
        [right("R1", "2026-12-04T19:00+08:00", "2026-12-04T20:00+08:00", film="F-01"),
         right("R2", "2026-12-04T20:00+08:00", "2026-12-04T21:00+08:00", film="F-02")])
    result = plan(payload, AUDITORIUM, [])
    assert [row["screening_id"] for row in result["slots"]] == ["EARLY", "LATE"]


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_branch_and_bound_matches_exhaustive_search(size):
    import random
    random.seed(size)
    payloads = []
    films = ["F-01", "F-02", "F-03"]
    for _ in range(8):
        shift = random.choice([0, 15, 30])
        windows = []
        cursor = _minute("2026-12-04T19:00+08:00", 'x')
        for n in range(3):
            length = random.choice([30, 45, 60])
            windows.append((cursor, cursor + length))
            cursor += length + random.choice([0, 10, 20])
        rights = [right(f"R{n}", _desk(start), _desk(end), film=random.choice(films))
                  for n, (start, end) in enumerate(windows)]
        screenings = [screening(f"S{n}", _desk(_minute("2026-12-04T19:00+08:00", 'x')
                                               + shift * 10 * n),
                                random.choice([30, 45, 60]), film=random.choice(films))
                      for n in range(size)]
        payloads.append(draft(screenings, rights,
                              max_delay=random.choice([0, 30, 60])))
    for payload in payloads:
        items = build_items(payload)
        choice, optimal = _optimize(items)
        assert optimal
        check_schedule(items, choice)
        placed, delay = brute_force(items)
        assert len(choice) == placed
        assert sum(choice[i] - items[i]["start"] for i in choice) == delay


def test_invalid_draft_reports_the_offending_line():
    payload = draft([screening("A", "2026-12-04T19:00+08:00", 60),
                     dict(screening("B", "2026-12-04T20:00+08:00", 60), territory="杭州")])
    with pytest.raises(ValueError, match="第 2 行"):
        read_draft(payload, AUDITORIUM)


def test_duplicate_screening_ids_are_rejected():
    payload = draft([screening("A", "2026-12-04T19:00+08:00", 60),
                     screening("A", "2026-12-04T21:00+08:00", 60)])
    with pytest.raises(ValueError, match="重复"):
        read_draft(payload, AUDITORIUM)


def test_negative_delay_limit_is_rejected():
    payload = draft([screening("A", "2026-12-04T19:00+08:00", 60)], max_delay=-1)
    with pytest.raises(ValueError, match="顺延分钟"):
        read_draft(payload, AUDITORIUM)


def test_empty_draft_is_valid_and_plans_nothing():
    result = plan({"max_delay_minutes": 30, "screenings": [], "rights": []},
                  AUDITORIUM, [])
    assert result["slots"] == [] and result["unplaced"] == []
