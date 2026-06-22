"""Deterministic outer mission orchestrator."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import CapabilityRegistry
from .capability_config import configured_capabilities
from .failures import FailureClassifier, RuleBasedClassifier
from .models import (
    Artifact,
    Budget,
    CandidateLoop,
    CandidateState,
    Event,
    ExecutionProfile,
    FitnessScore,
    Generation,
    GenerationState,
    LedgerEntry,
    Mission,
    MissionSchedule,
    Outcome,
    ScheduleState,
    from_dict,
    new_id,
    utc_now_iso,
)
from .leases import acquire_mission_lease
from .planning import FitnessReviewer, HeuristicPortfolioPlanner, collect_pitfalls
from .policy import APPROVAL_REQUIRED, prepare_candidate, resolve_within, side_effect_directive
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


@dataclass(slots=True)
class CandidateClaim:
    """A policy-checked unit of work for an external host agent."""

    mission_id: str
    generation_index: int
    candidate: CandidateLoop
    safety_directive: str
    claim_token: str
    blocked: bool = False
    block_reason: str | None = None


class MissionOrchestrator:
    """Run bounded multi-loop mission generations."""

    def __init__(
        self,
        store: MissionStore | None = None,
        runners: RunnerRegistry | None = None,
        capabilities: CapabilityRegistry | None = None,
        planner: HeuristicPortfolioPlanner | None = None,
        reviewer: FitnessReviewer | None = None,
        classifier: FailureClassifier | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self.store = store or MissionStore()
        self.runners = runners or default_runner_registry()
        self.capabilities = capabilities or configured_capabilities(self.store.root)
        self.planner = planner or HeuristicPortfolioPlanner(self.capabilities)
        self.reviewer = reviewer or FitnessReviewer()
        self.classifier = classifier or RuleBasedClassifier()
        self.workspace = Path(workspace).resolve() if workspace else None

    def create_mission(
        self,
        statement: str,
        success_criteria: str,
        *,
        schedule: str | None = None,
        clarifications: dict[str, str] | None = None,
        approvals: dict[str, str] | None = None,
        execution_profile: ExecutionProfile | None = None,
        selected_capabilities: list[str] | None = None,
        onboarding_session_id: str | None = None,
        budget: Budget | None = None,
    ) -> Mission:
        mission_schedule = MissionSchedule(expression=schedule) if schedule else None
        if mission_schedule is not None:
            mission_schedule = initialize_schedule(mission_schedule)

        mission = Mission(
            statement=statement,
            success_criteria=success_criteria,
            clarifications=clarifications or {},
            approvals=approvals or {},
            execution_profile=execution_profile or ExecutionProfile(),
            selected_capabilities=selected_capabilities or [],
            onboarding_session_id=onboarding_session_id,
            budget=budget or Budget(),
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
        card = self.capabilities.get(capability)
        if card is None:
            raise ValueError(f"Unknown capability: {capability}")
        if card.side_effect_class not in APPROVAL_REQUIRED:
            raise ValueError(
                f"Capability {capability} does not require external-action approval."
            )
        if not approved_by.strip():
            raise ValueError("Approver identity is required.")
        mission = self.store.load_mission(mission_id)
        mission.approvals[capability] = approved_by.strip()
        self.store.save_mission(mission)
        self._append_event(
            mission,
            "capability_approved",
            {"capability": capability, "approved_by": approved_by.strip()},
        )
        entry = LedgerEntry(
            mission_id=mission.id,
            event_type="capability_approved",
            summary=f"Approved capability {capability} for {approved_by.strip()}",
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
        runner_command: str | None = None,
        allow_side_effects: bool = False,
        verification: list[str] | None = None,
        verify_timeout_seconds: float | None = None,
    ) -> GenerationRunResult:
        """Run one generation under an exclusive mission lease.

        ``runner_command`` makes the run do real work: it is applied to every
        candidate's ``runner_config`` so the ``shell``/``agent_command`` runners
        execute it (the latter pipes each candidate's self-contained prompt to
        the command on stdin). When a command is given without an explicit
        ``runner_name``, the runner defaults to ``agent_command``.

        ``allow_side_effects`` is retained for CLI compatibility but does not
        bypass policy. A candidate can take outward-facing actions only when its
        required side-effecting capability has a recorded mission approval.

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
                runner_command=runner_command,
                allow_side_effects=allow_side_effects,
                verification=verification,
                verify_timeout_seconds=verify_timeout_seconds,
            )

    def prepare_generation(self, mission_id: str) -> Generation:
        """Plan a durable generation without executing its candidates.

        This is the MCP/host-agent execution path. Repeated calls return the
        current unfinished generation instead of creating duplicate work.
        """
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            mission = self.store.load_mission(mission_id)
            if mission.generations:
                current = mission.generations[-1]
                _repair_legacy_generation_state(current)
                if current.state in {GenerationState.PLANNED, GenerationState.RUNNING}:
                    self.store.save_mission(mission)
                    return current

            generation_index = len(mission.generations)
            portfolio = self.planner.plan(mission, generation_index)
            generation = Generation(
                index=generation_index,
                state=GenerationState.PLANNED,
                candidate_loops=portfolio.candidates,
                mutations=portfolio.mutations,
            )
            mission.generations.append(generation)
            self._append_event(
                mission,
                "generation_prepared",
                {
                    "candidate_count": len(generation.candidate_loops),
                    "mutations": generation.mutations,
                    "controller": mission.execution_profile.controller,
                },
                generation_index,
            )
            self.store.save_mission(mission)
            return generation

    def claim_candidate(
        self,
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        *,
        claimant_id: str = "host_agent",
        claim_token: str | None = None,
    ) -> CandidateClaim:
        """Policy-check and atomically claim a candidate for host execution."""
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            mission = self.store.load_mission(mission_id)
            generation = _require_generation(mission, generation_index)
            _repair_legacy_generation_state(generation)
            if generation.state == GenerationState.COMPLETED:
                raise ValueError(f"Generation {generation_index} is already completed.")
            candidate = _require_candidate(generation, candidate_id)
            if candidate.state == CandidateState.RUNNING:
                if not claim_token or claim_token != candidate.claim_token:
                    raise ValueError(
                        f"Candidate {candidate_id} is already claimed by {candidate.claimed_by or 'another host'}."
                    )
                return CandidateClaim(
                    mission_id=mission_id,
                    generation_index=generation_index,
                    candidate=candidate,
                    safety_directive=side_effect_directive(candidate, mission, self.capabilities),
                    claim_token=candidate.claim_token,
                )
            if candidate.state != CandidateState.PLANNED:
                raise ValueError(
                    f"Candidate {candidate_id} cannot be claimed from state {candidate.state.value}."
                )

            blocked_reason = prepare_candidate(candidate, mission, self.capabilities)
            if blocked_reason:
                result = RunResult(
                    candidate_loop_id=candidate.id,
                    success=False,
                    summary=blocked_reason,
                    metadata={"blocked_by_policy": True},
                )
                self._record_candidate_result(
                    mission,
                    generation,
                    candidate,
                    result,
                    blocked=True,
                )
                self.store.save_mission(mission)
                return CandidateClaim(
                    mission_id=mission_id,
                    generation_index=generation_index,
                    candidate=candidate,
                    safety_directive=side_effect_directive(candidate, mission, self.capabilities),
                    claim_token="",
                    blocked=True,
                    block_reason=blocked_reason,
                )

            candidate.state = CandidateState.RUNNING
            candidate.claim_token = claim_token or new_id("claim")
            candidate.claimed_by = claimant_id.strip() or "host_agent"
            candidate.claimed_at = utc_now_iso()
            generation.state = GenerationState.RUNNING
            self._append_event(
                mission,
                "candidate_claimed",
                {
                    "goal": candidate.goal,
                    "controller": mission.execution_profile.controller,
                    "claimed_by": candidate.claimed_by,
                },
                generation_index,
                candidate.id,
            )
            self.store.save_mission(mission)
            return CandidateClaim(
                mission_id=mission_id,
                generation_index=generation_index,
                candidate=candidate,
                safety_directive=side_effect_directive(candidate, mission, self.capabilities),
                claim_token=candidate.claim_token,
            )

    def submit_candidate_result(
        self,
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        *,
        success: bool,
        summary: str,
        output: str = "",
        artifacts: list[Artifact] | list[dict[str, object]] | None = None,
        metadata: dict[str, object] | None = None,
        submission_id: str | None = None,
        claim_token: str | None = None,
    ) -> CandidateLoop:
        """Persist one host-executed result with optional idempotency key."""
        if not summary.strip():
            raise ValueError("Candidate result summary cannot be empty.")
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            mission = self.store.load_mission(mission_id)
            generation = _require_generation(mission, generation_index)
            candidate = _require_candidate(generation, candidate_id)
            if candidate.state in {
                CandidateState.COMPLETED,
                CandidateState.FAILED,
                CandidateState.DISCARDED,
            }:
                if submission_id and candidate.submission_id == submission_id:
                    return candidate
                raise ValueError(f"Candidate {candidate_id} already has a terminal result.")
            if candidate.state != CandidateState.RUNNING:
                raise ValueError(f"Candidate {candidate_id} must be claimed before submission.")
            if not claim_token or claim_token != candidate.claim_token:
                raise ValueError(f"Candidate {candidate_id} result requires its active claim token.")

            hydrated_artifacts: list[Artifact] = []
            for item in artifacts or []:
                artifact = item if isinstance(item, Artifact) else from_dict(Artifact, item)
                # Validate containment even though the host, rather than this
                # process, created the artifact.
                artifact_path = resolve_within(mission_dir, artifact.path)
                if not artifact_path.exists():
                    raise ValueError(
                        f"Submitted artifact does not exist in mission storage: {artifact.path}"
                    )
                hydrated_artifacts.append(artifact)

            candidate.submission_id = submission_id
            result = RunResult(
                candidate_loop_id=candidate.id,
                success=success,
                summary=summary,
                output=output,
                artifacts=hydrated_artifacts,
                metadata={**(metadata or {}), "submission_id": submission_id},
            )
            self._apply_verification(mission, candidate, result, None)
            self._record_candidate_result(mission, generation, candidate, result)
            self.store.save_mission(mission)
            return candidate

    def write_candidate_artifact(
        self,
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        *,
        claim_token: str,
        filename: str,
        content: str,
        kind: str = "text",
        description: str = "Host-agent artifact",
    ) -> Artifact:
        """Safely write host-generated evidence into scoped mission storage."""
        clean_name = filename.strip()
        if (
            not clean_name
            or clean_name in {".", ".."}
            or "/" in clean_name
            or "\\" in clean_name
            or Path(clean_name).name != clean_name
        ):
            raise ValueError("Artifact filename must be a single safe filename.")
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            mission = self.store.load_mission(mission_id)
            generation = _require_generation(mission, generation_index)
            candidate = _require_candidate(generation, candidate_id)
            if candidate.state != CandidateState.RUNNING:
                raise ValueError("Candidate artifacts may be written only while the candidate is claimed.")
            if not claim_token or claim_token != candidate.claim_token:
                raise ValueError(f"Candidate {candidate_id} artifact requires its active claim token.")
            relative_path = (
                f"artifacts/generation-{generation_index}/{candidate_id}-host/{clean_name}"
            )
            self.store.write_artifact(mission_id, relative_path, content)
            artifact = Artifact(path=relative_path, kind=kind, description=description)
            self._append_event(
                mission,
                "candidate_artifact_written",
                {"path": relative_path, "kind": kind},
                generation_index,
                candidate_id,
            )
            return artifact

    def finalize_generation(
        self,
        mission_id: str,
        generation_index: int,
    ) -> GenerationRunResult:
        """Deterministically select lineage and synthesize a finished generation."""
        mission_dir = self.store.mission_dir(mission_id)
        if not mission_dir.exists():
            raise MissionNotFound(mission_id)
        with acquire_mission_lease(mission_dir, mission_id):
            mission = self.store.load_mission(mission_id)
            generation = _require_generation(mission, generation_index)
            _repair_legacy_generation_state(generation)
            if generation.state == GenerationState.COMPLETED:
                return _generation_result(mission, generation)
            unfinished = [
                candidate.id
                for candidate in generation.candidate_loops
                if candidate.state in {CandidateState.PLANNED, CandidateState.RUNNING}
            ]
            if unfinished:
                raise ValueError(
                    "Cannot finalize generation with unfinished candidates: " + ", ".join(unfinished)
                )

            generation.selected_lineage = _select_lineage(generation)
            generation.synthesis = _synthesize_generation(mission, generation)
            generation.state = GenerationState.COMPLETED
            synthesis_path = f"artifacts/generation-{generation_index}/synthesis.md"
            self.store.write_artifact(mission.id, synthesis_path, generation.synthesis)
            self._append_ledger(
                mission,
                LedgerEntry(
                    mission_id=mission.id,
                    generation_index=generation_index,
                    event_type="generation_synthesized",
                    summary=(
                        f"Generation {generation_index} synthesized with "
                        f"{len(generation.selected_lineage)} selected candidate(s)."
                    ),
                ),
            )
            self._append_event(
                mission,
                "generation_finished",
                {
                    "selected_loop_ids": generation.selected_lineage,
                    "synthesis_path": synthesis_path,
                    "mutations": generation.mutations,
                    "blocked_candidates": _blocked_candidate_ids(generation),
                },
                generation_index,
            )
            self.store.save_mission(mission)
            return _generation_result(mission, generation, events_written=1, ledger_entries_written=1)

    def _run_generation_locked(
        self,
        mission_id: str,
        *,
        runner_name: str | None = None,
        runner_command: str | None = None,
        allow_side_effects: bool = False,
        verification: list[str] | None = None,
        verify_timeout_seconds: float | None = None,
    ) -> GenerationRunResult:
        mission = self.store.load_mission(mission_id)
        if mission.generations:
            previous = mission.generations[-1]
            _repair_legacy_generation_state(previous)
            if previous.state in {GenerationState.PLANNED, GenerationState.RUNNING}:
                raise ValueError(
                    f"Generation {previous.index} is unfinished; resume or finalize it before starting another."
                )
        generation_index = len(mission.generations)
        portfolio = self.planner.plan(mission, generation_index)
        candidates = portfolio.candidates
        effective_runner = runner_name
        if runner_command and not effective_runner:
            effective_runner = "agent_command"  # real-agent default when a command is supplied
        if effective_runner:
            for candidate in candidates:
                candidate.runner = effective_runner
        if runner_command:
            for candidate in candidates:
                candidate.runner_config = {**candidate.runner_config, "command": runner_command}
        if verification:
            for candidate in candidates:
                candidate.verification = list(verification)

        generation = Generation(
            index=generation_index,
            state=GenerationState.RUNNING,
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
            {
                "candidate_count": len(candidates),
                "mutations": portfolio.mutations,
                "side_effects_requested": allow_side_effects,
                "side_effect_policy": "capability_scoped",
            },
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
                    result = self._run_candidate(
                        mission, generation_index, candidate, allow_side_effects
                    )
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
            candidate.outcome = self.classifier.classify(candidate, result)
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
                    **_outcome_event_fields(candidate.outcome),
                },
                generation_index,
                candidate.id,
            )
            events_written += 1

        generation.selected_lineage = _select_lineage(generation)
        generation.synthesis = _synthesize_generation(mission, generation)
        generation.state = GenerationState.COMPLETED
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

    def _record_candidate_result(
        self,
        mission: Mission,
        generation: Generation,
        candidate: CandidateLoop,
        result: RunResult,
        *,
        blocked: bool = False,
    ) -> str:
        """Apply a runner or host result through one durable result path."""
        candidate.state = (
            CandidateState.DISCARDED
            if blocked
            else CandidateState.COMPLETED
            if result.success
            else CandidateState.FAILED
        )
        candidate.result = result.summary
        candidate.artifacts = result.artifacts
        candidate.fitness = self.reviewer.score(candidate, result)
        candidate.outcome = self.classifier.classify(candidate, result)
        generation.fitness_scores = [
            score
            for score in generation.fitness_scores
            if score.candidate_loop_id != candidate.id
        ]
        generation.fitness_scores.append(candidate.fitness)
        if generation.state == GenerationState.PLANNED:
            generation.state = GenerationState.RUNNING

        result_relative_path = f"results/generation-{generation.index}/{candidate.id}.json"
        self.store.write_result(mission.id, result_relative_path, run_result_to_dict(result))
        event_type = (
            "candidate_discarded"
            if blocked
            else "candidate_completed"
            if result.success
            else "candidate_failed"
        )
        self._append_ledger(
            mission,
            LedgerEntry(
                mission_id=mission.id,
                generation_index=generation.index,
                candidate_loop_id=candidate.id,
                event_type=event_type,
                summary=result.summary,
                artifacts=result.artifacts,
            ),
        )
        self._append_event(
            mission,
            "candidate_finished",
            {
                "success": result.success,
                "summary": result.summary,
                "fitness": candidate.fitness.score,
                "artifacts": [artifact.path for artifact in result.artifacts],
                "blocked": blocked,
                "submission_id": candidate.submission_id,
                **_outcome_event_fields(candidate.outcome),
            },
            generation.index,
            candidate.id,
        )
        return result_relative_path

    def _run_candidate(
        self,
        mission: Mission,
        generation_index: int,
        candidate: CandidateLoop,
        allow_side_effects: bool,
    ) -> RunResult:
        runner = self.runners.require(candidate.runner)
        request = RunRequest(
            mission=mission,
            generation_index=generation_index,
            candidate=candidate,
            mission_dir=self.store.mission_dir(mission.id),
            workspace=self.workspace,
            safety_directive=side_effect_directive(
                candidate, mission, self.capabilities, allow_side_effects=allow_side_effects
            ),
            pitfalls=collect_pitfalls(mission, candidate),
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
        configured_workspace = (
            Path(mission.execution_profile.workspace).expanduser().resolve()
            if mission.execution_profile.workspace
            else None
        )
        cwd = self.workspace or configured_workspace or self.store.mission_dir(mission.id)
        report = run_verification(candidate.verification, cwd=cwd, timeout_seconds=verify_timeout_seconds)
        result.verification = report.results
        # Verification is authoritative when configured: it decides success
        # regardless of the runner's exit code. This rescues a candidate that
        # did the work but whose runner was killed (e.g. timed out before it
        # could report), and fails one that exited cleanly but cannot prove its
        # claimed side effects — so success reflects evidence, not self-report.
        runner_succeeded = result.success
        result.success = report.success
        result.metadata["verified"] = report.success
        if report.success and not runner_succeeded:
            result.summary = f"{result.summary} (runner did not exit cleanly; verification confirmed the work)."
        elif not report.success:
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


def _outcome_event_fields(outcome: Outcome | None) -> dict[str, object]:
    """Surface the queryable parts of an outcome on the candidate_finished event."""
    if outcome is None:
        return {}
    return {
        "failure_class": outcome.failure_class.value if outcome.failure_class else None,
        "remedy_hint": outcome.remedy_hint,
    }


def _require_schedule(mission: Mission) -> MissionSchedule:
    if mission.schedule is None:
        raise ScheduleNotConfigured(mission.id)
    return mission.schedule


def _require_generation(mission: Mission, generation_index: int) -> Generation:
    for generation in mission.generations:
        if generation.index == generation_index:
            return generation
    raise ValueError(f"Generation {generation_index} does not exist for mission {mission.id}.")


def _require_candidate(generation: Generation, candidate_id: str) -> CandidateLoop:
    for candidate in generation.candidate_loops:
        if candidate.id == candidate_id:
            return candidate
    raise ValueError(f"Candidate {candidate_id} does not exist in generation {generation.index}.")


def _repair_legacy_generation_state(generation: Generation) -> None:
    """Infer completed state for mission files written before state existed."""
    if generation.synthesis and generation.state != GenerationState.COMPLETED:
        generation.state = GenerationState.COMPLETED


def _blocked_candidate_ids(generation: Generation) -> list[str]:
    return [
        candidate.id
        for candidate in generation.candidate_loops
        if candidate.state == CandidateState.DISCARDED
    ]


def _generation_result(
    mission: Mission,
    generation: Generation,
    *,
    events_written: int = 0,
    ledger_entries_written: int = 0,
) -> GenerationRunResult:
    return GenerationRunResult(
        mission_id=mission.id,
        generation_index=generation.index,
        selected_loop_ids=list(generation.selected_lineage),
        synthesis=generation.synthesis or "",
        events_written=events_written,
        ledger_entries_written=ledger_entries_written,
        result_paths=[
            f"results/generation-{generation.index}/{candidate.id}.json"
            for candidate in generation.candidate_loops
            if candidate.state
            in {CandidateState.COMPLETED, CandidateState.FAILED, CandidateState.DISCARDED}
        ],
        mutations=list(generation.mutations),
        blocked_candidates=_blocked_candidate_ids(generation),
    )


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
