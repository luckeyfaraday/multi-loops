"""Application service for the durable main-loop agent protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_sessions import (
    AgentInterface,
    AgentPhase,
    AgentSessionStatus,
    MainLoopSession,
    MainLoopSessionStore,
    MissionDraft,
)
from .capabilities import CapabilityRegistry
from .capability_config import ConfiguredCapabilityStore, configured_capabilities
from .models import (
    Budget,
    Event,
    ExecutionProfile,
    LedgerEntry,
    Mission,
    SideEffectClass,
    to_dict,
    utc_now_iso,
)
from .orchestrator import MissionOrchestrator
from .policy import APPROVAL_REQUIRED
from .readiness import capability_readiness_items, mission_readiness_report
from .schedule_util import parse_schedule
from .storage import MissionStore


MAIN_LOOP_SYSTEM_PROMPT = """You are the main-loop agent for a durable mission runtime.

You may talk with the user for as long as useful. Gradually turn confirmed intent into a
structured mission draft; do not interrogate the user or invent missing preferences. Ask only
about unknowns that materially change the mission. Search capability cards before recommending
tools. Record durable decisions through main-loop tools instead of relying on chat memory.

Creating a mission, starting execution, changing schedules, and approving external actions are
distinct operations. Never claim that an operation happened unless its tool returned success.
Show the complete draft and wait for a later user message that explicitly confirms it before
calling mission confirmation; an initial task request is not confirmation of the derived draft.
External writes, publishing, messaging people, and spending require capability-scoped approval.
Before confirming a mission, identify every capability and execution prerequisite it needs. If a
capability, dependency, tool, credential, runner, or schedule backend is missing, explain the
concrete configuration change and ask whether the user wants you to apply it. Use capability setup
tools only after explicit approval. Do not call onboarding complete while required capabilities are
unavailable or a scheduled mission lacks a real unattended runner.
During onboarding, remain read-only except for the main-loop session and mission draft.
"""


_DRAFT_FIELDS = frozenset(
    {
        "statement",
        "success_criteria",
        "clarifications",
        "requested_capabilities",
        "schedule",
        "budget",
        "autonomy_level",
        "approval_policy",
        "workspace",
        "execution_runner",
        "runner_command",
        "verification",
    }
)


class _MissionAlreadyConfirmed(RuntimeError):
    def __init__(self, mission_id: str) -> None:
        super().__init__(mission_id)
        self.mission_id = mission_id


class MainLoopService:
    """State machine shared by MCP-hosted and standalone CLI agents."""

    def __init__(
        self,
        root: str | Path = ".multi-loop",
        *,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        self.root = Path(root)
        self.sessions = MainLoopSessionStore(self.root)
        self.missions = MissionStore(self.root)
        self.capabilities = capabilities or configured_capabilities(self.root)

    def open(
        self,
        *,
        interface: str | AgentInterface = AgentInterface.MCP,
        provider_id: str | None = None,
        mission_seed: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if session_id:
            return self.context(session_id)
        session = MainLoopSession(
            interface=AgentInterface(interface),
            provider_id=provider_id,
            system_prompt=MAIN_LOOP_SYSTEM_PROMPT,
            draft=MissionDraft(statement=mission_seed.strip()),
        )
        self.sessions.create(session)
        self.sessions.append_entry(
            session.id,
            "session_opened",
            {"interface": session.interface.value, "mission_seed": mission_seed.strip()},
        )
        return self.context(session.id)

    def context(self, session_id: str, *, recent_limit: int = 30) -> dict[str, Any]:
        session = self.sessions.load(session_id)
        entries = self.sessions.read_entries(session_id)
        latest_compaction = -1
        for index, entry in enumerate(entries):
            if entry.entry_type == "compaction":
                latest_compaction = index
        recent_start = max(latest_compaction + 1, len(entries) - max(1, recent_limit))
        recent = entries[recent_start:]
        validation = self.validate(session_id)
        return {
            "session": to_dict(session),
            "system_prompt": session.system_prompt,
            "working_summary": session.working_summary,
            "recent_entries": to_dict(recent),
            "validation": validation,
            "capabilities": [
                {
                    "name": name,
                    "description": self.capabilities.require(name).description,
                    "available": self.capabilities.available(name),
                    "side_effect_class": self.capabilities.require(name).side_effect_class.value,
                }
                for name in self.capabilities.names()
            ],
            "next_actions": self._next_actions(session, validation),
        }

    def record_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.append_message(
            session_id,
            "user",
            user_message,
            expected_revision=expected_revision,
        )
        self.sessions.append_message(session_id, "assistant", assistant_message)
        return self.context(session_id)

    def checkpoint(
        self,
        session_id: str,
        *,
        summary: str = "",
        decisions: list[str] | None = None,
        open_questions: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        clean_decisions = _clean_strings(decisions or [])
        clean_questions = _clean_strings(open_questions or [])

        def mutate(session: MainLoopSession) -> None:
            if summary.strip():
                session.working_summary = summary.strip()
            for decision in clean_decisions:
                if decision not in session.confirmed_decisions:
                    session.confirmed_decisions.append(decision)
            session.open_questions = clean_questions

        self.sessions.mutate(
            session_id,
            mutate,
            expected_revision=expected_revision,
            entry_type="checkpoint",
            data=lambda session: {
                "summary": session.working_summary,
                "decisions": clean_decisions,
                "open_questions": clean_questions,
            },
        )
        return self.context(session_id)

    def compact(
        self,
        session_id: str,
        *,
        summary: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not summary.strip():
            raise ValueError("Compaction summary must not be empty.")

        def mutate(session: MainLoopSession) -> None:
            session.working_summary = summary.strip()

        self.sessions.mutate(
            session_id,
            mutate,
            expected_revision=expected_revision,
            entry_type="compaction",
            data=lambda session: {
                "summary": session.working_summary,
                "revision_before": session.revision,
            },
        )
        return self.context(session_id)

    def update_draft(
        self,
        session_id: str,
        patch: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        unknown = sorted(set(patch) - _DRAFT_FIELDS)
        if unknown:
            raise ValueError(f"Unknown mission draft field(s): {', '.join(unknown)}")

        normalized = self._normalize_patch(patch)

        def mutate(session: MainLoopSession) -> None:
            if session.active_mission_id:
                raise ValueError("Mission draft is already committed.")
            for key, value in normalized.items():
                setattr(session.draft, key, value)
            session.draft.confirmed_at = None
            session.phase = AgentPhase.SCOPING
            errors = self._validate_draft(session)
            if not errors:
                session.phase = AgentPhase.READY_TO_CREATE

        self.sessions.mutate(
            session_id,
            mutate,
            expected_revision=expected_revision,
            entry_type="draft_updated",
            data={"patch": normalized},
        )
        return self.context(session_id)

    def validate(self, session_id: str) -> dict[str, Any]:
        return self._validation_for_session(self.sessions.load(session_id))

    def _validation_for_session(self, session: MainLoopSession) -> dict[str, Any]:
        errors = self._validate_draft(session)
        warnings: list[str] = []
        unavailable: list[str] = []
        for name in session.draft.requested_capabilities:
            capability = self.capabilities.get(name)
            if capability is not None and not self.capabilities.available(name):
                unavailable.append(name)
                errors.append(
                    f"Capability {name} is unavailable: "
                    f"{capability.availability_check or 'requires setup'}."
                )
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "unavailable_capabilities": unavailable,
            "ready_to_create": not errors and session.active_mission_id is None,
        }

    def readiness(self, session_id: str) -> dict[str, Any]:
        """Report draft readiness: capability gaps, blockers, and next actions.

        ``ready`` mirrors draft validation (the authoritative confirm gate);
        the per-capability items give the operator the actionable detail —
        missing env vars, setup hints, and approval state — needed to close
        each gap conversationally before the mission is confirmed.
        """
        session = self.sessions.load(session_id)
        validation = self._validation_for_session(session)
        items = capability_readiness_items(
            self.capabilities,
            session.draft.requested_capabilities,
            session.draft.capability_approvals,
        )
        next_actions = [
            f"{item['name']}: {item['fix']}" for item in items if item["status"] != "ready"
        ]
        for error in validation["errors"]:
            if "capabilit" not in error.lower():
                next_actions.append(f"resolve: {error}")
        return {
            "scope": "draft",
            "session_id": session_id,
            "ready": validation["valid"],
            "capabilities": items,
            "blockers": validation["errors"],
            "next_actions": next_actions or ["confirm the mission after explicit user approval"],
        }

    def mission_readiness(self, mission_id: str) -> dict[str, Any]:
        """Report readiness for a created mission before running a generation."""
        mission = self.missions.load_mission(mission_id)
        return mission_readiness_report(mission, self.capabilities)

    def capability_setup_plan(
        self,
        session_id: str,
        capability_names: list[str],
    ) -> dict[str, Any]:
        """Describe concrete changes and approvals before mutating configuration."""
        session = self.sessions.load(session_id)
        requested = _clean_strings(capability_names)
        unknown = [name for name in requested if self.capabilities.get(name) is None]
        cards = [self.capabilities.describe(name) for name in requested if name not in unknown]
        unavailable = [card for card in cards if not card["available"]]
        side_effects = [
            card for card in cards if SideEffectClass(card["side_effect_class"]) in APPROVAL_REQUIRED
        ]
        additions = [name for name in requested if name not in session.draft.requested_capabilities]
        runner_card = next((card for card in cards if card.get("runner_command")), None)
        if session.draft.schedule and runner_card is None:
            codex = self.capabilities.describe("codex_oauth_runner")
            if codex["available"]:
                runner_card = codex
                if "codex_oauth_runner" not in additions and "codex_oauth_runner" not in session.draft.requested_capabilities:
                    additions.append("codex_oauth_runner")
            else:
                unavailable.append(codex)
        if session.draft.schedule and "scheduled_tick" not in additions and "scheduled_tick" not in session.draft.requested_capabilities:
            additions.append("scheduled_tick")
        return {
            "session_id": session_id,
            "requested": requested,
            "capability_cards": cards,
            "changes": {
                "add_to_mission": additions,
                "execution_runner": runner_card.get("runner") if runner_card else None,
                "runner_command": runner_card.get("runner_command") if runner_card else None,
            },
            "side_effect_approvals": [
                {
                    "capability": card["name"],
                    "side_effect_class": card["side_effect_class"],
                    "scope_must_be_confirmed": True,
                }
                for card in side_effects
            ],
            "unavailable": unavailable,
            "unknown": unknown,
            "requires_user_approval": bool(additions or side_effects or runner_card),
            "can_apply": not unknown and not unavailable,
            "instruction": (
                "Show these changes and side-effect scopes to the user. Call capability_setup_apply "
                "only after the user explicitly agrees."
            ),
        }

    def capability_setup_apply(
        self,
        session_id: str,
        capability_names: list[str],
        *,
        confirmation_quote: str,
        approved_by: str = "user",
    ) -> dict[str, Any]:
        if not confirmation_quote.strip():
            raise ValueError("Explicit user confirmation is required before changing capability config.")
        plan = self.capability_setup_plan(session_id, capability_names)
        if not plan["can_apply"]:
            blockers = [*plan["unknown"], *[card["name"] for card in plan["unavailable"]]]
            raise ValueError("Capability setup has unresolved prerequisites: " + ", ".join(blockers))

        def mutate(session: MainLoopSession) -> None:
            for name in plan["changes"]["add_to_mission"]:
                if name not in session.draft.requested_capabilities:
                    session.draft.requested_capabilities.append(name)
            for approval in plan["side_effect_approvals"]:
                session.draft.capability_approvals[approval["capability"]] = approved_by.strip()
            if plan["changes"]["execution_runner"]:
                session.draft.execution_runner = plan["changes"]["execution_runner"]
                session.draft.runner_command = plan["changes"]["runner_command"]
            session.draft.confirmed_at = None
            session.phase = AgentPhase.SCOPING

        self.sessions.mutate(
            session_id,
            mutate,
            entry_type="capability_setup_applied",
            data={
                "capabilities": plan["changes"]["add_to_mission"],
                "side_effect_approvals": plan["side_effect_approvals"],
                "confirmation_quote": confirmation_quote.strip(),
                "approved_by": approved_by.strip(),
                "execution_runner": plan["changes"]["execution_runner"],
            },
        )
        return self.context(session_id)

    def add_command_capability(
        self,
        session_id: str,
        *,
        name: str,
        description: str,
        command: str,
        side_effect_class: str,
        confirmation_quote: str,
        runner: str = "agent_command",
        approved_by: str = "user",
    ) -> dict[str, Any]:
        """Add a user-approved command tool to persistent multi-loop config."""
        configured = ConfiguredCapabilityStore(self.root).add_command(
            name=name,
            description=description,
            command=command,
            side_effect_class=side_effect_class,
            configured_by=approved_by,
            approval_evidence=confirmation_quote,
            runner=runner,
        )
        self.capabilities = configured_capabilities(self.root)
        context = self.capability_setup_apply(
            session_id,
            [configured.capability.name],
            confirmation_quote=confirmation_quote,
            approved_by=approved_by,
        )
        return {"configured_capability": to_dict(configured), "context": context}

    def mission_capability_setup_plan(
        self,
        mission_id: str,
        capability_names: list[str],
    ) -> dict[str, Any]:
        """Plan capability and runner changes for an already-created mission."""
        mission = self.missions.load_mission(mission_id)
        requested = _clean_strings(capability_names)
        unknown = [name for name in requested if self.capabilities.get(name) is None]
        cards = [self.capabilities.describe(name) for name in requested if name not in unknown]
        unavailable = [card for card in cards if not card["available"]]
        additions = [name for name in requested if name not in mission.selected_capabilities]
        runner_card = next((card for card in cards if card.get("runner_command")), None)
        if mission.schedule and runner_card is None:
            codex = self.capabilities.describe("codex_oauth_runner")
            if codex["available"]:
                runner_card = codex
                if "codex_oauth_runner" not in additions:
                    additions.append("codex_oauth_runner")
            else:
                unavailable.append(codex)
        if mission.schedule and "scheduled_tick" not in additions and "scheduled_tick" not in mission.selected_capabilities:
            additions.append("scheduled_tick")
        approvals = [
            card
            for card in cards
            if SideEffectClass(card["side_effect_class"]) in APPROVAL_REQUIRED
            and card["name"] not in mission.approvals
        ]
        return {
            "mission_id": mission_id,
            "changes": {
                "add_to_mission": additions,
                "execution_runner": runner_card.get("runner") if runner_card else None,
                "runner_command": runner_card.get("runner_command") if runner_card else None,
            },
            "side_effect_approvals": [
                {"capability": card["name"], "side_effect_class": card["side_effect_class"]}
                for card in approvals
            ],
            "unknown": unknown,
            "unavailable": unavailable,
            "can_apply": not unknown and not unavailable,
            "requires_user_approval": bool(additions or approvals or runner_card),
        }

    def mission_capability_setup_apply(
        self,
        mission_id: str,
        capability_names: list[str],
        *,
        confirmation_quote: str,
        approved_by: str = "user",
    ) -> dict[str, Any]:
        if not confirmation_quote.strip():
            raise ValueError("Explicit user confirmation is required before changing mission config.")
        plan = self.mission_capability_setup_plan(mission_id, capability_names)
        if not plan["can_apply"]:
            blockers = [*plan["unknown"], *[card["name"] for card in plan["unavailable"]]]
            raise ValueError("Mission capability setup has unresolved prerequisites: " + ", ".join(blockers))
        mission = self.missions.load_mission(mission_id)
        for name in plan["changes"]["add_to_mission"]:
            if name not in mission.selected_capabilities:
                mission.selected_capabilities.append(name)
        for approval in plan["side_effect_approvals"]:
            mission.approvals[approval["capability"]] = approved_by.strip()
        if plan["changes"]["execution_runner"]:
            mission.execution_profile.runner = plan["changes"]["execution_runner"]
            mission.execution_profile.runner_command = plan["changes"]["runner_command"]
        self.missions.append_event(
            Event(
                mission_id=mission.id,
                event_type="mission_capability_setup_applied",
                data={
                    "capabilities": plan["changes"]["add_to_mission"],
                    "side_effect_approvals": plan["side_effect_approvals"],
                    "execution_runner": plan["changes"]["execution_runner"],
                    "confirmation_quote": confirmation_quote.strip(),
                },
            )
        )
        entry = LedgerEntry(
            mission_id=mission.id,
            event_type="mission_capability_setup_applied",
            summary=(
                "Configured capabilities: "
                + ", ".join(plan["changes"]["add_to_mission"] or capability_names)
            ),
        )
        self.missions.append_ledger(entry)
        mission.ledger.append(entry.id)
        self.missions.save_mission(mission)
        return {"mission": to_dict(mission), "plan": plan}

    def confirm(
        self,
        session_id: str,
        *,
        confirmed_by: str = "user",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        mission: Mission | None = None

        def mutate(current: MainLoopSession) -> None:
            nonlocal mission
            if current.active_mission_id:
                raise _MissionAlreadyConfirmed(current.active_mission_id)
            validation = self._validation_for_session(current)
            if not validation["valid"]:
                raise ValueError(
                    "Mission draft is not valid: " + "; ".join(validation["errors"])
                )

            # Mission creation occurs while the session revision lock is held. If
            # a prior attempt created the mission but crashed before updating the
            # session snapshot, recover that mission instead of creating another.
            mission = next(
                (
                    item
                    for item in self.missions.list_missions()
                    if item.onboarding_session_id == current.id
                ),
                None,
            )
            if mission is None:
                mission = self._create_mission_from_session(current, confirmed_by)
            current.active_mission_id = mission.id
            current.phase = AgentPhase.ACTIVE
            current.draft.confirmed_at = utc_now_iso()

        try:
            self.sessions.mutate(
                session_id,
                mutate,
                expected_revision=expected_revision,
                entry_type="mission_confirmed",
                data=lambda _session: {
                    "mission_id": mission.id if mission is not None else "",
                    "confirmed_by": confirmed_by,
                },
            )
        except _MissionAlreadyConfirmed as exc:
            existing = self.missions.load_mission(exc.mission_id)
            return {
                "created": False,
                "mission": to_dict(existing),
                "context": self.context(session_id),
            }
        assert mission is not None
        return {"created": True, "mission": to_dict(mission), "context": self.context(session_id)}

    def _create_mission_from_session(
        self,
        session: MainLoopSession,
        confirmed_by: str,
    ) -> Mission:
        controller = "mcp_host" if session.interface == AgentInterface.MCP else "cli_agent"
        provider_model = None
        if session.interface == AgentInterface.CLI and session.provider_id:
            try:
                from .providers import ProviderStore

                provider_model = ProviderStore(self.root).load(session.provider_id).model
            except FileNotFoundError:
                # Service-level callers may use a logical provider reference;
                # the CLI chat path validates profiles before opening a turn.
                provider_model = None
        profile = ExecutionProfile(
            controller=controller,
            provider_id=session.provider_id,
            model=provider_model,
            runner=(
                session.draft.execution_runner
                or ("native_tool_loop" if controller == "cli_agent" else "mcp_host_tools")
            ),
            runner_command=session.draft.runner_command,
            verification=list(session.draft.verification),
            workspace=session.draft.workspace,
            autonomy_level=session.draft.autonomy_level,
        )
        clarifications = dict(session.draft.clarifications)
        clarifications.update(
            {
                "approval_policy": session.draft.approval_policy,
                "autonomy_level": session.draft.autonomy_level,
                "confirmed_by": confirmed_by,
            }
        )
        return MissionOrchestrator(
            store=self.missions, capabilities=self.capabilities
        ).create_mission(
            session.draft.statement.strip(),
            session.draft.success_criteria.strip(),
            schedule=session.draft.schedule,
            clarifications=clarifications,
            execution_profile=profile,
            selected_capabilities=list(session.draft.requested_capabilities),
            onboarding_session_id=session.id,
            budget=session.draft.budget,
            approvals=dict(session.draft.capability_approvals),
        )

    def pause(self, session_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        self.sessions.mutate(
            session_id,
            lambda session: setattr(session, "phase", AgentPhase.PAUSED),
            expected_revision=expected_revision,
            entry_type="session_paused",
            data={},
        )
        return self.context(session_id)

    def resume(self, session_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        def mutate(session: MainLoopSession) -> None:
            session.status = AgentSessionStatus.ACTIVE
            session.phase = AgentPhase.ACTIVE if session.active_mission_id else AgentPhase.SCOPING

        self.sessions.mutate(
            session_id,
            mutate,
            expected_revision=expected_revision,
            entry_type="session_resumed",
            data={},
        )
        return self.context(session_id)

    def _validate_draft(self, session: MainLoopSession) -> list[str]:
        draft = session.draft
        errors: list[str] = []
        if not draft.statement.strip():
            errors.append("mission statement is required")
        if not draft.success_criteria.strip():
            errors.append("success criteria are required")
        if session.interface == AgentInterface.CLI and not session.provider_id:
            errors.append("a provider connection is required for CLI sessions")
        unknown = [name for name in draft.requested_capabilities if self.capabilities.get(name) is None]
        if unknown:
            errors.append("unknown capabilities: " + ", ".join(sorted(set(unknown))))
        unapproved = [
            name
            for name in draft.requested_capabilities
            if self.capabilities.get(name) is not None
            and self.capabilities.require(name).side_effect_class in APPROVAL_REQUIRED
            and name not in draft.capability_approvals
        ]
        if unapproved:
            errors.append(
                "capabilities require explicit scoped approval: "
                + ", ".join(sorted(set(unapproved)))
            )
        if draft.schedule:
            try:
                parse_schedule(draft.schedule)
            except ValueError as exc:
                errors.append(str(exc))
            if draft.execution_runner not in {"agent_command", "shell"}:
                errors.append("scheduled missions require a configured unattended runner")
            if not draft.runner_command:
                errors.append("scheduled missions require an executable runner command")
        for field_name in ("max_iterations", "max_seconds", "max_cost_usd", "max_tokens"):
            value = getattr(draft.budget, field_name)
            if value is not None and value <= 0:
                errors.append(f"budget.{field_name} must be positive")
        if draft.budget.max_cost_usd is not None:
            errors.append(
                "budget.max_cost_usd is not supported without provider pricing; "
                "use max_tokens instead"
            )
        return errors

    def _normalize_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in patch.items():
            if key in {
                "statement",
                "success_criteria",
                "autonomy_level",
                "approval_policy",
                "execution_runner",
                "runner_command",
            }:
                normalized[key] = str(value).strip()
            elif key in {"schedule", "workspace"}:
                clean = str(value).strip() if value is not None else ""
                normalized[key] = clean or None
            elif key == "clarifications":
                if not isinstance(value, dict):
                    raise ValueError("clarifications must be an object")
                normalized[key] = {
                    str(item_key): str(item_value).strip()
                    for item_key, item_value in value.items()
                    if str(item_value).strip()
                }
            elif key == "requested_capabilities":
                if not isinstance(value, list):
                    raise ValueError("requested_capabilities must be a list")
                normalized[key] = _clean_strings(value)
            elif key == "verification":
                if not isinstance(value, list):
                    raise ValueError("verification must be a list")
                normalized[key] = _clean_strings(value)
            elif key == "budget":
                if not isinstance(value, dict):
                    raise ValueError("budget must be an object")
                allowed = {"max_iterations", "max_seconds", "max_cost_usd", "max_tokens"}
                unknown = sorted(set(value) - allowed)
                if unknown:
                    raise ValueError("Unknown budget field(s): " + ", ".join(unknown))
                normalized[key] = Budget(**value)
        return normalized

    @staticmethod
    def _next_actions(session: MainLoopSession, validation: dict[str, Any]) -> list[str]:
        if session.phase == AgentPhase.PAUSED:
            return ["resume the main-loop session"]
        if session.active_mission_id:
            return [
                "continue discussing or refining the active mission",
                "prepare the next generation",
                "request capability-scoped approval when required",
            ]
        if validation["errors"]:
            return ["continue the conversation and update the mission draft"]
        return ["show the user the mission draft", "confirm the mission after explicit approval"]


def _clean_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        if clean and clean not in result:
            result.append(clean)
    return result
