"""User-facing executive mission reports.

The report is the laid-back user's window into a mission: what happened, what
evidence exists, what authority was exercised, and what needs their decision.
It is rendered deterministically from persisted mission state — no LLM in the
loop — so it can be produced after every generation and again on demand.
"""

from __future__ import annotations

from .models import (
    CandidateLoop,
    CandidateState,
    Generation,
    Mission,
    PermissionRecord,
    utc_now_iso,
)

# Keep the evidence listing scannable; the artifacts directory has the rest.
_MAX_EVIDENCE_LINES = 12


def render_mission_report(
    mission: Mission,
    permissions: list[PermissionRecord] | None = None,
) -> str:
    permissions = permissions or []
    generation = mission.generations[-1] if mission.generations else None

    lines = [f"# Mission Report: {mission.statement}", ""]
    lines.extend(_status_lines(mission, generation))
    lines.extend(_progress_lines(generation))
    lines.extend(_evidence_lines(generation))
    lines.extend(_authority_lines(mission, permissions))
    lines.extend(_attention_lines(mission, generation))
    lines.extend(_next_lines(mission))
    lines.extend(["", f"_Generated {utc_now_iso()} from mission {mission.id}._", ""])
    return "\n".join(lines)


def write_generation_report(store, mission: Mission, generation_index: int) -> str:
    """Render and persist the report for one finished generation.

    Returns the mission-relative artifact path. ``store`` needs only
    ``read_permissions`` and ``write_artifact``.
    """
    report = render_mission_report(mission, store.read_permissions(mission.id))
    relative_path = f"reports/generation-{generation_index}.md"
    store.write_artifact(mission.id, relative_path, report)
    return relative_path


def _status_lines(mission: Mission, generation: Generation | None) -> list[str]:
    lines = [f"**Goal:** {mission.success_criteria}", ""]
    if generation is None:
        lines.append("No generations have run yet.")
        return lines
    completed = _by_state(generation, CandidateState.COMPLETED)
    failed = _by_state(generation, CandidateState.FAILED)
    blocked = _by_state(generation, CandidateState.DISCARDED)
    lines.append(
        f"Generation {generation.index} ({generation.state.value}): "
        f"{len(completed)} delivered, {len(failed)} failed, "
        f"{len(blocked)} blocked by policy."
    )
    schedule = mission.schedule
    if schedule is not None:
        status = schedule.last_status or "not yet run"
        lines.append(
            f"Schedule: {schedule.display or schedule.expression} "
            f"({schedule.state.value}, last run {status}, next {schedule.next_run_at or 'n/a'})."
        )
    return lines


def _progress_lines(generation: Generation | None) -> list[str]:
    if generation is None:
        return []
    lines = ["", "## What Was Done", ""]
    selected = set(generation.selected_lineage)
    scores = {score.candidate_loop_id: score.score for score in generation.fitness_scores}
    reported = False
    for candidate in generation.candidate_loops:
        if candidate.state != CandidateState.COMPLETED:
            continue
        marker = " (selected)" if candidate.id in selected else ""
        score = scores.get(candidate.id)
        score_text = f", score {score:.2f}" if score is not None else ""
        lines.append(f"- **{candidate.goal}**{marker}{score_text}")
        if candidate.result:
            lines.append(f"  - {candidate.result}")
        reported = True
    if not reported:
        lines.append("- No candidate delivered results this generation.")
    return lines


def _evidence_lines(generation: Generation | None) -> list[str]:
    if generation is None:
        return []
    paths = [
        artifact.path
        for candidate in generation.candidate_loops
        for artifact in candidate.artifacts
    ]
    lines = ["", "## Evidence", ""]
    if not paths:
        lines.append("- No artifacts were produced.")
        return lines
    for path in paths[:_MAX_EVIDENCE_LINES]:
        lines.append(f"- `{path}`")
    if len(paths) > _MAX_EVIDENCE_LINES:
        lines.append(f"- … and {len(paths) - _MAX_EVIDENCE_LINES} more in the artifacts directory.")
    return lines


def _authority_lines(mission: Mission, permissions: list[PermissionRecord]) -> list[str]:
    lines = ["", "## Authority", ""]
    if not mission.approvals and not permissions:
        lines.append("- No external-action authority has been granted; work stays read-only and local.")
        return lines
    for capability, approved_by in sorted(mission.approvals.items()):
        uses = sum(
            1
            for record in permissions
            if record.action == "used" and record.capability == capability
        )
        lines.append(
            f"- `{capability}` granted by {approved_by}; exercised {uses} time(s)."
        )
    revoked = [record for record in permissions if record.action == "revoked"]
    for record in revoked:
        lines.append(f"- `{record.capability}` revoked by {record.actor}.")
    return lines


def _attention_lines(mission: Mission, generation: Generation | None) -> list[str]:
    items: list[str] = []
    if generation is not None:
        for candidate in _by_state(generation, CandidateState.DISCARDED):
            for gate in candidate.policy_gates:
                if gate.requires_approval and not gate.approved_by:
                    items.append(
                        f"**{candidate.goal}** is waiting on your approval of "
                        f"`{gate.capability}` ({gate.side_effect_class.value})."
                    )
        for candidate in _by_state(generation, CandidateState.FAILED):
            hint = (
                f" Suggested fix: {candidate.outcome.remedy_hint}"
                if candidate.outcome and candidate.outcome.remedy_hint
                else ""
            )
            items.append(f"**{candidate.goal}** failed: {candidate.result or 'no detail'}.{hint}")
    schedule = mission.schedule
    if schedule is not None and schedule.last_error:
        items.append(f"Schedule error: {schedule.last_error}")
    if not items:
        return []
    lines = ["", "## Needs Your Attention", ""]
    lines.extend(f"- {item}" for item in items)
    return lines


def _next_lines(mission: Mission) -> list[str]:
    lines = ["", "## Next", ""]
    schedule = mission.schedule
    if schedule is not None and schedule.enabled and schedule.next_run_at:
        lines.append(
            f"- The next generation runs automatically at {schedule.next_run_at}."
        )
    else:
        lines.append(
            f"- Run the next generation with `multi-loop run {mission.id}` "
            "or attach a schedule to continue unattended."
        )
    return lines


def _by_state(generation: Generation, state: CandidateState) -> list[CandidateLoop]:
    return [
        candidate
        for candidate in generation.candidate_loops
        if candidate.state == state
    ]
