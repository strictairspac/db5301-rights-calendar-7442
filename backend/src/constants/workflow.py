from enum import StrEnum


class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class BookingStatus(StrEnum):
    CONFIRMED = 'confirmed'
    CANCELLED = 'cancelled'


class ProgrammeStatus(StrEnum):
    PUBLISHED = 'published'
    SUPERSEDED = 'superseded'

