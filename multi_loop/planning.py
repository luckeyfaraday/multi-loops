"""Portfolio planning, capability resolution, and policy preparation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .capabilities import CapabilityRegistry, default_capabilities
from .models import (
    CandidateLoop,
    CandidateState,
    CapabilityRef,
    FitnessScore,
    Mission,
)
from .policy import attach_policy_gates, candidate_blocked_now
from .runners import RunResult

_CAPABILITY_RUNNERS: dict[str, str] = {
    "agent_loop": "mock",
    "shell_command": "shell",
    "agent_command": "agent_command",
    "manual_task": "mock",
}


@dataclass(slots=True)
class PortfolioPlan:
    candidates: list[CandidateLoop] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)


class HeuristicPortfolioPlanner:
    """Deterministic planner that evolves portfolios across generations."""

    def __init__(self, capabilities: CapabilityRegistry | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    def plan(self, mission: Mission, generation_index: int) -> PortfolioPlan:
        if generation_index == 0:
            return self._plan_initial(mission)
        return self._plan_evolved(mission, generation_index)

    def _plan_initial(self, mission: Mission) -> PortfolioPlan:
        candidates = [
            _base_loop(
                mission,
                role="research",
                goal=f"Map constraints, assumptions, and missing information for: {mission.statement}",
                success_criteria="Return the key constraints, assumptions, unknowns, and evidence needed.",
                capabilities=["agent_loop"],
            ),
            _base_loop(
                mission,
                role="strategy",
                goal=f"Generate a concrete candidate strategy for: {mission.statement}",
                success_criteria="Return a specific plan with expected artifacts, risks, and next actions.",
                capabilities=["agent_loop"],
            ),
            _base_loop(
                mission,
                role="review",
                goal=f"Review risks and verification paths for: {mission.statement}",
                success_criteria="Return risk findings, verification steps, and stop/go criteria.",
                capabilities=["agent_loop"],
            ),
        ]
        candidates.extend(_mission_specific_loops(mission))
        for capability_name in mission.selected_capabilities:
            if capability_name == "agent_loop" or any(
                capability_name in _capability_names(candidate) for candidate in candidates
            ):
                continue
            candidates.append(
                _base_loop(
                    mission,
                    role=f"capability_{capability_name}",
                    goal=(
                        f"Use the selected {capability_name} capability to advance: "
                        f"{mission.statement}"
                    ),
                    success_criteria=(
                        "Return concrete evidence and artifacts aligned with the mission's "
                        "success criteria."
                    ),
                    capabilities=[capability_name],
                )
            )
        return PortfolioPlan(candidates=_finalize_candidates(mission, candidates, self.capabilities))

    def _plan_evolved(self, mission: Mission, generation_index: int) -> PortfolioPlan:
        previous = mission.generations[generation_index - 1]
        candidates: list[CandidateLoop] = []
        mutations: list[str] = []

        winners = [
            candidate
            for candidate in previous.candidate_loops
            if candidate.id in previous.selected_lineage
        ]
        failures = [
            candidate
            for candidate in previous.candidate_loops
            if candidate.state == CandidateState.FAILED
        ]
        # Candidates discarded because a policy gate blocked them are not retried
        # blindly (that would loop forever while approval is withheld). But once
        # their capability has since been approved, resume the work.
        recoverable = [
            candidate
            for candidate in previous.candidate_loops
            if candidate.state == CandidateState.DISCARDED
            and not candidate_blocked_now(candidate, mission, self.capabilities)
        ]

        for winner in winners:
            child = _base_loop(
                mission,
                role=f"{winner.role}_refined",
                goal=(
                    f"Refine and deepen the prior {winner.role} output for: {mission.statement}. "
                    f"Prior result: {winner.result or 'No result recorded.'}"
                ),
                success_criteria=(
                    f"Improve on generation {previous.index} findings with more specificity, "
                    "evidence, and actionable next steps."
                ),
                capabilities=_capability_names(winner),
                parent_ids=[winner.id],
            )
            candidates.append(child)
            mutations.append(f"narrow_scope:{winner.id}->{child.id}")

        for failed in failures:
            child = _base_loop(
                mission,
                role=f"{failed.role}_retry",
                goal=f"Retry with a narrower scope after failure: {failed.goal}",
                success_criteria="Return a smaller scoped result that can complete under current constraints.",
                capabilities=_capability_names(failed),
                parent_ids=[failed.id],
            )
            candidates.append(child)
            mutations.append(f"retry_narrow:{failed.id}->{child.id}")

        for blocked in recoverable:
            child = _base_loop(
                mission,
                role=f"{blocked.role}_approved",
                goal=f"Resume now-approved work: {blocked.goal}",
                success_criteria=blocked.success_criteria,
                capabilities=_capability_names(blocked),
                parent_ids=[blocked.id],
            )
            candidates.append(child)
            mutations.append(f"approved_retry:{blocked.id}->{child.id}")

        if len(winners) >= 2:
            left, right = winners[0], winners[1]
            child = _base_loop(
                mission,
                role="crossover",
                goal=(
                    f"Combine the strongest parts of the {left.role} and {right.role} outputs "
                    f"for: {mission.statement}"
                ),
                success_criteria="Return one integrated candidate that merges the best insights from both parents.",
                capabilities=sorted(set(_capability_names(left) + _capability_names(right))),
                parent_ids=[left.id, right.id],
            )
            candidates.append(child)
            mutations.append(f"crossover:{left.id}+{right.id}->{child.id}")

        candidates.append(
            _base_loop(
                mission,
                role="synthesis_worker",
                goal=f"Synthesize selected lineage into an integrated artifact for: {mission.statement}",
                success_criteria=mission.success_criteria,
                capabilities=["agent_loop"],
                dependencies=[winner.id for winner in winners],
            )
        )

        if not candidates:
            return self._plan_initial(mission)

        return PortfolioPlan(
            candidates=_finalize_candidates(mission, candidates, self.capabilities),
            mutations=mutations,
        )


def preferred_runner(clarifications: dict[str, str]) -> str:
    tools = clarifications.get("preferred_tools", "").lower()
    if "agent_command" in tools:
        return "agent_command"
    if "shell" in tools:
        return "shell"
    return "mock"


def _finalize_candidates(
    mission: Mission,
    candidates: list[CandidateLoop],
    capabilities: CapabilityRegistry,
) -> list[CandidateLoop]:
    default_runner = preferred_runner(mission.clarifications)
    for candidate in candidates:
        if candidate.runner == "mock" and default_runner != "mock":
            candidate.runner = default_runner
        attach_policy_gates(candidate, mission, capabilities)
    return candidates


def _base_loop(
    mission: Mission,
    *,
    role: str,
    goal: str,
    success_criteria: str,
    capabilities: list[str],
    parent_ids: list[str] | None = None,
    dependencies: list[str] | None = None,
) -> CandidateLoop:
    refs = [CapabilityRef(name=name) for name in capabilities]
    runner = _CAPABILITY_RUNNERS.get(capabilities[0], "mock") if capabilities else "mock"
    if runner == "mock":
        runner = preferred_runner(mission.clarifications)
    return CandidateLoop(
        goal=goal,
        success_criteria=success_criteria,
        role=role,
        runner=runner,
        parent_ids=parent_ids or [],
        dependencies=dependencies or [],
        required_capabilities=refs,
    )


def _capability_names(candidate: CandidateLoop) -> list[str]:
    names = [ref.name for ref in candidate.required_capabilities]
    return names or ["agent_loop"]


def _mission_specific_loops(mission: Mission) -> list[CandidateLoop]:
    text = " ".join([mission.statement, *mission.clarifications.values()]).lower()
    extras: list[CandidateLoop] = []

    if _matches(text, "company", "startup", "business", "market", "customer"):
        extras.append(
            _base_loop(
                mission,
                role="market_research",
                goal=f"Research market opportunities and customer pain for: {mission.statement}",
                success_criteria="Return ranked opportunities with evidence, risks, and validation steps.",
                capabilities=["web_research"],
            )
        )

    if _matches(text, "campaign", "ad", "ads", "marketing", "growth"):
        extras.append(
            _base_loop(
                mission,
                role="campaign_experiment",
                goal=f"Draft ad campaign hypotheses for: {mission.statement}",
                success_criteria="Return audience, hook, offer, channel, and budget assumptions for each variant.",
                capabilities=["paid_ads"],
            )
        )

    if _matches(text, "video", "youtube", "documentary", "content"):
        extras.append(
            _base_loop(
                mission,
                role="content_research",
                goal=f"Gather source material and narrative angles for: {mission.statement}",
                success_criteria="Return sources, claims, and competing narrative structures.",
                capabilities=["web_research", "media_generation"],
            )
        )

    return extras


def _matches(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _verification_score(result: RunResult) -> float:
    # Reward verifiable evidence and give nothing for an unverified self-report,
    # so a candidate that proves its work outscores one that merely claims it.
    if not result.verification:
        return 0.0
    return 0.15 if all(item.success for item in result.verification) else 0.0


class FitnessReviewer:
    """Score candidate outputs with a deterministic MVP rubric."""

    def score(self, candidate: CandidateLoop, result: RunResult) -> FitnessScore:
        runner_score = 0.55 if result.success else 0.0
        artifact_score = 0.20 if result.artifacts else 0.0
        verification_score = _verification_score(result)
        specificity_score = 0.15 if len(result.summary.strip()) >= 20 else 0.05
        lineage_bonus = 0.05 if candidate.parent_ids else 0.0
        score = round(
            runner_score + artifact_score + verification_score + specificity_score + lineage_bonus,
            4,
        )
        rationale = (
            "Candidate succeeded with artifacts and verification evidence."
            if result.success
            else "Candidate failed or was blocked; score reflects incomplete execution."
        )
        return FitnessScore(
            candidate_loop_id=candidate.id,
            score=score,
            rationale=rationale,
            rubric={
                "runner_success": runner_score,
                "artifacts": artifact_score,
                "verification": verification_score,
                "specificity": specificity_score,
                "lineage_bonus": lineage_bonus,
            },
        )
