"""Command-line interface for the MVP multi-loop runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import to_dict
from .onboarding import OnboardingEngine, collect_answers, format_capability_brief
from .orchestrator import MissionOrchestrator
from .scheduler import MissionScheduler
from .storage import MissionStore


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

    run_parser = subparsers.add_parser("run", help="Run one mission generation")
    run_parser.add_argument("mission_id", help="Mission ID")
    run_parser.add_argument(
        "--runner",
        help="Runner to force for planned candidates; defaults to the mission preference",
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

    subparsers.add_parser("tick", help="Run scheduled mission ticks that are due")

    args = parser.parse_args(argv)
    store = MissionStore(args.root)

    try:
        return _dispatch(args, store)
    except FileNotFoundError:
        target = getattr(args, "mission_id", None) or "the requested resource"
        print(f"Mission not found: {target}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, store: MissionStore) -> int:
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

    if args.command == "run":
        workspace = Path(args.workspace).resolve() if args.workspace else None
        orchestrator = MissionOrchestrator(store=store, workspace=workspace)
        result = orchestrator.run_generation(
            args.mission_id,
            runner_name=args.runner,
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

    if args.command == "tick":
        report = MissionScheduler(store=store).tick()
        _print_json(to_dict(report))
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
