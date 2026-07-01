"""Command-line interface for the MVP multi-loop runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_loop import MainLoopAgent
from .capability_config import configured_capabilities
from .index import MissionIndex
from .main_agent import MainLoopService
from .models import to_dict
from .onboarding import OnboardingEngine, collect_answers, format_capability_brief
from .orchestrator import MissionOrchestrator, ScheduleNotConfigured
from .providers import OpenAICompatibleClient, ProviderStore
from .scheduler import MissionScheduler
from .storage import MissionNotFound, MissionStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="multi-loop")
    parser.add_argument("--root", default=".multi-loop", help="Storage root directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a mission")
    create_parser.add_argument("statement", help="Mission statement")
    create_parser.add_argument(
        "--success-criteria",
        default="Make measurable progress and produce durable artifacts.",
        help="Mission-level success criteria",
    )
    create_parser.add_argument("--schedule", help="Optional schedule expression")

    onboard_parser = subparsers.add_parser("onboard", help="Run mission onboarding")
    onboard_parser.add_argument("--mission", default="", help="Optional mission statement seed")
    onboard_parser.add_argument(
        "--defaults",
        action="store_true",
        help="Use default answers after the mission seed instead of prompting",
    )
    onboard_parser.add_argument(
        "--no-create",
        action="store_true",
        help="Only print the onboarding plan; do not create a mission",
    )

    agent_parser = subparsers.add_parser("agent", help="Manage the durable main-loop agent")
    agent_commands = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_open = agent_commands.add_parser("open", help="Open a main-loop session")
    agent_open.add_argument("--interface", choices=["cli", "mcp"], default="mcp")
    agent_open.add_argument("--provider-id")
    agent_open.add_argument("--mission", default="", help="Optional mission statement seed")
    agent_open.add_argument("--session-id", help="Resume an existing session")
    agent_context = agent_commands.add_parser("context", help="Show resumable agent context")
    agent_context.add_argument("session_id")
    agent_context.add_argument("--recent-limit", type=int, default=30)
    agent_draft = agent_commands.add_parser("draft", help="Patch a mission draft with JSON")
    agent_draft.add_argument("session_id")
    agent_draft.add_argument("patch", help="JSON object containing confirmed draft fields")
    agent_draft.add_argument("--revision", type=int)
    agent_validate = agent_commands.add_parser("validate", help="Validate a mission draft")
    agent_validate.add_argument("session_id")
    agent_confirm = agent_commands.add_parser("confirm", help="Confirm and create the mission")
    agent_confirm.add_argument("session_id")
    agent_confirm.add_argument("--by", default="user")
    agent_confirm.add_argument("--revision", type=int)
    agent_checkpoint = agent_commands.add_parser("checkpoint", help="Persist resumable context")
    agent_checkpoint.add_argument("session_id")
    agent_checkpoint.add_argument("--summary", default="")
    agent_checkpoint.add_argument("--decision", action="append", default=[])
    agent_checkpoint.add_argument("--question", action="append", default=[])
    agent_checkpoint.add_argument("--revision", type=int)
    agent_commands.add_parser("list", help="List durable main-loop sessions")
    agent_pause = agent_commands.add_parser("pause", help="Pause a main-loop session")
    agent_pause.add_argument("session_id")
    agent_pause.add_argument("--revision", type=int)
    agent_resume = agent_commands.add_parser("resume", help="Resume a main-loop session")
    agent_resume.add_argument("session_id")
    agent_resume.add_argument("--revision", type=int)
    agent_chat = agent_commands.add_parser("chat", help="Talk to the native main-loop agent")
    agent_chat.add_argument("--provider-id", help="Connected provider profile")
    agent_chat.add_argument("--session-id", help="Resume an existing session")
    agent_chat.add_argument("--mission", default="", help="Optional mission statement seed")
    agent_chat.add_argument("--message", help="Run one non-interactive user turn")
    agent_chat.add_argument("--max-tool-iterations", type=int, default=12)

    provider_parser = subparsers.add_parser("provider", help="Manage native LLM provider profiles")
    provider_commands = provider_parser.add_subparsers(dest="provider_command", required=True)
    provider_connect = provider_commands.add_parser("connect", help="Connect a provider profile")
    provider_connect.add_argument("provider_id")
    provider_connect.add_argument(
        "--kind",
        choices=["openai", "openrouter", "openai_compatible"],
        default="openai",
    )
    provider_connect.add_argument("--model", required=True)
    provider_connect.add_argument("--base-url")
    provider_connect.add_argument(
        "--api-key-env",
        help="Environment variable containing the key; secret values are never stored",
    )
    provider_commands.add_parser("list", help="List provider profiles")
    provider_validate = provider_commands.add_parser("validate", help="Validate provider connectivity")
    provider_validate.add_argument("provider_id")
    provider_remove = provider_commands.add_parser("disconnect", help="Remove a provider profile")
    provider_remove.add_argument("provider_id")

    run_parser = subparsers.add_parser("run", help="Run one mission generation")
    run_parser.add_argument("mission_id", help="Mission ID")
    run_parser.add_argument(
        "--runner",
        help="Runner to force for planned candidates; defaults to the mission preference",
    )
    run_parser.add_argument(
        "--runner-command",
        help="Command for shell/agent_command runners, e.g. 'claude -p' or 'pytest -q'. "
        "Implies --runner agent_command unless --runner is given.",
    )
    run_parser.add_argument(
        "--allow-side-effects",
        action="store_true",
        help="Legacy compatibility flag. Outward actions still require a recorded "
        "approval for the candidate's specific side-effecting capability.",
    )
    run_parser.add_argument(
        "--verify",
        action="append",
        metavar="COMMAND",
        help="Verification command applied to each candidate (repeatable). When set, "
        "it is authoritative: success reflects the command's exit code, not the runner's.",
    )
    run_parser.add_argument("--workspace", help="Workspace for runners and verification commands")
    run_parser.add_argument("--verify-timeout", type=float, help="Verification timeout in seconds")

    status_parser = subparsers.add_parser("status", help="Show mission status")
    status_parser.add_argument("mission_id", help="Mission ID")

    subparsers.add_parser("list", help="List missions")

    approve_parser = subparsers.add_parser("approve", help="Approve a side-effecting capability")
    approve_parser.add_argument("mission_id", help="Mission ID")
    approve_parser.add_argument("capability", help="Capability name")
    approve_parser.add_argument("--by", default="user", help="Approver identity")

    pause_parser = subparsers.add_parser("pause", help="Pause a mission schedule")
    pause_parser.add_argument("mission_id", help="Mission ID")
    pause_parser.add_argument("--reason", help="Optional pause reason")

    resume_parser = subparsers.add_parser("resume", help="Resume a paused mission schedule")
    resume_parser.add_argument("mission_id", help="Mission ID")

    trigger_parser = subparsers.add_parser("trigger", help="Mark a mission schedule due now")
    trigger_parser.add_argument("mission_id", help="Mission ID")

    capabilities_parser = subparsers.add_parser("capabilities", help="List or search capability cards")
    capabilities_parser.add_argument("--search", help="Search capabilities by query")
    capabilities_parser.add_argument("--describe", help="Describe a single capability by name")
    capabilities_parser.add_argument(
        "--available", action="store_true", help="Only show currently available capabilities"
    )
    capabilities_parser.add_argument(
        "--include-unavailable",
        action="store_true",
        help="Include unavailable capabilities in search results",
    )

    toolsets_parser = subparsers.add_parser("toolsets", help="List or resolve capability toolsets")
    toolsets_parser.add_argument(
        "--resolve",
        help="Resolve toolset/capability names (comma- or space-separated, or all) to capabilities",
    )

    search_parser = subparsers.add_parser("search", help="Search the mission ledger (or missions) by text")
    search_parser.add_argument("query", help="Text to search for")
    search_parser.add_argument(
        "--missions", action="store_true", help="Search mission statements instead of the ledger"
    )
    search_parser.add_argument("--limit", type=int, default=20, help="Maximum results")

    lessons_parser = subparsers.add_parser("lessons", help="List or search learned failure lessons")
    lessons_commands = lessons_parser.add_subparsers(dest="lessons_command", required=True)
    lessons_list = lessons_commands.add_parser("list", help="List learned failure lessons")
    lessons_list.add_argument("--limit", type=int, default=20, help="Maximum results")
    lessons_search = lessons_commands.add_parser("search", help="Search learned failure lessons")
    lessons_search.add_argument("query", help="Text to search for")
    lessons_search.add_argument("--limit", type=int, default=20, help="Maximum results")

    lineage_parser = subparsers.add_parser("lineage", help="Show a candidate loop's ancestry")
    lineage_parser.add_argument("candidate_id", help="Candidate loop ID")

    subparsers.add_parser("tick", help="Run scheduled mission ticks that are due")

    args = parser.parse_args(argv)
    store = MissionStore(args.root)

    try:
        return _dispatch(args, store)
    except MissionNotFound as exc:
        print(f"Mission not found: {exc.mission_id}", file=sys.stderr)
        return 1
    except ScheduleNotConfigured as exc:
        print(f"Mission has no schedule: {exc.mission_id}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, store: MissionStore) -> int:
    if args.command == "provider":
        providers = ProviderStore(args.root)
        if args.provider_command == "connect":
            profile = providers.connect(
                args.provider_id,
                kind=args.kind,
                model=args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
            )
            _print_json({"connected": True, "provider": to_dict(profile)})
        elif args.provider_command == "list":
            _print_json({"providers": to_dict(providers.list())})
        elif args.provider_command == "validate":
            profile = providers.load(args.provider_id)
            _print_json(OpenAICompatibleClient(profile).validate())
        else:
            providers.remove(args.provider_id)
            _print_json({"disconnected": True, "provider_id": args.provider_id})
        return 0

    if args.command == "create":
        orchestrator = MissionOrchestrator(store=store)
        mission = orchestrator.create_mission(
            args.statement,
            args.success_criteria,
            schedule=args.schedule,
        )
        _print_json(
            {
                "mission_id": mission.id,
                "mission_dir": str(store.mission_dir(mission.id)),
                "mission": to_dict(mission),
            }
        )
        return 0

    if args.command == "onboard":
        engine = OnboardingEngine()
        questions = engine.questions(args.mission)
        if args.defaults:
            answers = engine.default_answers(args.mission)
            if args.mission:
                answers["mission_statement"] = args.mission
        else:
            answers = collect_answers(questions)
        plan = engine.build_plan(answers)
        mission_payload = None
        if not args.no_create:
            orchestrator = MissionOrchestrator(store=store)
            schedule = plan.clarifications.get("schedule")
            if schedule and schedule.lower() == "no schedule yet":
                schedule = None
            mission = orchestrator.create_mission(
                plan.mission_statement,
                plan.success_criteria,
                schedule=schedule,
                clarifications=plan.clarifications,
            )
            mission_payload = {
                "mission_id": mission.id,
                "mission_dir": str(store.mission_dir(mission.id)),
                "mission": to_dict(mission),
            }
        _print_json(
            {
                "capability_brief": format_capability_brief(plan),
                "created": mission_payload is not None,
                "mission": mission_payload,
                "onboarding_plan": to_dict(plan),
            }
        )
        return 0

    if args.command == "agent":
        service = MainLoopService(args.root)
        if args.agent_command == "chat":
            return _chat(args, service)
        if args.agent_command == "list":
            payload = {"sessions": to_dict(service.sessions.list())}
        elif args.agent_command == "pause":
            payload = service.pause(args.session_id, expected_revision=args.revision)
        elif args.agent_command == "resume":
            payload = service.resume(args.session_id, expected_revision=args.revision)
        elif args.agent_command == "open":
            payload = service.open(
                interface=args.interface,
                provider_id=args.provider_id,
                mission_seed=args.mission,
                session_id=args.session_id,
            )
        elif args.agent_command == "context":
            payload = service.context(args.session_id, recent_limit=args.recent_limit)
        elif args.agent_command == "draft":
            patch = json.loads(args.patch)
            if not isinstance(patch, dict):
                raise ValueError("Mission draft patch must be a JSON object.")
            payload = service.update_draft(
                args.session_id,
                patch,
                expected_revision=args.revision,
            )
        elif args.agent_command == "validate":
            payload = service.validate(args.session_id)
        elif args.agent_command == "confirm":
            payload = service.confirm(
                args.session_id,
                confirmed_by=args.by,
                expected_revision=args.revision,
            )
        else:
            payload = service.checkpoint(
                args.session_id,
                summary=args.summary,
                decisions=args.decision,
                open_questions=args.question,
                expected_revision=args.revision,
            )
        _print_json(payload)
        return 0

    if args.command == "run":
        workspace = Path(args.workspace).resolve() if args.workspace else None
        orchestrator = MissionOrchestrator(
            store=store,
            workspace=workspace,
            lessons_index=MissionIndex(store.root),
        )
        result = orchestrator.run_generation(
            args.mission_id,
            runner_name=args.runner,
            runner_command=args.runner_command,
            allow_side_effects=args.allow_side_effects,
            verification=args.verify,
            verify_timeout_seconds=args.verify_timeout,
        )
        _print_json(to_dict(result))
        return 0

    if args.command == "status":
        mission = store.load_mission(args.mission_id)
        _print_json(
            {
                "mission": to_dict(mission),
                "ledger_count": len(store.read_ledger(args.mission_id)),
                "event_count": len(store.read_events(args.mission_id)),
                "mission_dir": str(store.mission_dir(args.mission_id)),
            }
        )
        return 0

    if args.command == "list":
        _print_json(
            {
                "missions": [
                    {
                        "id": mission.id,
                        "statement": mission.statement,
                        "success_criteria": mission.success_criteria,
                        "generation_count": len(mission.generations),
                        "updated_at": mission.updated_at,
                    }
                    for mission in store.list_missions()
                ]
            }
        )
        return 0

    if args.command == "approve":
        orchestrator = MissionOrchestrator(store=store)
        mission = orchestrator.approve_capability(
            args.mission_id,
            args.capability,
            approved_by=args.by,
        )
        _print_json({"mission_id": mission.id, "approvals": mission.approvals})
        return 0

    if args.command in {"pause", "resume", "trigger"}:
        orchestrator = MissionOrchestrator(store=store)
        if args.command == "pause":
            mission = orchestrator.pause_schedule(args.mission_id, reason=args.reason)
        elif args.command == "resume":
            mission = orchestrator.resume_schedule(args.mission_id)
        else:
            mission = orchestrator.trigger_schedule(args.mission_id)
        _print_json({"mission_id": mission.id, "schedule": to_dict(mission.schedule)})
        return 0

    if args.command == "capabilities":
        registry = configured_capabilities(args.root)
        if args.describe:
            if registry.get(args.describe) is None:
                print(f"Unknown capability: {args.describe}", file=sys.stderr)
                return 1
            _print_json(registry.describe(args.describe))
        elif args.search:
            _print_json(
                {
                    "query": args.search,
                    "results": registry.search_cards(
                        args.search, include_unavailable=args.include_unavailable
                    ),
                }
            )
        else:
            cards = [registry.describe(name) for name in registry.names()]
            if args.available:
                cards = [card for card in cards if card["available"]]
            _print_json({"capabilities": cards, "count": len(cards)})
        return 0

    if args.command == "toolsets":
        registry = configured_capabilities(args.root)
        if args.resolve:
            names = args.resolve.replace(",", " ").split()
            try:
                resolved = registry.resolve_names(names)
            except KeyError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _print_json(
                {
                    "names": names,
                    "resolved": resolved,
                    "available": [name for name in resolved if registry.available(name)],
                }
            )
        else:
            _print_json(
                {"toolsets": [registry.describe_toolset(name) for name in registry.toolset_names()]}
            )
        return 0

    if args.command in {"search", "lineage", "lessons"}:
        index = MissionIndex(args.root)
        index.rebuild(store)  # derived index; refresh from JSON before querying
        if args.command == "search":
            if args.missions:
                _print_json(
                    {"query": args.query, "missions": index.search_missions(args.query, limit=args.limit)}
                )
            else:
                hits = index.search_ledger(args.query, limit=args.limit)
                _print_json({"query": args.query, "hits": to_dict(hits)})
        elif args.command == "lessons":
            if args.lessons_command == "search":
                lessons = index.search_lessons(args.query, limit=args.limit)
                _print_json({"query": args.query, "lessons": to_dict(lessons), "count": len(lessons)})
            else:
                lessons = index.list_lessons(limit=args.limit)
                _print_json({"lessons": to_dict(lessons), "count": len(lessons)})
        else:
            _print_json(
                {"candidate_id": args.candidate_id, "ancestors": index.lineage(args.candidate_id)}
            )
        return 0

    if args.command == "tick":
        report = MissionScheduler(store=store).tick()
        _print_json(to_dict(report))
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _chat(args: argparse.Namespace, service: MainLoopService) -> int:
    providers = ProviderStore(args.root)
    if args.session_id:
        session = service.sessions.load(args.session_id)
        provider_id = args.provider_id or session.provider_id
        if args.provider_id and session.provider_id and args.provider_id != session.provider_id:
            raise ValueError("A resumed session must use its connected provider profile.")
        session_id = session.id
    else:
        profiles = providers.list()
        provider_id = args.provider_id or (profiles[0].id if len(profiles) == 1 else None)
        if not provider_id:
            raise ValueError("Connect a provider first or pass --provider-id.")
        opened = service.open(
            interface="cli",
            provider_id=provider_id,
            mission_seed=args.mission,
        )
        session_id = opened["session"]["id"]
    if not provider_id:
        raise ValueError("The session has no provider; connect one and start a new CLI session.")
    client = OpenAICompatibleClient(providers.load(provider_id))
    agent = MainLoopAgent(
        args.root,
        client,
        max_tool_iterations=args.max_tool_iterations,
    )
    if args.message is not None:
        result = agent.turn(session_id, args.message)
        _print_json(to_dict(result))
        return 0

    print(f"main-loop session: {session_id} (provider: {provider_id})")
    print("Type /exit to leave; the session remains resumable.")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if message in {"/exit", "/quit"}:
            return 0
        if not message:
            continue
        try:
            result = agent.turn(session_id, message)
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue
        print(f"agent> {result.content}")
