"""Expose the multi-loop mission runtime as an optional MCP server.

The `*_impl` functions are plain Python so tests and CLI callers do not need the
MCP SDK. `build_server()` is the only place that imports FastMCP.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from .capabilities import default_capabilities
from .capability_config import configured_capabilities
from .index import MissionIndex
from .main_agent import MainLoopService
from .mcp_runs import MANAGER, mcp_runs_dir
from .models import to_dict
from .onboarding import OnboardingEngine, format_capability_brief
from .orchestrator import MissionOrchestrator
from .reports import render_mission_report
from .runners import default_runner_registry
from .scheduler import MissionScheduler
from .storage import MissionNotFound, MissionStore

DEFAULT_ROOT = ".multi-loop"


def _store(root: str | Path = DEFAULT_ROOT) -> MissionStore:
    return MissionStore(root)


def _error(exc: BaseException) -> dict[str, Any]:
    return {"error": f"{type(exc).__name__}: {exc}", "summary": f"failed: {exc}"}


def _mission_payload(store: MissionStore, mission_id: str) -> dict[str, Any]:
    mission = store.load_mission(mission_id)
    return {
        "mission": to_dict(mission),
        "mission_dir": str(store.mission_dir(mission_id)),
        "ledger_count": len(store.read_ledger(mission_id)),
        "event_count": len(store.read_events(mission_id)),
    }


def main_loop_open_impl(
    *,
    root: str = DEFAULT_ROOT,
    session_id: str | None = None,
    mission_seed: str = "",
    provider_id: str | None = None,
    interface: str = "mcp",
) -> dict[str, Any]:
    """Open or resume the durable main-loop agent session."""
    try:
        return MainLoopService(root).open(
            interface=interface,
            provider_id=provider_id,
            mission_seed=mission_seed,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001 - MCP boundary returns structured failures
        return _error(exc)


def main_loop_context_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
    recent_limit: int = 30,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).context(session_id, recent_limit=recent_limit)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_list_impl(*, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    try:
        return {"sessions": to_dict(MainLoopService(root).sessions.list())}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_pause_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).pause(session_id, expected_revision=expected_revision)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_resume_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).resume(session_id, expected_revision=expected_revision)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_record_turn_impl(
    session_id: str,
    user_message: str,
    assistant_message: str,
    *,
    root: str = DEFAULT_ROOT,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).record_turn(
            session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_checkpoint_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
    summary: str = "",
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).checkpoint(
            session_id,
            summary=summary,
            decisions=decisions,
            open_questions=open_questions,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main_loop_compact_impl(
    session_id: str,
    summary: str,
    *,
    root: str = DEFAULT_ROOT,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).compact(
            session_id,
            summary=summary,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_draft_update_impl(
    session_id: str,
    patch: dict[str, Any],
    *,
    root: str = DEFAULT_ROOT,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).update_draft(
            session_id,
            patch,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_draft_validate_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).validate(session_id)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_confirm_impl(
    session_id: str,
    *,
    root: str = DEFAULT_ROOT,
    confirmed_by: str = "user",
    expected_revision: int | None = None,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).confirm(
            session_id,
            confirmed_by=confirmed_by,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def capability_setup_plan_impl(
    session_id: str,
    capability_names: list[str],
    *,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).capability_setup_plan(session_id, capability_names)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def capability_setup_apply_impl(
    session_id: str,
    capability_names: list[str],
    confirmation_quote: str,
    *,
    root: str = DEFAULT_ROOT,
    approved_by: str = "user",
) -> dict[str, Any]:
    try:
        return MainLoopService(root).capability_setup_apply(
            session_id,
            capability_names,
            confirmation_quote=confirmation_quote,
            approved_by=approved_by,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def capability_add_command_impl(
    session_id: str,
    name: str,
    description: str,
    command: str,
    side_effect_class: str,
    confirmation_quote: str,
    *,
    root: str = DEFAULT_ROOT,
    runner: str = "agent_command",
    approved_by: str = "user",
) -> dict[str, Any]:
    try:
        return MainLoopService(root).add_command_capability(
            session_id,
            name=name,
            description=description,
            command=command,
            side_effect_class=side_effect_class,
            confirmation_quote=confirmation_quote,
            runner=runner,
            approved_by=approved_by,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_capability_setup_plan_impl(
    mission_id: str,
    capability_names: list[str],
    *,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    try:
        return MainLoopService(root).mission_capability_setup_plan(
            mission_id, capability_names
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_capability_setup_apply_impl(
    mission_id: str,
    capability_names: list[str],
    confirmation_quote: str,
    *,
    root: str = DEFAULT_ROOT,
    approved_by: str = "user",
) -> dict[str, Any]:
    try:
        return MainLoopService(root).mission_capability_setup_apply(
            mission_id,
            capability_names,
            confirmation_quote=confirmation_quote,
            approved_by=approved_by,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_readiness_impl(
    *,
    root: str = DEFAULT_ROOT,
    mission_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Report capability gaps and blockers for a mission or a draft session."""
    try:
        service = MainLoopService(root)
        if mission_id:
            return service.mission_readiness(mission_id)
        if session_id:
            return service.readiness(session_id)
        return {"error": "Provide mission_id or session_id.", "summary": "failed: no target"}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_configure_impl(
    mission_id: str,
    patch: dict[str, Any],
    *,
    root: str = DEFAULT_ROOT,
    changed_by: str = "operator",
) -> dict[str, Any]:
    try:
        mission = MissionOrchestrator(store=_store(root)).configure_mission(
            mission_id, patch, changed_by=changed_by
        )
        return {
            "mission": to_dict(mission),
            "summary": "mission configuration updated: " + ", ".join(sorted(patch)),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_pause_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
    reason: str | None = None,
) -> dict[str, Any]:
    try:
        mission = MissionOrchestrator(store=_store(root)).pause_schedule(
            mission_id, reason=reason
        )
        return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_resume_impl(mission_id: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    try:
        mission = MissionOrchestrator(store=_store(root)).resume_schedule(mission_id)
        return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_trigger_impl(mission_id: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    try:
        mission = MissionOrchestrator(store=_store(root)).trigger_schedule(mission_id)
        return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def onboard_impl(
    mission: str = "",
    *,
    root: str = DEFAULT_ROOT,
    answers: dict[str, str] | None = None,
    create: bool = True,
) -> dict[str, Any]:
    """Build an onboarding plan, optionally creating the mission."""
    engine = OnboardingEngine()
    collected = engine.default_answers(mission)
    if mission:
        collected["mission_statement"] = mission
    if answers:
        collected.update(answers)

    plan = engine.build_plan(collected)
    mission_payload = None
    if create:
        schedule = plan.clarifications.get("schedule")
        if schedule and schedule.lower() == "no schedule yet":
            schedule = None
        store = _store(root)
        created = MissionOrchestrator(store=store).create_mission(
            plan.mission_statement,
            plan.success_criteria,
            schedule=schedule,
            clarifications=plan.clarifications,
        )
        mission_payload = {
            "mission_id": created.id,
            "mission_dir": str(store.mission_dir(created.id)),
            "mission": to_dict(created),
        }

    return {
        "created": mission_payload is not None,
        "mission": mission_payload,
        "onboarding_plan": to_dict(plan),
        "capability_brief": format_capability_brief(plan),
    }


def create_mission_impl(
    statement: str,
    success_criteria: str = "Make measurable progress and produce durable artifacts.",
    *,
    root: str = DEFAULT_ROOT,
    schedule: str | None = None,
    clarifications: dict[str, str] | None = None,
) -> dict[str, Any]:
    store = _store(root)
    mission = MissionOrchestrator(store=store).create_mission(
        statement,
        success_criteria,
        schedule=schedule,
        clarifications=clarifications,
    )
    return {
        "mission_id": mission.id,
        "mission_dir": str(store.mission_dir(mission.id)),
        "mission": to_dict(mission),
    }


def mission_status_impl(mission_id: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    try:
        return _mission_payload(_store(root), mission_id)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def mission_report_impl(mission_id: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    try:
        store = _store(root)
        mission = store.load_mission(mission_id)
        return {
            "mission_id": mission_id,
            "report": render_mission_report(mission, store.read_permissions(mission_id)),
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def list_missions_impl(*, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    store = _store(root)
    return {
        "missions": [
            {
                "id": mission.id,
                "statement": mission.statement,
                "success_criteria": mission.success_criteria,
                "generation_count": len(mission.generations),
                "updated_at": mission.updated_at,
            }
            for mission in store.list_missions()
        ],
        "root": str(store.root),
    }


def approve_capability_impl(
    mission_id: str,
    capability: str,
    *,
    root: str = DEFAULT_ROOT,
    approved_by: str = "user",
) -> dict[str, Any]:
    try:
        mission = MissionOrchestrator(store=_store(root)).approve_capability(
            mission_id,
            capability,
            approved_by=approved_by,
        )
        return {"mission_id": mission.id, "approvals": mission.approvals}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def generation_prepare_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    """Plan work for execution by the MCP host agent without invoking another model."""
    store = _store(root)
    try:
        generation = MissionOrchestrator(store=store).prepare_generation(mission_id)
        return {
            "mission_id": mission_id,
            "generation": to_dict(generation),
            "summary": f"generation {generation.index} is ready for host-agent execution",
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def candidate_claim_impl(
    mission_id: str,
    generation_index: int,
    candidate_id: str,
    *,
    root: str = DEFAULT_ROOT,
    claimant_id: str = "mcp_host",
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Atomically claim and policy-check one host-agent candidate."""
    try:
        claim = MissionOrchestrator(store=_store(root)).claim_candidate(
            mission_id,
            generation_index,
            candidate_id,
            claimant_id=claimant_id,
            claim_token=claim_token,
        )
        return {
            "claim": to_dict(claim),
            "summary": claim.block_reason or f"candidate {candidate_id} claimed",
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def candidate_submit_result_impl(
    mission_id: str,
    generation_index: int,
    candidate_id: str,
    success: bool,
    summary: str,
    *,
    root: str = DEFAULT_ROOT,
    output: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    submission_id: str | None = None,
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Submit structured evidence from work executed by the MCP host agent."""
    try:
        candidate = MissionOrchestrator(store=_store(root)).submit_candidate_result(
            mission_id,
            generation_index,
            candidate_id,
            success=success,
            summary=summary,
            output=output,
            artifacts=artifacts,
            metadata=metadata,
            submission_id=submission_id,
            claim_token=claim_token,
        )
        return {
            "mission_id": mission_id,
            "generation_index": generation_index,
            "candidate": to_dict(candidate),
            "summary": f"candidate {candidate_id} result recorded",
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def candidate_artifact_write_impl(
    mission_id: str,
    generation_index: int,
    candidate_id: str,
    claim_token: str,
    filename: str,
    content: str,
    *,
    root: str = DEFAULT_ROOT,
    kind: str = "text",
    description: str = "Host-agent artifact",
) -> dict[str, Any]:
    """Write host evidence into a candidate-scoped path and return its reference."""
    try:
        artifact = MissionOrchestrator(store=_store(root)).write_candidate_artifact(
            mission_id,
            generation_index,
            candidate_id,
            claim_token=claim_token,
            filename=filename,
            content=content,
            kind=kind,
            description=description,
        )
        return {"artifact": to_dict(artifact), "summary": f"artifact written: {artifact.path}"}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def generation_finalize_impl(
    mission_id: str,
    generation_index: int,
    *,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    """Finalize selection and synthesis after every candidate is terminal."""
    store = _store(root)
    try:
        result = MissionOrchestrator(store=store).finalize_generation(
            mission_id,
            generation_index,
        )
        return {
            "mission_id": mission_id,
            "generation_index": generation_index,
            "result": to_dict(result),
            "mission": to_dict(store.load_mission(mission_id)),
            "summary": f"generation {generation_index} finalized",
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def run_generation_blocking_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
    runner: str | None = None,
    runner_command: str | None = None,
    allow_side_effects: bool = False,
    verification: list[str] | None = None,
    workspace: str | None = None,
    verify_timeout: float | None = None,
) -> dict[str, Any]:
    store = _store(root)
    try:
        result = MissionOrchestrator(
            store=store,
            workspace=workspace,
            lessons_index=MissionIndex(store.root),
        ).run_generation(
            mission_id,
            runner_name=runner,
            runner_command=runner_command,
            allow_side_effects=allow_side_effects,
            verification=verification,
            verify_timeout_seconds=verify_timeout,
        )
        mission = store.load_mission(mission_id)
    except Exception as exc:  # noqa: BLE001 - MCP callers need JSON, not torn calls
        return _error(exc)

    return {
        "mission_id": mission_id,
        "generation_index": result.generation_index,
        "result": to_dict(result),
        "mission": to_dict(mission),
        "summary": (
            f"generation {result.generation_index} completed with "
            f"{len(result.selected_loop_ids)} selected candidate(s)"
        ),
    }


def run_generation_start_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
    runner: str | None = None,
    runner_command: str | None = None,
    allow_side_effects: bool = False,
    verification: list[str] | None = None,
    workspace: str | None = None,
    verify_timeout: float | None = None,
) -> dict[str, Any]:
    store = _store(root)
    try:
        mission = store.load_mission(mission_id)
    except MissionNotFound as exc:
        return {"error": str(exc), "mission_id": exc.mission_id}

    meta = {
        "mission_id": mission_id,
        "statement": mission.statement,
        "root": str(store.root),
        "runner": runner,
        "runner_command": runner_command,
        "allow_side_effects": allow_side_effects,
        "workspace": workspace,
    }

    def thunk(emit):
        emit("generation_started", {"mission_id": mission_id, "runner": runner})
        result = run_generation_blocking_impl(
            mission_id,
            root=root,
            runner=runner,
            runner_command=runner_command,
            allow_side_effects=allow_side_effects,
            verification=verification,
            workspace=workspace,
            verify_timeout=verify_timeout,
        )
        emit(
            "generation_finished",
            {
                "mission_id": mission_id,
                "generation_index": result.get("generation_index"),
                "summary": result.get("summary"),
                "error": result.get("error"),
            },
        )
        return result

    handle = MANAGER.start(thunk=thunk, meta=meta, base=mcp_runs_dir(root))
    events_path = handle.run_dir / "events.jsonl"
    return {
        "status": "running",
        "run_id": handle.run_id,
        "run_dir": str(handle.run_dir),
        "events_path": str(events_path),
        "message": "Generation started in the background; poll run_status, run_tail, and run_result.",
    }


def run_generation_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
    runner: str | None = None,
    runner_command: str | None = None,
    allow_side_effects: bool = False,
    verification: list[str] | None = None,
    workspace: str | None = None,
    verify_timeout: float | None = None,
    detach: bool = True,
) -> dict[str, Any]:
    if detach:
        return run_generation_start_impl(
            mission_id,
            root=root,
            runner=runner,
            runner_command=runner_command,
            allow_side_effects=allow_side_effects,
            verification=verification,
            workspace=workspace,
            verify_timeout=verify_timeout,
        )
    return run_generation_blocking_impl(
        mission_id,
        root=root,
        runner=runner,
        runner_command=runner_command,
        allow_side_effects=allow_side_effects,
        verification=verification,
        workspace=workspace,
        verify_timeout=verify_timeout,
    )


def run_status_impl(run_id: str) -> dict[str, Any]:
    return MANAGER.status(run_id)


def run_tail_impl(run_id: str, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
    return MANAGER.tail(run_id, cursor, limit)


def run_result_impl(run_id: str, wait: bool = False, timeout: float | None = None) -> dict[str, Any]:
    return MANAGER.result(run_id, wait, timeout)


def run_list_impl() -> dict[str, Any]:
    return MANAGER.list_runs()


def tick_impl(*, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    return to_dict(MissionScheduler(store=_store(root)).tick())


def list_backends_impl(*, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    capabilities = configured_capabilities(root)
    return {
        "runners": default_runner_registry().names(),
        "capabilities": [
            {
                "name": capability.name,
                "available": capabilities.available(capability.name),
                "side_effect_class": capability.side_effect_class.value,
            }
            for capability in capabilities.list()
        ],
        "notes": (
            "Runners are local: mock, shell, and agent_command. Configured command capabilities "
            "and Codex OAuth can supply unattended execution."
        ),
    }


def capability_list_impl(
    *, root: str = DEFAULT_ROOT, available_only: bool = False
) -> dict[str, Any]:
    registry = configured_capabilities(root)
    cards = [registry.describe(name) for name in registry.names()]
    if available_only:
        cards = [card for card in cards if card["available"]]
    return {"capabilities": cards, "count": len(cards)}


def capability_search_impl(
    query: str,
    *,
    limit: int = 5,
    include_unavailable: bool = False,
    root: str = DEFAULT_ROOT,
) -> dict[str, Any]:
    registry = configured_capabilities(root)
    return {
        "query": query,
        "results": registry.search_cards(query, limit=limit, include_unavailable=include_unavailable),
    }


def capability_describe_impl(name: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    registry = configured_capabilities(root)
    if registry.get(name) is None:
        return {"error": f"Unknown capability: {name}", "name": name}
    return registry.describe(name)


def toolset_list_impl(*, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    registry = configured_capabilities(root)
    return {"toolsets": [registry.describe_toolset(name) for name in registry.toolset_names()]}


def toolset_resolve_impl(
    names: list[str] | str, *, root: str = DEFAULT_ROOT
) -> dict[str, Any]:
    registry = configured_capabilities(root)
    requested = [names] if isinstance(names, str) else list(names)
    try:
        resolved = registry.resolve_names(requested)
    except KeyError as exc:
        return {"error": str(exc), "names": requested}
    return {
        "names": requested,
        "resolved": resolved,
        "available": [name for name in resolved if registry.available(name)],
    }


def search_impl(
    query: str,
    *,
    root: str = DEFAULT_ROOT,
    missions: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    index = MissionIndex(root)
    index.rebuild(_store(root))  # derived index; refresh from JSON before querying
    if missions:
        return {"query": query, "missions": index.search_missions(query, limit=limit)}
    return {"query": query, "hits": to_dict(index.search_ledger(query, limit=limit))}


def lessons_list_impl(*, root: str = DEFAULT_ROOT, limit: int = 20) -> dict[str, Any]:
    index = MissionIndex(root)
    index.rebuild(_store(root))
    lessons = index.list_lessons(limit=limit)
    return {"lessons": to_dict(lessons), "count": len(lessons)}


def lessons_search_impl(
    query: str, *, root: str = DEFAULT_ROOT, limit: int = 20
) -> dict[str, Any]:
    index = MissionIndex(root)
    index.rebuild(_store(root))
    lessons = index.search_lessons(query, limit=limit)
    return {"query": query, "lessons": to_dict(lessons), "count": len(lessons)}


def lineage_impl(candidate_id: str, *, root: str = DEFAULT_ROOT) -> dict[str, Any]:
    index = MissionIndex(root)
    index.rebuild(_store(root))
    return {"candidate_id": candidate_id, "ancestors": index.lineage(candidate_id)}


def doctor_impl(root: str = DEFAULT_ROOT, cwd: str | None = None) -> dict[str, Any]:
    root_path = Path(root)
    cwd_status: dict[str, Any] = {"path": cwd, "provided": cwd is not None}
    if cwd:
        cwd_status.update(
            {
                "exists": os.path.isdir(cwd),
                "readable": os.access(cwd, os.R_OK),
                "writable": os.access(cwd, os.W_OK),
            }
        )
    parent = root_path if root_path.exists() else root_path.parent
    return {
        "ok": True,
        "server": {
            "python": sys.executable,
            "package_dir": str(Path(__file__).parent),
            "mcp_sdk_installed": importlib.util.find_spec("mcp") is not None,
        },
        "storage": {
            "root": str(root_path),
            "exists": root_path.exists(),
            "runs_dir": str(root_path / "runs"),
            "mcp_runs_dir": str(mcp_runs_dir(root_path)),
            "parent_writable": os.access(parent, os.W_OK),
        },
        "cwd": cwd_status,
        "backends": list_backends_impl(root=root),
        "recommendations": [
            "Install with the optional MCP extra to run the server: pip install -e '.[mcp]'.",
            "run_generation detaches by default; monitor with run_status/run_tail/run_result.",
            "Mission state is stored under .multi-loop/runs; MCP run logs are under .multi-loop/mcp-runs.",
        ],
    }


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "multi-loop",
        instructions=(
            "You are the mission OPERATOR: the user states the mission and stays hands-off; you "
            "own preparation, configuration, execution, and supervision, and every change you make "
            "is recorded in the mission ledger. "
            "For interactive onboarding, always start with main_loop_open; do not use the legacy "
            "onboard tool. Build the mission through mission_draft_update. Search capabilities, "
            "then call capability_setup_plan for every required tool or backend. Show the plan and "
            "ask the user before capability_setup_apply or capability_add_command. Check "
            "mission_readiness before mission_confirm, before the first generation, and whenever "
            "capabilities change; work through its gaps instead of running blind. Do not call "
            "mission_confirm until mission_draft_validate reports valid, all required capabilities "
            "are available and approved, and scheduled missions have a real unattended runner. "
            "After creation, reconfigure the mission yourself with mission_configure (budget, "
            "schedule, runner, verification, capabilities) and manage schedules with mission_pause, "
            "mission_resume, and mission_trigger. Only two things stay with the user: the mission "
            "statement itself and side-effect approvals. "
            "The root argument is the multi-loop state directory (normally <workspace>/.multi-loop), "
            "not the workspace itself. The MCP host agent conducts the conversation and execution."
        ),
    )

    @mcp.tool()
    def main_loop_open(
        root: str = DEFAULT_ROOT,
        session_id: str | None = None,
        mission_seed: str = "",
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        """Open or resume the main-loop agent.

        The MCP host agent conducts the conversation. Use the returned stable
        system prompt and context, then persist durable decisions with
        main_loop_checkpoint and mission_draft_update.
        """
        return main_loop_open_impl(
            root=root,
            session_id=session_id,
            mission_seed=mission_seed,
            provider_id=provider_id,
            interface="mcp",
        )

    @mcp.tool()
    def main_loop_context(
        session_id: str,
        root: str = DEFAULT_ROOT,
        recent_limit: int = 30,
    ) -> dict[str, Any]:
        """Rebuild bounded durable context for an existing main-loop session."""
        return main_loop_context_impl(session_id, root=root, recent_limit=recent_limit)

    @mcp.tool()
    def main_loop_list(root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """List durable main-loop sessions available for resumption."""
        return main_loop_list_impl(root=root)

    @mcp.tool()
    def main_loop_pause(
        session_id: str,
        root: str = DEFAULT_ROOT,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Pause a main-loop conversation without deleting its state."""
        return main_loop_pause_impl(
            session_id, root=root, expected_revision=expected_revision
        )

    @mcp.tool()
    def main_loop_resume(
        session_id: str,
        root: str = DEFAULT_ROOT,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Resume a paused main-loop conversation from durable state."""
        return main_loop_resume_impl(
            session_id, root=root, expected_revision=expected_revision
        )

    @mcp.tool()
    def main_loop_record_turn(
        session_id: str,
        user_message: str,
        assistant_message: str,
        root: str = DEFAULT_ROOT,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist one completed host-agent conversation turn for later resume."""
        return main_loop_record_turn_impl(
            session_id,
            user_message,
            assistant_message,
            root=root,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    def main_loop_checkpoint(
        session_id: str,
        root: str = DEFAULT_ROOT,
        summary: str = "",
        decisions: list[str] | None = None,
        open_questions: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist a resumable summary and confirmed decisions; summaries are not authority."""
        return main_loop_checkpoint_impl(
            session_id,
            root=root,
            summary=summary,
            decisions=decisions,
            open_questions=open_questions,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    def main_loop_compact(
        session_id: str,
        summary: str,
        root: str = DEFAULT_ROOT,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Append a compaction checkpoint while retaining the full append-only transcript."""
        return main_loop_compact_impl(
            session_id,
            summary,
            root=root,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    def mission_draft_update(
        session_id: str,
        patch: dict[str, Any],
        root: str = DEFAULT_ROOT,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Update confirmed mission-draft fields; unknown fields and stale revisions fail."""
        return mission_draft_update_impl(
            session_id,
            patch,
            root=root,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    def mission_draft_validate(
        session_id: str,
        root: str = DEFAULT_ROOT,
    ) -> dict[str, Any]:
        """Deterministically validate a mission draft before asking for confirmation."""
        return mission_draft_validate_impl(session_id, root=root)

    @mcp.tool()
    def mission_confirm(
        session_id: str,
        root: str = DEFAULT_ROOT,
        confirmed_by: str = "user",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Create the mission only after the user explicitly confirms the valid draft."""
        return mission_confirm_impl(
            session_id,
            root=root,
            confirmed_by=confirmed_by,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    def capability_setup_plan(
        session_id: str,
        capability_names: list[str],
        root: str = DEFAULT_ROOT,
    ) -> dict[str, Any]:
        """Plan required capability/config changes without applying them.

        Show this plan to the user and ask permission before applying it.
        """
        return capability_setup_plan_impl(
            session_id, capability_names, root=root
        )

    @mcp.tool()
    def capability_setup_apply(
        session_id: str,
        capability_names: list[str],
        confirmation_quote: str,
        root: str = DEFAULT_ROOT,
        approved_by: str = "user",
    ) -> dict[str, Any]:
        """Apply an available capability setup after explicit user confirmation."""
        return capability_setup_apply_impl(
            session_id,
            capability_names,
            confirmation_quote,
            root=root,
            approved_by=approved_by,
        )

    @mcp.tool()
    def capability_add_command(
        session_id: str,
        name: str,
        description: str,
        command: str,
        side_effect_class: str,
        confirmation_quote: str,
        root: str = DEFAULT_ROOT,
        runner: str = "agent_command",
        approved_by: str = "user",
    ) -> dict[str, Any]:
        """Persist a user-approved command as a new capability and add it to the draft.

        Never embed credentials in the command. Ask the user before calling this tool.
        """
        return capability_add_command_impl(
            session_id,
            name,
            description,
            command,
            side_effect_class,
            confirmation_quote,
            root=root,
            runner=runner,
            approved_by=approved_by,
        )

    @mcp.tool()
    def mission_capability_setup_plan(
        mission_id: str,
        capability_names: list[str],
        root: str = DEFAULT_ROOT,
    ) -> dict[str, Any]:
        """Plan capability and runner changes for an existing mission without applying them."""
        return mission_capability_setup_plan_impl(
            mission_id, capability_names, root=root
        )

    @mcp.tool()
    def mission_capability_setup_apply(
        mission_id: str,
        capability_names: list[str],
        confirmation_quote: str,
        root: str = DEFAULT_ROOT,
        approved_by: str = "user",
    ) -> dict[str, Any]:
        """Apply approved capability and unattended-runner changes to an existing mission."""
        return mission_capability_setup_apply_impl(
            mission_id,
            capability_names,
            confirmation_quote,
            root=root,
            approved_by=approved_by,
        )

    @mcp.tool()
    def onboard(
        mission: str = "",
        root: str = DEFAULT_ROOT,
        answers: dict[str, str] | None = None,
        create: bool = True,
    ) -> dict[str, Any]:
        """Legacy deterministic onboarding for scripts; do not use for interactive onboarding."""
        return onboard_impl(mission, root=root, answers=answers, create=create)

    @mcp.tool()
    def create_mission(
        statement: str,
        success_criteria: str = "Make measurable progress and produce durable artifacts.",
        root: str = DEFAULT_ROOT,
        schedule: str | None = None,
        clarifications: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create and persist a mission."""
        return create_mission_impl(
            statement,
            success_criteria,
            root=root,
            schedule=schedule,
            clarifications=clarifications,
        )

    @mcp.tool()
    def generation_prepare(
        mission_id: str,
        root: str = DEFAULT_ROOT,
    ) -> dict[str, Any]:
        """Prepare a generation for this MCP host agent to execute.

        This tool never invokes a nested LLM. Call candidate_claim for each
        candidate, perform the work with your own tools, submit the evidence,
        then call generation_finalize.
        """
        return generation_prepare_impl(mission_id, root=root)

    @mcp.tool()
    def candidate_claim(
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        root: str = DEFAULT_ROOT,
        claimant_id: str = "mcp_host",
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Claim one candidate and receive its policy safety directive."""
        return candidate_claim_impl(
            mission_id,
            generation_index,
            candidate_id,
            root=root,
            claimant_id=claimant_id,
            claim_token=claim_token,
        )

    @mcp.tool()
    def candidate_submit_result(
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        success: bool,
        summary: str,
        root: str = DEFAULT_ROOT,
        output: str = "",
        artifacts: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        submission_id: str | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Persist the host agent's structured result and evidence for a claimed candidate."""
        return candidate_submit_result_impl(
            mission_id,
            generation_index,
            candidate_id,
            success,
            summary,
            root=root,
            output=output,
            artifacts=artifacts,
            metadata=metadata,
            submission_id=submission_id,
            claim_token=claim_token,
        )

    @mcp.tool()
    def candidate_artifact_write(
        mission_id: str,
        generation_index: int,
        candidate_id: str,
        claim_token: str,
        filename: str,
        content: str,
        root: str = DEFAULT_ROOT,
        kind: str = "text",
        description: str = "Host-agent artifact",
    ) -> dict[str, Any]:
        """Write candidate evidence to a safe mission path before submitting its result."""
        return candidate_artifact_write_impl(
            mission_id,
            generation_index,
            candidate_id,
            claim_token,
            filename,
            content,
            root=root,
            kind=kind,
            description=description,
        )

    @mcp.tool()
    def generation_finalize(
        mission_id: str,
        generation_index: int,
        root: str = DEFAULT_ROOT,
    ) -> dict[str, Any]:
        """Deterministically score, select, and synthesize a completed host-agent generation."""
        return generation_finalize_impl(mission_id, generation_index, root=root)

    @mcp.tool()
    def run_generation(
        mission_id: str,
        root: str = DEFAULT_ROOT,
        runner: str | None = None,
        runner_command: str | None = None,
        allow_side_effects: bool = False,
        verification: list[str] | None = None,
        workspace: str | None = None,
        verify_timeout: float | None = None,
        detach: bool = True,
    ) -> dict[str, Any]:
        """Run one mission generation; detached by default and monitorable by run_id.

        Pass runner_command (e.g. 'claude -p') to drive real agent/shell runners.
        Side effects require capability-scoped mission approval; the legacy
        allow_side_effects flag cannot bypass that policy. Pass verification
        commands to make success reflect evidence rather than runner exit code.
        """
        return run_generation_impl(
            mission_id,
            root=root,
            runner=runner,
            runner_command=runner_command,
            allow_side_effects=allow_side_effects,
            verification=verification,
            workspace=workspace,
            verify_timeout=verify_timeout,
            detach=detach,
        )

    @mcp.tool()
    def mission_status(mission_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Return mission state plus ledger/event counts."""
        return mission_status_impl(mission_id, root=root)

    @mcp.tool()
    def mission_report(mission_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Render the user-facing executive report for a mission.

        Show this to the user instead of raw status when they ask how the
        mission is going: it covers progress, evidence, authority, items
        needing their decision, and what happens next.
        """
        return mission_report_impl(mission_id, root=root)

    @mcp.tool()
    def list_missions(root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """List persisted missions."""
        return list_missions_impl(root=root)

    @mcp.tool()
    def approve_capability(
        mission_id: str,
        capability: str,
        root: str = DEFAULT_ROOT,
        approved_by: str = "user",
    ) -> dict[str, Any]:
        """Approve a side-effecting capability for a mission."""
        return approve_capability_impl(
            mission_id,
            capability,
            root=root,
            approved_by=approved_by,
        )

    @mcp.tool()
    def mission_readiness(
        root: str = DEFAULT_ROOT,
        mission_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Report per-capability readiness gaps, blockers, and next actions.

        Pass session_id for a draft under onboarding or mission_id for a
        created mission. Run this before confirming, before the first
        generation, and after any capability or approval change.
        """
        return mission_readiness_impl(root=root, mission_id=mission_id, session_id=session_id)

    @mcp.tool()
    def mission_configure(
        mission_id: str,
        patch: dict[str, Any],
        root: str = DEFAULT_ROOT,
        changed_by: str = "operator",
    ) -> dict[str, Any]:
        """Reconfigure a mission as its operator; every change is ledger-audited.

        Patch keys: success_criteria, clarifications (empty value deletes a
        key), budget, schedule (null clears it), execution_profile (runner,
        runner_command, verification, workspace, autonomy_level), and
        selected_capabilities. The mission statement and side-effect approvals
        cannot be changed here.
        """
        return mission_configure_impl(mission_id, patch, root=root, changed_by=changed_by)

    @mcp.tool()
    def mission_pause(
        mission_id: str,
        root: str = DEFAULT_ROOT,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Pause a mission's schedule so ticks skip it until resumed."""
        return mission_pause_impl(mission_id, root=root, reason=reason)

    @mcp.tool()
    def mission_resume(mission_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Resume a paused mission schedule and recompute its next run."""
        return mission_resume_impl(mission_id, root=root)

    @mcp.tool()
    def mission_trigger(mission_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Mark a scheduled mission due now so the next tick runs a generation."""
        return mission_trigger_impl(mission_id, root=root)

    @mcp.tool()
    def tick(root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Run scheduled mission ticks that are currently due."""
        return tick_impl(root=root)

    @mcp.tool()
    def run_status(run_id: str) -> dict[str, Any]:
        """Light status for a detached generation run."""
        return run_status_impl(run_id)

    @mcp.tool()
    def run_tail(run_id: str, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read detached run events with seq greater than cursor."""
        return run_tail_impl(run_id, cursor, limit)

    @mcp.tool()
    def run_result(run_id: str, wait: bool = False, timeout: float | None = None) -> dict[str, Any]:
        """Fetch a detached run result, or running status if it is not done."""
        return run_result_impl(run_id, wait, timeout)

    @mcp.tool()
    def run_list() -> dict[str, Any]:
        """List detached runs started by this server process."""
        return run_list_impl()

    @mcp.tool()
    def list_backends(root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """List configured local runners and capability availability."""
        return list_backends_impl(root=root)

    @mcp.tool()
    def capability_list(
        root: str = DEFAULT_ROOT, available_only: bool = False
    ) -> dict[str, Any]:
        """List capability cards, optionally only those currently available."""
        return capability_list_impl(root=root, available_only=available_only)

    @mcp.tool()
    def capability_search(
        query: str,
        root: str = DEFAULT_ROOT,
        limit: int = 5,
        include_unavailable: bool = False,
    ) -> dict[str, Any]:
        """Search capability cards by token overlap for on-demand discovery."""
        return capability_search_impl(
            query, root=root, limit=limit, include_unavailable=include_unavailable
        )

    @mcp.tool()
    def capability_describe(
        name: str, root: str = DEFAULT_ROOT
    ) -> dict[str, Any]:
        """Return the full capability card, including availability and missing env."""
        return capability_describe_impl(name, root=root)

    @mcp.tool()
    def toolset_list(root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """List capability toolsets with their resolved members."""
        return toolset_list_impl(root=root)

    @mcp.tool()
    def toolset_resolve(
        names: list[str], root: str = DEFAULT_ROOT
    ) -> dict[str, Any]:
        """Resolve toolset/capability names (and all/*) to a flat capability list."""
        return toolset_resolve_impl(names, root=root)

    @mcp.tool()
    def search(
        query: str,
        root: str = DEFAULT_ROOT,
        missions: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the mission ledger (or mission statements) across all missions."""
        return search_impl(query, root=root, missions=missions, limit=limit)

    @mcp.tool()
    def lessons_list(root: str = DEFAULT_ROOT, limit: int = 20) -> dict[str, Any]:
        """List learned failed-loop lessons across all missions."""
        return lessons_list_impl(root=root, limit=limit)

    @mcp.tool()
    def lessons_search(
        query: str,
        root: str = DEFAULT_ROOT,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search learned failed-loop lessons across all missions."""
        return lessons_search_impl(query, root=root, limit=limit)

    @mcp.tool()
    def lineage(candidate_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Return a candidate loop's ancestry (parents, grandparents, ...)."""
        return lineage_impl(candidate_id, root=root)

    @mcp.tool()
    def doctor(root: str = DEFAULT_ROOT, cwd: str | None = None) -> dict[str, Any]:
        """Diagnose storage, optional MCP SDK presence, and local runner metadata."""
        return doctor_impl(root=root, cwd=cwd)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
