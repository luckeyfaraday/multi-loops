"""Bounded scheduled mission ticks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .schedule_util import advance_schedule, is_schedule_due
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
            from .orchestrator import MissionOrchestrator

            self.orchestrator = MissionOrchestrator(store=self.store)
        else:
            self.orchestrator = orchestrator

    def tick(self, *, now: datetime | None = None) -> SchedulerTickReport:
        current = now or datetime.now(timezone.utc)
        report = SchedulerTickReport()

        for mission in self.store.list_missions():
            schedule = mission.schedule
            if schedule is None or not schedule.enabled:
                continue

            if not is_schedule_due(schedule, current):
                report.skipped.append(
                    TickResult(
                        mission_id=mission.id,
                        generation_index=len(mission.generations),
                        next_run_at=schedule.next_run_at,
                        skipped_reason="not_due",
                    )
                )
                continue

            if schedule.max_generation_steps <= 0:
                report.skipped.append(
                    TickResult(
                        mission_id=mission.id,
                        generation_index=len(mission.generations),
                        next_run_at=schedule.next_run_at,
                        skipped_reason="max_generation_steps_reached",
                    )
                )
                continue

            result = self.orchestrator.run_generation(mission.id)
            refreshed = self.store.load_mission(mission.id)
            refreshed.schedule = advance_schedule(refreshed.schedule, current)
            self.store.save_mission(refreshed)

            report.ticked.append(
                TickResult(
                    mission_id=mission.id,
                    generation_index=result.generation_index,
                    next_run_at=refreshed.schedule.next_run_at if refreshed.schedule else None,
                    run_result=result,
                )
            )

        return report
