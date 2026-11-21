"""Rights review and release decisions for 潮幕影展."""

from datetime import datetime, timedelta, timezone
from json import dumps
import re

from sqlalchemy import text

from .rights_sql import ANALYZE_SCREENINGS


DESK_TIMEZONE = timezone(timedelta(hours=8))
SCOPE_FIELDS = ("film_id", "territory", "medium")
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::00)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)

SPEC = {
    "name": "潮幕影展·放映授权排期",
    "summary": "逐场核对放映权益，给出保留排期、建议顺延或暂停发布的决策及补权时段。",
    "customer": "潮幕影展排片组与版权组",
    "entity_labels": ["影片台账", "排片校核", "缺口单"],
    "field_labels": {
        "max_delay_minutes": "最多顺延分钟",
        "screenings": "待校核排片",
        "rights": "已生效授权窗口",
        "screening_id": "场次编号",
        "right_id": "授权编号",
        "film_id": "影片编号",
        "title": "片名",
        "territory": "地域",
        "medium": "媒介",
        "starts_at": "开始时间",
        "ends_at": "结束时间",
    },
    "guidance": (
        "导入排片及已生效的授权。时间使用带时区的 ISO 8601 整分钟，结果按 UTC+08:00 显示。"
        "授权的地域或媒介可填 * 表示全部。顺延时长按开场时间计算，候选时段须复核场地和平台档期。"
    ),
    "sample": {
        "max_delay_minutes": 45,
        "screenings": [
            dict(screening_id="P-101", film_id="F-01", title="潮线以北", territory="上海",
                 medium="影院", starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T20:30+08:00"),
            dict(screening_id="P-102", film_id="F-02", title="雾港来信", territory="上海",
                 medium="影院", starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(screening_id="P-103", film_id="F-03", title="白鹭返航", territory="上海",
                 medium="线上", starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(screening_id="P-104", film_id="F-03", title="白鹭返航", territory="杭州",
                 medium="线上", starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
        ],
        "rights": [
            dict(right_id="R-01", film_id="F-01", territory="上海", medium="影院",
                 starts_at="2026-12-04T18:55+08:00", ends_at="2026-12-04T19:35+08:00"),
            dict(right_id="R-02", film_id="F-01", territory="上海", medium="影院",
                 starts_at="2026-12-04T19:10+08:00", ends_at="2026-12-04T19:15+08:00"),
            dict(right_id="R-03", film_id="F-01", territory="*", medium="影院",
                 starts_at="2026-12-04T19:30+08:00", ends_at="2026-12-04T20:00+08:00"),
            dict(right_id="R-04", film_id="F-01", territory="上海", medium="*",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T20:30+08:00"),
            dict(right_id="R-05", film_id="F-02", territory="上海", medium="影院",
                 starts_at="2026-12-04T19:50+08:00", ends_at="2026-12-04T20:20+08:00"),
            dict(right_id="R-06", film_id="F-02", territory="上海", medium="影院",
                 starts_at="2026-12-04T20:30+08:00", ends_at="2026-12-04T21:45+08:00"),
            dict(right_id="R-07", film_id="F-03", territory="上海", medium="线上",
                 starts_at="2026-12-04T20:10+08:00", ends_at="2026-12-04T20:40+08:00"),
            dict(right_id="R-08", film_id="F-03", territory="杭州", medium="线上",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(right_id="R-09", film_id="F-03", territory="上海", medium="影院",
                 starts_at="2026-12-04T19:45+08:00", ends_at="2026-12-04T21:15+08:00"),
        ],
    },
    "expect": {
        "排片场次": 4,
        "保留排期场次": 2,
        "建议顺延场次": 1,
        "暂停发布场次": 1,
        "未授权分钟": 40,
        "缺口段数": 3,
    },
}


def _parse_time(value, label):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError(f"{label}须使用带时区的 ISO 8601 整分钟")
    try:
        utc_time = datetime.fromisoformat(value).astimezone(timezone.utc)
        utc_time.astimezone(DESK_TIMEZONE)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{label}的日期、时间或时区超出有效范围") from error
    return utc_time


def _read_rows(payload, key, id_field, extra_fields=()):
    labels = SPEC["field_labels"]
    records = payload.get(key)
    if not isinstance(records, list):
        raise ValueError(f"{labels[key]}须为列表")
    normalized = []
    identifiers = set()
    for number, record in enumerate(records, start=1):
        label = f"{labels[key]}第 {number} 行"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        row = {}
        for field in (id_field, *SCOPE_FIELDS, *extra_fields):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError(f"{label}的{labels[field]}须为有效非空文本")
            row[field] = value.strip()
        identifier = row[id_field]
        if identifier in identifiers:
            raise ValueError(f"{label}的{labels[id_field]}重复（{identifier}）")
        identifiers.add(identifier)
        if key == "screenings" and "*" in (row["territory"], row["medium"]):
            raise ValueError(f"{label}须填写具体地域和媒介")
        start = _parse_time(record.get("starts_at"), f"{label}开始时间")
        end = _parse_time(record.get("ends_at"), f"{label}结束时间")
        if end <= start:
            raise ValueError(f"{label}的结束时间须晚于开始时间")
        row.update(starts_at=start.isoformat(timespec="minutes"),
                   ends_at=end.isoformat(timespec="minutes"))
        normalized.append(row)
    return normalized


def _read_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("校核输入须为对象")
    max_delay = payload.get("max_delay_minutes")
    if type(max_delay) is not int or max_delay < 0:
        raise ValueError("最多顺延分钟须为非负整数")
    screenings = _read_rows(payload, "screenings", "screening_id", ("title",))
    rights = _read_rows(payload, "rights", "right_id")
    return screenings, rights, max_delay


def validate(payload):
    """Raise ValueError for invalid input before the database query."""
    _read_payload(payload)


def _desk_time(value):
    return value.astimezone(DESK_TIMEZONE).isoformat(timespec="minutes")


def _screening_result(row, identity):
    missing = row["unauthorized_minutes"]
    proposed_start = None
    proposed_end = None
    if missing == 0:
        decision = "保留排期"
        action = "按原排期提交发布审核"
    elif row["proposed_start"] is not None:
        decision = "建议顺延"
        action = "确认新时段的场地或平台档期后改签"
        proposed_start = _desk_time(row["proposed_start"])
        proposed_end = _desk_time(row["proposed_end"])
    else:
        decision = "暂停发布"
        action = "按缺口单申请补权或另排"
    return {
        **identity,
        "原开始": _desk_time(row["starts_at"]),
        "原结束": _desk_time(row["ends_at"]),
        "放映分钟": row["screening_minutes"],
        "已授权分钟": row["screening_minutes"] - missing,
        "未授权分钟": missing,
        "编辑决策": decision,
        "建议开始": proposed_start,
        "建议结束": proposed_end,
        "编辑动作": action,
    }


def solve(payload, engine):
    """Return original-slot gaps and the earliest rights-feasible delay for each screening."""
    screenings, rights, max_delay = _read_payload(payload)
    decisions = {}
    gaps = []
    metrics = dict.fromkeys(SPEC["expect"], 0)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        rows = conn.execute(ANALYZE_SCREENINGS, {
            "screenings": dumps(screenings, ensure_ascii=False),
            "rights": dumps(rights, ensure_ascii=False),
            "max_delay_minutes": max_delay,
        }).mappings()
        for row in rows:
            identity = {
                "场次编号": row["screening_id"],
                "影片编号": row["film_id"],
                "片名": row["title"],
                "地域": row["territory"],
                "媒介": row["medium"],
            }
            if row["screening_id"] not in decisions:
                result = _screening_result(row, identity)
                decisions[row["screening_id"]] = result
                metrics[f"{result['编辑决策']}场次"] += 1
                metrics["未授权分钟"] += result["未授权分钟"]
            if row["gap_start"] is not None:
                gaps.append({
                    **identity,
                    "缺口开始": _desk_time(row["gap_start"]),
                    "缺口结束": _desk_time(row["gap_end"]),
                    "未授权分钟": row["gap_minutes"],
                })

    metrics["排片场次"] = len(decisions)
    metrics["缺口段数"] = len(gaps)
    return {
        "summary": (
            f"校核 {metrics['排片场次']} 场，保留 {metrics['保留排期场次']} 场，"
            f"建议顺延 {metrics['建议顺延场次']} 场，暂停发布 {metrics['暂停发布场次']} 场。"
            f"原排期有 {metrics['缺口段数']} 处授权缺口，共 {metrics['未授权分钟']} 分钟。"
        ),
        "metrics": metrics,
        "sections": [
            {"title": "排片校核与编辑决策", "rows": list(decisions.values())},
            {"title": "原排片权益缺口单", "rows": gaps},
        ],
    }
