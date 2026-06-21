"""Schedule parsing and advancement for unattended missions.

Adapted from Hermes' cron job model (`cron/jobs.py`), trimmed to multi-loop's
needs. Supports three schedule kinds:

- one-shot:  ``30m``, ``2h``, ``1d`` (duration from now) or an ISO timestamp.
- interval:  ``every 30m``, ``every 2h`` (recurring).
- cron:      ``0 9 * * *`` (requires the optional ``croniter`` package).

The module stays dependency-free for one-shot and interval schedules; cron is
only available when ``croniter`` is installed, and a cron schedule that cannot
compute its next run marks the schedule as ``error`` rather than silently
disabling it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import MissionSchedule, ScheduleState

try:  # cron support is optional; everything else works without it.
    from croniter import croniter

    HAS_CRONITER = True
except ImportError:  # pragma: no cover - exercised only when croniter is absent
    croniter = None  # type: ignore[assignment]
    HAS_CRONITER = False

_DURATION_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440}

# Bounds for how late a recurring run may be and still catch up rather than
# fast-forward: half the period, clamped to [2 minutes, 2 hours].
_MIN_GRACE_SECONDS = 120
_MAX_GRACE_SECONDS = 7200


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """Interpret naive timestamps as UTC so comparisons never raise."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_duration(text: str) -> int:
    """Parse a duration string like ``30m``/``2h``/``1d`` into minutes."""
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise ValueError(f"Invalid duration: {text!r}. Use a form like '30m', '2h', or '1d'.")
    amount = int(match.group(1))
    unit = match.group(2)[0].lower()
    if amount <= 0:
        raise ValueError(f"Duration must be positive: {text!r}.")
    return amount * _UNIT_MINUTES[unit]


def parse_schedule(expression: str) -> dict[str, Any]:
    """Parse a schedule expression into a structured descriptor.

    Returns a dict with ``kind`` (``once``/``interval``/``cron``), the kind's
    payload (``minutes``/``run_at``/``expr``), and a human-readable ``display``.
    Raises ``ValueError`` for anything that is not a recognized schedule.
    """
    text = expression.strip()
    lowered = text.lower()

    if lowered.startswith("every "):
        minutes = parse_duration(text[6:].strip())
        return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}

    parts = text.split()
    if len(parts) >= 5 and all(re.match(r"^[\d*\-,/]+$", part) for part in parts[:5]):
        if not HAS_CRONITER:
            raise ValueError(
                "Cron schedules require the optional 'croniter' package "
                "(pip install croniter)."
            )
        try:
            croniter(text)
        except Exception as exc:  # noqa: BLE001 - surface a clear schedule error
            raise ValueError(f"Invalid cron expression {text!r}: {exc}") from exc
        return {"kind": "cron", "expr": text, "display": text}

    if "T" in text or re.match(r"^\d{4}-\d{2}-\d{2}", text):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp {text!r}: {exc}") from exc
        dt = _ensure_aware(dt)
        return {
            "kind": "once",
            "run_at": dt.isoformat(),
            "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}",
        }

    minutes = parse_duration(text)  # raises ValueError if unrecognized
    return {"kind": "once", "minutes": minutes, "display": f"once in {text}"}


