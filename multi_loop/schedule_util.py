"""Schedule parsing helpers shared by orchestrator and scheduler."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import MissionSchedule

_SCHEDULE_RE = re.compile(r"^every\s+(\d+)\s*([hdm])$", re.IGNORECASE)
_UNIT_TO_SECONDS = {"h": 3600, "d": 86400, "m": 60}


def initialize_schedule(schedule: MissionSchedule, *, now: datetime | None = None) -> MissionSchedule:
    """Ensure a schedule has a next run time."""
    current = now or datetime.now(timezone.utc)
    if schedule.next_run_at is None:
        schedule.next_run_at = current.isoformat()
    return schedule


def is_schedule_due(schedule: MissionSchedule, now: datetime) -> bool:
    if schedule.next_run_at is None:
        return True
    next_run = datetime.fromisoformat(schedule.next_run_at)
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return next_run <= now


def advance_schedule(schedule: MissionSchedule | None, now: datetime) -> MissionSchedule | None:
    if schedule is None:
        return None
    delta = parse_schedule_delta(schedule.expression)
    if delta is None:
        schedule.next_run_at = None
        schedule.enabled = False
        return schedule
    schedule.next_run_at = (now + delta).isoformat()
    return schedule


def parse_schedule_delta(expression: str) -> timedelta | None:
    match = _SCHEDULE_RE.match(expression.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = _UNIT_TO_SECONDS.get(unit)
    if seconds is None or amount <= 0:
        return None
    return timedelta(seconds=amount * seconds)