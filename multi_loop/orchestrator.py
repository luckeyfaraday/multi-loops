"""Deterministic outer mission orchestrator."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import CapabilityRegistry, default_capabilities
from .models import (
    CandidateLoop,
    CandidateState,
    Event,
    FitnessScore,
    Generation,
    LedgerEntry,
    Mission,
    MissionSchedule,
    ScheduleState,
    utc_now_iso,
)
from .leases import acquire_mission_lease
from .planning import FitnessReviewer, HeuristicPortfolioPlanner
from .policy import prepare_candidate
from .runners import RunRequest, RunResult, RunnerRegistry, default_runner_registry, run_result_to_dict
from .schedule_util import compute_next_run, initialize_schedule
from .storage import MissionNotFound, MissionStore
from .verification import run_verification


@dataclass(slots=True)
class GenerationRunResult:
    mission_id: str
    generation_index: int
    selected_loop_ids: list[str]
    synthesis: str
    events_written: int
    ledger_entries_written: int
    result_paths: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)
    blocked_candidates: list[str] = field(default_factory=list)


class MissionOrchestrator:
    """Run bounded multi-loop mission generations."""

    def __init__(
        self,
        store: MissionStore | None = None,
        runners: RunnerRegistry | None = None,
        capabilities: CapabilityRegistry | None = None,
        planner: HeuristicPortfolioPlanner | None = None,
        reviewer: FitnessReviewer | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self.store = store or MissionStore()
        self.runners = runners or default_runner_registry()
        self.capabilities = capabilities or default_capabilities()
        self.planner = planner or HeuristicPortfolioPlanner(self.capabilities)
        self.reviewer = reviewer or FitnessReviewer()
        self.workspace = Path(workspace).resolve() if workspace else None

    def create_mission(
        self,
        statement: str,
        success_criteria: str,
        *,
        schedule: str | None = None,
        clarifications: dict[str, str] | None = None,
        approvals: dict[str, str] | None = None,
    ) -> Mission:
        mission_schedule = MissionSchedule(expression=schedule) if schedule else None
        if mission_schedule is not None:
            mission_schedule = initialize_schedule(mission_schedule)

        mission = Mission(
            statement=statement,
            success_criteria=success_criteria,
            clarifications=clarifications or {},
            approvals=approvals or {},
            schedule=mission_schedule,
        )
        self.store.create_mission(mission)
        self._append_event(
            mission,
            "mission_created",
            {"statement": statement, "clarifications": mission.clarifications},
        )
        entry = LedgerEntry(
            mission_id=mission.id,
            event_type="mission_created",
            summary=f"Mission created: {statement}",
        )
        self._append_ledger(mission, entry)
        self.store.save_mission(mission)
        return mission

    def approve_capability(self, mission_id: str, capability: str, *, approved_by: str) -> Mission:
        mission = self.store.load_mission(mission_id)
        mission.approvals[capability] = approved_by
        self.store.save_mission(mission)
        self._append_event(
            mission,
            "capability_approved",
            {"capability": capability, "approved_by": approved_by},
        )
        entry = LedgerEntry(
            mission_id=mission.id,
            event_type="capability_approved",
            summary=f"Approved capability {capability} for {approved_by}",
        )
        self._append_ledger(mission, entry)
        self.store.save_mission(mission)
        return mission

    def pause_schedule(self, mission_id: str, *, reason: str | None = None) -> Mission:
        """Pause a mission's schedule so it is skipped by ticks until resumed.

        Pausing leaves ``enabled`` set and uses the ``PAUSED`` state so the
        mission stays visible (and resumable) in tick reports; ``enabled=False``
        is reserved for terminal schedules (completed or step-budget exhausted).
        """
        mission = self.store.load_mission(mission_id)
        schedule = _require_schedule(mission)
        schedule.state = ScheduleState.PAUSED
        schedule.paused_at = utc_now_iso()
        schedule.paused_reason = reason
        self._record_schedule_event(mission, "schedule_paused", {"reason": reason})
        self.store.save_mission(mission)
        return mission

    def resume_schedule(self, mission_id: str, *, now: datetime | None = None) -> Mission:
        """Resume a paused schedule and recompute its next future run."""
        mission = self.store.load_mission(mission_id)
        schedule = _require_schedule(mission)
        schedule.enabled = True
        schedule.state = ScheduleState.SCHEDULED
        schedule.paused_at = None
        schedule.paused_reason = None
        current = now or datetime.now(timezone.utc)
        schedule.next_run_at = compute_next_run(
            schedule.expression, now=current, last_run_at=schedule.last_run_at
        ) or current.isoformat()
        self._record_schedule_event(mission, "schedule_resumed", {"next_run_at": schedule.next_run_at})
        self.store.save_mission(mission)
        return mission

    def trigger_schedule(self, mission_id: str) -> Mission:
        """Mark a schedule due now so the next tick runs a generation."""
        mission = self.store.load_mission(mission_id)
        schedule = _require_schedule(mission)
        schedule.enabled = True
        if schedule.state in {ScheduleState.PAUSED, ScheduleState.ERROR}:
            schedule.state = ScheduleState.SCHEDULED
        schedule.next_run_at = utc_now_iso()
        self._record_schedule_event(mission, "schedule_triggered", {"next_run_at": schedule.next_run_at})
        self.store.save_mission(mission)
        return mission

    def _record_schedule_event(self, mission: Mission, event_type: str, data: dict[str, object]) -> None:
        self._append_event(mission, event_type, data)
        self._append_ledger(
            mission,
            LedgerEntry(mission_id=mission.id, event_type=event_type, summary=f"{event_type}: {mission.id}"),
        )

    def run_generation(
        self,
        mission_id: str,
        *,
        runner_name: str | None = None,
        verify_timeout_seconds: float | None = None,
    ) -> GenerationRunResult:
        """Run one generation under an exclusive mission lease.

        Raises ``MissionBusy`` if another runner (CLI, MCP, scheduler) already
        holds the mission, so concurrent callers skip rather than producing a
        duplicate generation index.
        """
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            return self._run_generation_locked(
                mission_id,
                runner_name=runner_name,
                verify_timeout_seconds=verify_timeout_seconds,
            )

    def _run_generation_locked(
        self,
        mission_id: str,
        *,
        runner_name: str | None = None,
        verify_timeout_seconds: float | None = None,
    ) -> GenerationRunResult:
        mission = self.store.load_mission(mission_id)
        generation_index = len(mission.generations)
        portfolio = self.planner.plan(mission, generation_index)
        candidates = portfolio.candidates
        if runner_name:
            for candidate in candidates:
                candidate.runner = runner_name

        generation = Generation(
            index=generation_index,
            candidate_loops=candidates,
            mutations=portfolio.mutations,
        )
        mission.generations.append(generation)
        self.store.save_mission(mission)

        events_written = 0
        ledger_entries_written = 0
        result_paths: list[str] = []
        blocked_candidates: list[str] = []
        self._append_event(
            mission,
            "generation_started",
            {"candidate_count": len(candidates), "mutations": portfolio.mutations},
            generation_index,
        )
        events_written += 1

        for candidate in candidates:
            self._append_event(
                mission,
                "candidate_started",
                {"goal": candidate.goal, "runner": candidate.runner},
                generation_index,
                candidate.id,
            )
            events_written += 1

            blocked_reason = prepare_candidate(candidate, mission, self.capabilities)
            if blocked_reason:
                blocked_candidates.append(candidate.id)
                result = RunResult(
                    candidate_loop_id=candidate.id,
                    success=False,
                    summary=blocked_reason,
                    metadata={"blocked_by_policy": True},
                )
            else:
                try:
                    result = self._run_candidate(mission, generation_index, candidate)
                    self._apply_verification(mission, candidate, result, verify_timeout_seconds)
                except Exception as exc:  # isolate one candidate's crash from the generation
                    result = RunResult(
                        candidate_loop_id=candidate.id,
                        success=False,
                        summary=f"Candidate raised an unexpected error: {exc}",
                        metadata={"error": type(exc).__name__},
                    )

            if blocked_reason:
                candidate.state = CandidateState.DISCARDED
            else:
                candidate.state = CandidateState.COMPLETED if result.success else CandidateState.FAILED
            candidate.result = result.summary
            candidate.artifacts = result.artifacts
            candidate.fitness = self.reviewer.score(candidate, result)
            generation.fitness_scores.append(candidate.fitness)

            result_relative_path = f"results/generation-{generation_index}/{candidate.id}.json"
            self.store.write_result(mission.id, result_relative_path, run_result_to_dict(result))
            result_paths.append(result_relative_path)

            if blocked_reason:
                candidate_event_type = "candidate_discarded"
            elif result.success:
                candidate_event_type = "candidate_completed"
            else:
                candidate_event_type = "candidate_failed"
            entry = LedgerEntry(
                mission_id=mission.id,
                generation_index=generation_index,
                candidate_loop_id=candidate.id,
                event_type=candidate_event_type,
                summary=result.summary,
                artifacts=result.artifacts,
            )
            self._append_ledger(mission, entry)
            ledger_entries_written += 1

            self._append_event(
                mission,
                "candidate_finished",
                {
                    "success": result.success,
                    "summary": result.summary,
                    "fitness": candidate.fitness.score,
                    "artifacts": [artifact.path for artifact in result.artifacts],
                    "blocked": blocked_reason is not None,
                },
                generation_index,
                candidate.id,
            )
            events_written += 1

        generation.selected_lineage = _select_lineage(generation)
        generation.synthesis = _synthesize_generation(mission, generation)
        synthesis_path = f"artifacts/generation-{generation_index}/synthesis.md"
        self.store.write_artifact(mission.id, synthesis_path, generation.synthesis)

        synthesis_entry = LedgerEntry(
            mission_id=mission.id,
            generation_index=generation_index,
            event_type="generation_synthesized",
            summary=(
                f"Generation {generation_index} synthesized with "
                f"{len(generation.selected_lineage)} selected candidate(s)."
            ),
        )
        self._append_ledger(mission, synthesis_entry)
        ledger_entries_written += 1
        self._append_event(
            mission,
            "generation_finished",
            {
                "selected_loop_ids": generation.selected_lineage,
                "synthesis_path": synthesis_path,
                "mutations": generation.mutations,
                "blocked_candidates": blocked_candidates,
            },
            generation_index,
        )
        events_written += 1

        self.store.save_mission(mission)
        return GenerationRunResult(
            mission_id=mission.id,
            generation_index=generation_index,
            selected_loop_ids=generation.selected_lineage,
            synthesis=generation.synthesis,
            events_written=events_written,
            ledger_entries_written=ledger_entries_written,
            result_paths=result_paths,
            mutations=generation.mutations,
            blocked_candidates=blocked_candidates,
        )

    def _run_candidate(self, mission: Mission, generation_index: int, candidate: CandidateLoop) -> RunResult:
        runner = self.runners.require(candidate.runner)
        request = RunRequest(
            mission=mission,
            generation_index=generation_index,
            candidate=candidate,
            mission_dir=self.store.mission_dir(mission.id),
            workspace=self.workspace,
        )
        return runner.run(request)

    def _apply_verification(
        self,
        mission: Mission,
        candidate: CandidateLoop,
        result: RunResult,
        verify_timeout_seconds: float | None,
    ) -> None:
        if not candidate.verification:
            return
        cwd = self.workspace or self.store.mission_dir(mission.id)
        report = run_verification(candidate.verification, cwd=cwd, timeout_seconds=verify_timeout_seconds)
        result.verification = report.results
        if not report.success:
            result.success = False
            result.summary = f"{result.summary} Verification failed."

    def _append_event(
        self,
        mission: Mission,
        event_type: str,
        data: dict[str, object],
        generation_index: int | None = None,
        candidate_loop_id: str | None = None,
    ) -> None:
        self.store.append_event(
            Event(
                mission_id=mission.id,
                event_type=event_type,
                data=data,
                generation_index=generation_index,
                candidate_loop_id=candidate_loop_id,
            )
        )

    def _append_ledger(self, mission: Mission, entry: LedgerEntry) -> None:
        self.store.append_ledger(entry)
        mission.ledger.append(entry.id)


class ScheduleNotConfigured(ValueError):
    """Raised when a schedule operation targets a mission without a schedule."""

    def __init__(self, mission_id: str) -> None:
        super().__init__(f"Mission has no schedule: {mission_id}")
        self.mission_id = mission_id


def _require_schedule(mission: Mission) -> MissionSchedule:
    if mission.schedule is None:
        raise ScheduleNotConfigured(mission.id)
    return mission.schedule


def _select_lineage(generation: Generation) -> list[str]:
    completed_ids = {
        candidate.id
        for candidate in generation.candidate_loops
        if candidate.state == CandidateState.COMPLETED
    }
    eligible_scores = [
        score
        for score in generation.fitness_scores
        if score.candidate_loop_id in completed_ids
    ]
    scored = sorted(
        eligible_scores,
        key=lambda score: score.score,
        reverse=True,
    )
    if not scored:
        return []
    best_score = scored[0].score
    return [
        score.candidate_loop_id
        for score in scored
        if math.isclose(score.score, best_score, rel_tol=1e-9, abs_tol=1e-9)
    ]


def _synthesize_generation(mission: Mission, generation: Generation) -> str:
    lines = [
        f"# Generation {generation.index} Synthesis",
        "",
        f"Mission: {mission.statement}",
        "",
        f"Success criteria: {mission.success_criteria}",
        "",
        "## Candidate Results",
        "",
    ]
    scores_by_id = {score.candidate_loop_id: score for score in generation.fitness_scores}
    for candidate in generation.candidate_loops:
        score = scores_by_id.get(candidate.id)
        score_text = "n/a" if score is None else f"{score.score:.2f}"
        parent_text = f", parents {', '.join(candidate.parent_ids)}" if candidate.parent_ids else ""
        lines.append(
            f"- {candidate.id} ({candidate.role}, {candidate.state.value}, score {score_text}{parent_text}): "
            f"{candidate.result or 'No result'}"
        )

    lines.extend(["", "## Mutations", ""])
    if generation.mutations:
        for mutation in generation.mutations:
            lines.append(f"- {mutation}")
    else:
        lines.append("- None")

    lines.extend(["", "## Selected Lineage", ""])
    if generation.selected_lineage:
        for loop_id in generation.selected_lineage:
            lines.append(f"- {loop_id}")
    else:
        lines.append("- None")
    lines.extend(["", f"Generated at: {utc_now_iso()}", ""])
    return "\n".join(lines)
