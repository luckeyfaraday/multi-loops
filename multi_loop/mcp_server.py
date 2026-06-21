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
from .mcp_runs import MANAGER, mcp_runs_dir
from .models import to_dict
from .onboarding import OnboardingEngine, format_capability_brief
from .orchestrator import MissionOrchestrator
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
    except MissionNotFound as exc:
        return {"error": str(exc), "mission_id": exc.mission_id}


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
    except MissionNotFound as exc:
        return {"error": str(exc), "mission_id": exc.mission_id}


def run_generation_blocking_impl(
    mission_id: str,
    *,
    root: str = DEFAULT_ROOT,
    runner: str | None = None,
    runner_command: str | None = None,
    workspace: str | None = None,
    verify_timeout: float | None = None,
) -> dict[str, Any]:
    store = _store(root)
    try:
        result = MissionOrchestrator(store=store, workspace=workspace).run_generation(
            mission_id,
            runner_name=runner,
            runner_command=runner_command,
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
        "workspace": workspace,
    }

    def thunk(emit):
        emit("generation_started", {"mission_id": mission_id, "runner": runner})
        result = run_generation_blocking_impl(
            mission_id,
            root=root,
            runner=runner,
            runner_command=runner_command,
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
            workspace=workspace,
            verify_timeout=verify_timeout,
        )
    return run_generation_blocking_impl(
        mission_id,
        root=root,
        runner=runner,
        runner_command=runner_command,
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


def list_backends_impl() -> dict[str, Any]:
    capabilities = default_capabilities()
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
        "notes": "MVP runners are local: mock, shell, and agent_command.",
    }


def capability_list_impl(*, available_only: bool = False) -> dict[str, Any]:
    registry = default_capabilities()
    cards = [registry.describe(name) for name in registry.names()]
    if available_only:
        cards = [card for card in cards if card["available"]]
    return {"capabilities": cards, "count": len(cards)}


def capability_search_impl(
    query: str,
    *,
    limit: int = 5,
    include_unavailable: bool = False,
) -> dict[str, Any]:
    registry = default_capabilities()
    return {
        "query": query,
        "results": registry.search_cards(query, limit=limit, include_unavailable=include_unavailable),
    }


def capability_describe_impl(name: str) -> dict[str, Any]:
    registry = default_capabilities()
    if registry.get(name) is None:
        return {"error": f"Unknown capability: {name}", "name": name}
    return registry.describe(name)


def toolset_list_impl() -> dict[str, Any]:
    registry = default_capabilities()
    return {"toolsets": [registry.describe_toolset(name) for name in registry.toolset_names()]}


def toolset_resolve_impl(names: list[str] | str) -> dict[str, Any]:
    registry = default_capabilities()
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
        "backends": list_backends_impl(),
        "recommendations": [
            "Install with the optional MCP extra to run the server: pip install -e '.[mcp]'.",
            "run_generation detaches by default; monitor with run_status/run_tail/run_result.",
            "Mission state is stored under .multi-loop/runs; MCP run logs are under .multi-loop/mcp-runs.",
        ],
    }


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("multi-loop")

    @mcp.tool()
    def onboard(
        mission: str = "",
        root: str = DEFAULT_ROOT,
        answers: dict[str, str] | None = None,
        create: bool = True,
    ) -> dict[str, Any]:
        """Build an onboarding plan and optionally create a mission."""
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
    def run_generation(
        mission_id: str,
        root: str = DEFAULT_ROOT,
        runner: str | None = None,
        runner_command: str | None = None,
        workspace: str | None = None,
        verify_timeout: float | None = None,
        detach: bool = True,
    ) -> dict[str, Any]:
        """Run one mission generation; detached by default and monitorable by run_id.

        Pass runner_command (e.g. 'claude -p') to drive real agent/shell runners.
        """
        return run_generation_impl(
            mission_id,
            root=root,
            runner=runner,
            runner_command=runner_command,
            workspace=workspace,
            verify_timeout=verify_timeout,
            detach=detach,
        )

    @mcp.tool()
    def mission_status(mission_id: str, root: str = DEFAULT_ROOT) -> dict[str, Any]:
        """Return mission state plus ledger/event counts."""
        return mission_status_impl(mission_id, root=root)

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
    def list_backends() -> dict[str, Any]:
        """List configured local runners and capability availability."""
        return list_backends_impl()

    @mcp.tool()
    def capability_list(available_only: bool = False) -> dict[str, Any]:
        """List capability cards, optionally only those currently available."""
        return capability_list_impl(available_only=available_only)

    @mcp.tool()
    def capability_search(
        query: str,
        limit: int = 5,
        include_unavailable: bool = False,
    ) -> dict[str, Any]:
        """Search capability cards by token overlap for on-demand discovery."""
        return capability_search_impl(query, limit=limit, include_unavailable=include_unavailable)

    @mcp.tool()
    def capability_describe(name: str) -> dict[str, Any]:
        """Return the full capability card, including availability and missing env."""
        return capability_describe_impl(name)

    @mcp.tool()
    def toolset_list() -> dict[str, Any]:
        """List capability toolsets with their resolved members."""
        return toolset_list_impl()

    @mcp.tool()
    def toolset_resolve(names: list[str]) -> dict[str, Any]:
        """Resolve toolset/capability names (and all/*) to a flat capability list."""
        return toolset_resolve_impl(names)

    @mcp.tool()
    def doctor(root: str = DEFAULT_ROOT, cwd: str | None = None) -> dict[str, Any]:
        """Diagnose storage, optional MCP SDK presence, and local runner metadata."""
        return doctor_impl(root=root, cwd=cwd)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