def _grace_seconds(parsed: dict[str, Any]) -> int:
    """How late a recurring run may be and still catch up instead of skipping."""
    kind = parsed.get("kind")
    if kind == "interval":
        period = parsed.get("minutes", 1) * 60
    elif kind == "cron" and HAS_CRONITER:
        try:
            base = _now()
            cron = croniter(parsed["expr"], base)
            first = cron.get_next(datetime)
            second = cron.get_next(datetime)
            period = int((second - first).total_seconds())
        except Exception:  # noqa: BLE001 - fall back to the minimum grace
            return _MIN_GRACE_SECONDS
    else:
        return _MIN_GRACE_SECONDS
    return max(_MIN_GRACE_SECONDS, min(period // 2, _MAX_GRACE_SECONDS))


def compute_next_run(
    expression: str,
    *,
    now: datetime | None = None,
    last_run_at: str | None = None,
) -> str | None:
    """Compute the next run time (ISO string) for ``expression``.

    Returns ``None`` when a one-shot schedule has already fired, or when a cron
    schedule cannot be computed (callers treat the latter as an error for
    recurring schedules rather than completion).
    """
    current = _now(now)
    parsed = parse_schedule(expression)
    kind = parsed["kind"]

    if kind == "once":
        if last_run_at:
            return None  # one-shot already fired
        if "run_at" in parsed:
            return _ensure_aware(datetime.fromisoformat(parsed["run_at"])).isoformat()
        return (current + timedelta(minutes=parsed["minutes"])).isoformat()

    if kind == "interval":
        anchor = (
            _ensure_aware(datetime.fromisoformat(last_run_at)) if last_run_at else current
        )
        return (anchor + timedelta(minutes=parsed["minutes"])).isoformat()

    if kind == "cron":
        if not HAS_CRONITER:
            return None
        base = _ensure_aware(datetime.fromisoformat(last_run_at)) if last_run_at else current
        return croniter(parsed["expr"], base).get_next(datetime).isoformat()

    return None


def initialize_schedule(schedule: MissionSchedule, *, now: datetime | None = None) -> MissionSchedule:
    """Validate the expression and stamp derived fields onto a new schedule."""
    parsed = parse_schedule(schedule.expression)  # raises ValueError if invalid
    schedule.kind = parsed["kind"]
    schedule.display = parsed["display"]
    if schedule.next_run_at is None:
        if parsed["kind"] == "once":
            # A one-shot fires at its target time (timestamp or now + duration).
            schedule.next_run_at = compute_next_run(schedule.expression, now=now)
        else:
            # Recurring schedules fire on the next due tick at/after creation;
            # later runs anchor to the actual last run via ``mark_schedule_run``.
            schedule.next_run_at = _now(now).isoformat()
    return schedule


def is_schedule_due(schedule: MissionSchedule, now: datetime) -> bool:
    if schedule.next_run_at is None:
        return True
    return _ensure_aware(datetime.fromisoformat(schedule.next_run_at)) <= _ensure_aware(now)


def is_stale_recurring(schedule: MissionSchedule, now: datetime) -> bool:
    """Whether a recurring run is so late it should fast-forward, not fire."""
    if schedule.kind not in {"interval", "cron"} or schedule.next_run_at is None:
        return False
    next_run = _ensure_aware(datetime.fromisoformat(schedule.next_run_at))
    parsed = parse_schedule(schedule.expression)
    return (_ensure_aware(now) - next_run).total_seconds() > _grace_seconds(parsed)


def advance_schedule(schedule: MissionSchedule | None, now: datetime) -> MissionSchedule | None:
    """Pre-advance ``next_run_at`` before a run (at-most-once crash safety).

    Only recurring schedules are advanced; one-shot schedules are left untouched
    so they can retry after a crash. A recurring schedule whose next run cannot
    be computed (e.g. cron without ``croniter``) is marked ``error`` rather than
    disabled, so the user's schedule does not silently stop.
    """
    if schedule is None:
        return None
    if schedule.kind not in {"interval", "cron"}:
        return schedule
    next_run = compute_next_run(schedule.expression, now=now)
    if next_run is None:
        schedule.state = ScheduleState.ERROR
        if not schedule.last_error:
            schedule.last_error = "Could not compute next run for recurring schedule."
        return schedule
    schedule.next_run_at = next_run
    return schedule


def mark_schedule_run(
    schedule: MissionSchedule,
    *,
    success: bool,
    now: datetime,
    error: str | None = None,
    delivery_error: str | None = None,
) -> MissionSchedule:
    """Record a completed run and compute the schedule's next state."""
    run_at = _ensure_aware(now).isoformat()
    schedule.last_run_at = run_at
    schedule.last_status = "ok" if success else "error"
    schedule.last_error = None if success else error
    schedule.last_delivery_error = delivery_error

    next_run = compute_next_run(schedule.expression, now=now, last_run_at=run_at)
    if next_run is None:
        if schedule.kind in {"interval", "cron"}:
            # Recurring schedule that cannot recompute: surface as error, keep enabled.
            schedule.state = ScheduleState.ERROR
            if not schedule.last_error:
                schedule.last_error = "Could not compute next run for recurring schedule."
        else:
            # One-shot completed: disable so it is no longer reconsidered.
            schedule.enabled = False
            schedule.state = ScheduleState.COMPLETED
            schedule.next_run_at = None
        return schedule

    schedule.next_run_at = next_run
    if schedule.state != ScheduleState.PAUSED:
        schedule.state = ScheduleState.SCHEDULED
    return schedule
