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
from .capabilities import CapabilityRegistry, default_capabilities
from .models import Budget, ExecutionProfile, to_dict, utc_now_iso
from .orchestrator import MissionOrchestrator
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
    }
)


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
        self.capabilities = capabilities or default_capabilities()

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
        session = self.sessions.load(session_id)
        errors = self._validate_draft(session)
        warnings: list[str] = []
        unavailable: list[str] = []
        for name in session.draft.requested_capabilities:
            capability = self.capabilities.get(name)
            if capability is not None and not self.capabilities.available(name):
                unavailable.append(name)
                warnings.append(
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

    def confirm(
        self,
        session_id: str,
        *,
        confirmed_by: str = "user",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.load(session_id)
        if expected_revision is not None and session.revision != expected_revision:
            from .agent_sessions import SessionConflict

            raise SessionConflict(session_id, expected_revision, session.revision)
        validation = self.validate(session_id)
        if not validation["valid"]:
            raise ValueError("Mission draft is not valid: " + "; ".join(validation["errors"]))
        if session.active_mission_id:
            mission = self.missions.load_mission(session.active_mission_id)
            return {"created": False, "mission": to_dict(mission), "context": self.context(session_id)}

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
            runner="native_tool_loop" if controller == "cli_agent" else "mcp_host_tools",
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
        mission = MissionOrchestrator(store=self.missions, capabilities=self.capabilities).create_mission(
            session.draft.statement.strip(),
            session.draft.success_criteria.strip(),
            schedule=session.draft.schedule,
            clarifications=clarifications,
            execution_profile=profile,
            selected_capabilities=list(session.draft.requested_capabilities),
            onboarding_session_id=session.id,
            budget=session.draft.budget,
        )

        def mutate(current: MainLoopSession) -> None:
            current.active_mission_id = mission.id
            current.phase = AgentPhase.ACTIVE
            current.draft.confirmed_at = utc_now_iso()

        self.sessions.mutate(
            session_id,
            mutate,
            expected_revision=session.revision,
            entry_type="mission_confirmed",
            data={"mission_id": mission.id, "confirmed_by": confirmed_by},
        )
        return {"created": True, "mission": to_dict(mission), "context": self.context(session_id)}

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
        if draft.schedule:
            try:
                parse_schedule(draft.schedule)
            except ValueError as exc:
                errors.append(str(exc))
        for field_name in ("max_iterations", "max_seconds", "max_cost_usd", "max_tokens"):
            value = getattr(draft.budget, field_name)
            if value is not None and value <= 0:
                errors.append(f"budget.{field_name} must be positive")
        return errors

    def _normalize_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in patch.items():
            if key in {"statement", "success_criteria", "autonomy_level", "approval_policy"}:
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
