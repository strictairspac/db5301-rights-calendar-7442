import re
from datetime import UTC, datetime, timedelta, timezone


DESK_TIMEZONE = timezone(timedelta(hours=8))
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::00)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def utc_time(seconds=0):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def parse_timestamp(value, label):
    """Parse an offset-bearing ISO 8601 minute and return it in UTC."""
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError(f"{label}须使用带时区的 ISO 8601 整分钟")
    try:
        moment = datetime.fromisoformat(value).astimezone(timezone.utc)
        moment.astimezone(DESK_TIMEZONE)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{label}的日期、时间或时区超出有效范围") from error
    return moment


def desk_time(value):
    """Render an aware datetime as an ISO minute in the festival timezone."""
    return value.astimezone(DESK_TIMEZONE).isoformat(timespec='minutes')


def desk_stamp(value, label='时间'):
    """Validate an ISO timestamp and return it normalized to UTC+08:00."""
    return desk_time(parse_timestamp(value, label))


def epoch_minute(value):
    """Whole minutes since the epoch; inputs are validated to whole minutes."""
    return int(parse_timestamp(value, '时间').timestamp() // 60)
