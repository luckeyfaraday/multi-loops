"""Policy gates and path-containment safety for candidate execution.

This module is the single home for the decisions that decide whether a
candidate loop may run: capability availability, side-effect approval gates,
and filesystem containment. Keeping it separate from portfolio planning lets
the orchestrator, scheduler, runners, and MCP layer share one policy surface.
"""

from __future__ import annotations

from pathlib import Path

from .capabilities import CapabilityRegistry
from .models import CandidateLoop, Mission, PolicyGate, SideEffectClass

# Side effects that reach outside the local mission workspace and therefore
# require an explicit, recorded approval before a candidate may run.
APPROVAL_REQUIRED = frozenset(
    {
        SideEffectClass.EXTERNAL_WRITE,
        SideEffectClass.PUBLIC_PUBLISH,
        SideEffectClass.SPEND_MONEY,
        SideEffectClass.MESSAGE_PERSON,
    }
)


def prepare_candidate(
    candidate: CandidateLoop,
    mission: Mission,
    capabilities: CapabilityRegistry,
) -> str | None:
    """Resolve policy gates and return a block reason when a run must not proceed."""
    attach_policy_gates(candidate, mission, capabilities)

    for ref in candidate.required_capabilities:
        capability = capabilities.get(ref.name)
        if capability is None:
            if ref.required:
                return f"Required capability is not registered: {ref.name}."
            continue
        if ref.required and not capabilities.available(ref.name):
            note = capability.availability_check or "requires setup"
            return f"Required capability unavailable: {ref.name} ({note})."

    return policy_block_reason(candidate.policy_gates)


def attach_policy_gates(
    candidate: CandidateLoop,
    mission: Mission,
    capabilities: CapabilityRegistry,
) -> None:
    """Attach approval gates for side-effecting capabilities and apply approvals."""
    gates_by_capability = {gate.capability: gate for gate in candidate.policy_gates}
    for ref in candidate.required_capabilities:
        capability = capabilities.get(ref.name)
        if capability is None or capability.side_effect_class not in APPROVAL_REQUIRED:
            continue
        approved_by = mission.approvals.get(ref.name)
        approved_at = None if approved_by is None else mission.updated_at
        gate = gates_by_capability.get(ref.name)
        if gate is None:
            candidate.policy_gates.append(
                PolicyGate(
                    capability=ref.name,
                    side_effect_class=capability.side_effect_class,
                    requires_approval=True,
                    approved_by=approved_by,
                    approved_at=approved_at,
                )
            )
        elif gate.approved_by is None and approved_by is not None:
            # An approval recorded after the gate was first attached should take effect.
            gate.approved_by = approved_by
            gate.approved_at = approved_at


def candidate_blocked_now(
    candidate: CandidateLoop,
    mission: Mission,
    capabilities: CapabilityRegistry,
) -> bool:
    """Whether the candidate would be blocked under the mission's current state.

    Mirrors the gating in ``prepare_candidate`` without mutating the candidate,
    so the planner can decide whether a previously discarded candidate is now
    runnable (e.g. its capability has since been approved).
    """
    for ref in candidate.required_capabilities:
        capability = capabilities.get(ref.name)
        if capability is None:
            if ref.required:
                return True
            continue
        if ref.required and not capabilities.available(ref.name):
            return True
        if (
            capability.side_effect_class in APPROVAL_REQUIRED
            and not mission.approvals.get(ref.name)
        ):
            return True
    return False


def has_approved_side_effect(
    candidate: CandidateLoop,
    mission: Mission,
    capabilities: CapabilityRegistry,
) -> bool:
    """Whether the candidate has an approved side-effecting capability."""
    for ref in candidate.required_capabilities:
        capability = capabilities.get(ref.name)
        if (
            capability is not None
            and capability.side_effect_class in APPROVAL_REQUIRED
            and mission.approvals.get(ref.name)
        ):
            return True
    return False


_DENY_SIDE_EFFECTS = (
    "SIDE EFFECTS: NONE PERMITTED. Stay read-only and local. Do NOT merge, close, "
    "publish, post, upload, send messages, spend money, delete, or otherwise mutate "
    "any remote or external service. Inspect, analyze, and plan only; leave every "
    "outward-facing or irreversible action to an approved execution loop."
)
_ALLOW_SIDE_EFFECTS = (
    "SIDE EFFECTS: APPROVED for this run. You may take the outward-facing actions the "
    "mission requires. For each such action you MUST return a verifiable handle (URL, "
    "object ID, status code, receipt, or absolute path) so the parent loop can confirm "
    "it actually happened — unconfirmed claims are treated as failures."
)


def side_effect_directive(
    candidate: CandidateLoop,
    mission: Mission,
    capabilities: CapabilityRegistry,
    *,
    allow_side_effects: bool = False,
) -> str:
    """Return the safety directive that bounds a spawned agent's outward actions.

    The default posture is deny: a candidate's runner is told to stay read-only
    and local regardless of what the spawned agent *could* do, so a nominally
    ``local_write`` candidate cannot quietly merge PRs or spend money. The
    constraint is lifted only when side effects are explicitly approved — via
    ``allow_side_effects`` or an approved side-effecting capability.
    """
    if allow_side_effects or has_approved_side_effect(candidate, mission, capabilities):
        return _ALLOW_SIDE_EFFECTS
    return _DENY_SIDE_EFFECTS


def policy_block_reason(policy_gates: list[PolicyGate]) -> str | None:
    for gate in policy_gates:
        if gate.requires_approval and not gate.approved_by:
            return (
                "Policy gate blocked candidate: "
                f"{gate.capability} requires approval for {gate.side_effect_class.value}."
            )
    return None


class PathEscape(ValueError):
    """Raised when a relative path would resolve outside its base directory."""

    def __init__(self, base: Path, relative: str) -> None:
        super().__init__(f"Path {relative!r} escapes its base directory: {base}")
        self.base = base
        self.relative = relative


def resolve_within(base: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``base`` and verify it stays inside ``base``.

    Catches absolute paths, ``..`` traversal, and symlinks that would lead out
    of ``base`` (resolution follows links). Returns the absolute target path.
    """
    base_resolved = Path(base).resolve()
    target = (base_resolved / relative).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise PathEscape(base_resolved, relative)
    return target
