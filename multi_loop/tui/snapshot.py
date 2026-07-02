"""Compact operator context assembled fresh for every chat turn.

This is what makes the console "already know": the user never explains state
to the agent, and the agent never has to go looking for it.
"""

from __future__ import annotations

from ..models import CandidateState, Mission
from ..storage import MissionStore

_MAX_MISSIONS = 8


def build_snapshot(store: MissionStore, *, selected_mission_id: str | None = None) -> str:
    missions = store.list_missions()
    if not missions:
        return "No missions exist yet."
    missions.sort(key=lambda mission: mission.updated_at, reverse=True)

    lines: list[str] = ["Missions:"]
    for mission in missions[:_MAX_MISSIONS]:
        lines.append(f"- {mission.id}: {_mission_line(mission)}")

    focus = _find(missions, selected_mission_id) or missions[0]
    lines.append(f"\nFocused mission {focus.id}:")
    lines.append(f"- statement: {focus.statement}")
    lines.append(f"- success criteria: {focus.success_criteria}")
    if focus.approvals:
        grants = ", ".join(f"{cap} (by {who})" for cap, who in sorted(focus.approvals.items()))
        lines.append(f"- granted authority: {grants}")
    else:
        lines.append("- granted authority: none (read-only and local)")
    blocked = _pending_approvals(focus)
    if blocked:
        lines.append("- awaiting user approval: " + ", ".join(sorted(blocked)))
    return "\n".join(lines)


def _mission_line(mission: Mission) -> str:
    parts = [mission.statement[:80]]
    parts.append(f"{len(mission.generations)} generation(s)")
    if mission.schedule is not None:
        parts.append(
            f"schedule {mission.schedule.display or mission.schedule.expression} "
            f"[{mission.schedule.state.value}], next {mission.schedule.next_run_at or 'n/a'}"
        )
    else:
        parts.append("no schedule")
    if mission.generations:
        generation = mission.generations[-1]
        states: dict[str, int] = {}
        for candidate in generation.candidate_loops:
            states[candidate.state.value] = states.get(candidate.state.value, 0) + 1
        summary = ", ".join(f"{count} {state}" for state, count in sorted(states.items()))
        parts.append(f"gen {generation.index} {generation.state.value} ({summary})")
    return "; ".join(parts)


def _pending_approvals(mission: Mission) -> set[str]:
    pending: set[str] = set()
    for generation in mission.generations:
        for candidate in generation.candidate_loops:
            if candidate.state != CandidateState.DISCARDED:
                continue
            for gate in candidate.policy_gates:
                if gate.requires_approval and not gate.approved_by:
                    pending.add(gate.capability)
    return pending - set(mission.approvals)


def _find(missions: list[Mission], mission_id: str | None) -> Mission | None:
    if not mission_id:
        return None
    for mission in missions:
        if mission.id == mission_id:
            return mission
    return None
