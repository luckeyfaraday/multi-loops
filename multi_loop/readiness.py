"""Deterministic mission readiness reports for the operator agent.

Readiness answers the operator's prep question — "if a generation ran right
now, what would fail or be blocked?" — before any budget is spent. It is a
pure derivation from the capability registry and mission/draft state, so the
operator can call it repeatedly while working through gaps with the user.
"""

from __future__ import annotations

import shutil
from typing import Any

from .capabilities import CapabilityRegistry
from .models import Mission
from .policy import APPROVAL_REQUIRED

# The runners a schedule can fire without an interactive session attached.
UNATTENDED_RUNNERS = frozenset({"agent_command", "shell", "hermes"})


def capability_readiness_items(
    registry: CapabilityRegistry,
    names: list[str],
    approvals: dict[str, str],
) -> list[dict[str, Any]]:
    """Classify each capability as ready, needs_setup, needs_approval, or unknown."""
    items: list[dict[str, Any]] = []
    for name in names:
        capability = registry.get(name)
        if capability is None:
            items.append(
                {
                    "name": name,
                    "status": "unknown",
                    "detail": "not a registered capability",
                    "fix": (
                        "search capability cards for an equivalent, or persist it "
                        "with capability_add_command"
                    ),
                }
            )
            continue
        requires_approval = capability.side_effect_class in APPROVAL_REQUIRED
        approved_by = approvals.get(name)
        item: dict[str, Any] = {
            "name": name,
            "side_effect_class": capability.side_effect_class.value,
            "requires_approval": requires_approval,
            "approved_by": approved_by,
        }
        if not registry.available(name):
            item.update(
                {
                    "status": "needs_setup",
                    "missing_env": registry.missing_env(name),
                    "detail": capability.availability_check or "requires setup",
                    "fix": capability.setup_hint
                    or capability.availability_check
                    or "configure the capability's backend",
                }
            )
        elif requires_approval and not approved_by:
            item.update(
                {
                    "status": "needs_approval",
                    "detail": (
                        f"side effect class {capability.side_effect_class.value} "
                        "requires a recorded approval"
                    ),
                    "fix": "ask the user, then record approve_capability for this mission",
                }
            )
        else:
            item["status"] = "ready"
        items.append(item)
    return items


def mission_readiness_report(
    mission: Mission,
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    """Build the operator-facing readiness report for a created mission."""
    items = capability_readiness_items(
        registry, mission.selected_capabilities, mission.approvals
    )
    blockers = [
        f"capability {item['name']}: {item['status']} ({item.get('detail', '')})"
        for item in items
        if item["status"] != "ready"
    ]
    notices: list[str] = []
    profile = mission.execution_profile
    if mission.schedule is not None:
        if profile.runner not in UNATTENDED_RUNNERS:
            blockers.append(
                "scheduled mission has no unattended runner "
                f"(runner is {profile.runner!r}; needs one of {sorted(UNATTENDED_RUNNERS)})"
            )
        if profile.runner == "hermes":
            # The hermes runner builds its own subprocess command and never
            # reads runner_command; it only needs the executable on PATH.
            if shutil.which("hermes") is None:
                blockers.append(
                    "scheduled mission runner executable not found on PATH: hermes"
                )
        elif not profile.runner_command:
            blockers.append("scheduled mission has no executable runner command")
        if mission.schedule.state.value != "scheduled":
            notices.append(
                f"schedule state is {mission.schedule.state.value}"
                + (
                    f" ({mission.schedule.paused_reason})"
                    if mission.schedule.paused_reason
                    else ""
                )
            )
    return {
        "scope": "mission",
        "mission_id": mission.id,
        "ready": not blockers,
        "capabilities": items,
        "blockers": blockers,
        "notices": notices,
        "next_actions": _next_actions(items, blockers),
    }


def _next_actions(items: list[dict[str, Any]], blockers: list[str]) -> list[str]:
    actions: list[str] = []
    for item in items:
        if item["status"] == "ready":
            continue
        actions.append(f"{item['name']}: {item['fix']}")
    for blocker in blockers:
        if blocker.startswith("scheduled mission"):
            actions.append(
                "configure an unattended runner via mission_configure "
                "(execution_profile.runner + runner_command) or capability setup"
            )
            break
    if not actions:
        actions.append("run the next generation")
    return actions
