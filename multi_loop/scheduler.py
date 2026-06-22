"""Bounded scheduled mission ticks.

The scheduler runs one bounded generation for each mission that is due. It
borrows Hermes' unattended-job discipline:

- at-most-once: ``next_run_at`` is pre-advanced before a recurring run so a
  crash mid-generation does not re-fire the same run on the next tick.
- missed recurring runs that are past their catch-up grace window are
  fast-forwarded instead of firing a stale burst.
- run outcome (``last_status``/``last_error``) is recorded on the schedule, and
  a recurring schedule that can no longer compute its next run is surfaced as
  ``error`` rather than silently disabled.
- paused schedules are skipped without being reconsidered as due.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .leases import MissionBusy
from .models import ScheduleState
from .schedule_util import (
    advance_schedule,
    is_schedule_due,
    is_stale_recurring,
    mark_schedule_run,
)
from .storage import MissionStore

if TYPE_CHECKING:
    from .orchestrator import GenerationRunResult, MissionOrchestrator


@dataclass(slots=True)
class TickResult:
    mission_id: str
    generation_index: int
    next_run_at: str | None
    run_result: GenerationRunResult | None = None
    skipped_reason: str | None = None
    last_status: str | None = None
    error: str | None = None


@dataclass(slots=True)
class SchedulerTickReport:
    ticked: list[TickResult] = field(default_factory=list)
    skipped: list[TickResult] = field(default_factory=list)


class MissionScheduler:
    """Run one bounded generation for missions that are due."""

    def __init__(
        self,
        store: MissionStore | None = None,
        orchestrator: MissionOrchestrator | None = None,
    ) -> None:
        self.store = store or MissionStore()
        if orchestrator is None:
            from .index import MissionIndex
            from .orchestrator import MissionOrchestrator

            self.orchestrator = MissionOrchestrator(
                store=self.store, lessons_index=MissionIndex(self.store.root)
            )
        else:
            self.orchestrator = orchestrator

    def tick(self, *, now: datetime | None = None) -> SchedulerTickReport:
        current = now or datetime.now(timezone.utc)
        report = SchedulerTickReport()

        for mission in self.store.list_missions():
            schedule = mission.schedule
            if schedule is None or not schedule.enabled:
                continue

            if schedule.state == ScheduleState.PAUSED:
                report.skipped.append(self._skip(mission, schedule, "paused"))
                continue

            if not is_schedule_due(schedule, current):
                report.skipped.append(self._skip(mission, schedule, "not_due"))
                continue

            if is_stale_recurring(schedule, current):
                # Gateway was down past the catch-up window: fast-forward to the
                # next future run instead of firing a stale generation.
                advance_schedule(schedule, current)
                self.store.save_mission(mission)
                report.skipped.append(self._skip(mission, schedule, "fast_forwarded"))
                continue

            if schedule.max_generation_steps is not None and schedule.max_generation_steps <= 0:
                report.skipped.append(self._skip(mission, schedule, "max_generation_steps_reached"))
                continue

            result = self._run_due(mission.id, schedule_kind=schedule.kind, now=current)
            if result.skipped_reason:
                report.skipped.append(result)
            else:
                report.ticked.append(result)

        return report

    def _run_due(self, mission_id: str, *, schedule_kind: str | None, now: datetime) -> TickResult:
        # Pre-advance recurring schedules before running (at-most-once crash
        # safety). One-shot schedules are left untouched so they can retry.
        if schedule_kind in {"interval", "cron"}:
            mission = self.store.load_mission(mission_id)
            advance_schedule(mission.schedule, now)
            self.store.save_mission(mission)

        run_result: GenerationRunResult | None = None
        success = True
        error: str | None = None
        try:
            run_result = self.orchestrator.run_generation(mission_id)
        except MissionBusy:
            # Another runner (CLI/MCP/overlapping tick) holds the mission. Skip
            # this cycle; that runner advances the mission, and the schedule was
            # already pre-advanced so the next tick lands in the future.
            mission = self.store.load_mission(mission_id)
            return self._skip(mission, mission.schedule, "already_running")
        except Exception as exc:  # isolate one mission's failure from the tick loop
            success = False
            error = f"{type(exc).__name__}: {exc}"

        refreshed = self.store.load_mission(mission_id)
        schedule = refreshed.schedule
        if schedule is not None:
            mark_schedule_run(schedule, success=success, now=now, error=error)
            self._consume_step_budget(schedule)
            self.store.save_mission(refreshed)

        return TickResult(
            mission_id=mission_id,
            generation_index=run_result.generation_index if run_result else len(refreshed.generations),
            next_run_at=schedule.next_run_at if schedule else None,
            run_result=run_result,
            last_status=schedule.last_status if schedule else None,
            error=error,
        )

    @staticmethod
    def _consume_step_budget(schedule) -> None:
        """Decrement the generation-step budget and disable once exhausted.

        Budget is consumed on both success and failure so a repeatedly failing
        mission cannot loop forever against a finite step budget.
        """
        if schedule.max_generation_steps is None:
            return
        schedule.max_generation_steps -= 1
        if schedule.max_generation_steps <= 0:
            schedule.enabled = False
            schedule.state = ScheduleState.COMPLETED

    @staticmethod
    def _skip(mission, schedule, reason: str) -> TickResult:
        return TickResult(
            mission_id=mission.id,
            generation_index=len(mission.generations),
            next_run_at=schedule.next_run_at,
            skipped_reason=reason,
            last_status=schedule.last_status,
        )
