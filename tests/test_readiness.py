import tempfile
import unittest
from pathlib import Path

from multi_loop.capabilities import CapabilityRegistry
from multi_loop.main_agent import MainLoopService
from multi_loop.models import Capability, SideEffectClass
from multi_loop.orchestrator import MissionOrchestrator


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="ready_cap",
            description="A locally available capability.",
            toolset_or_backend="local",
        )
    )
    registry.register(
        Capability(
            name="needs_env",
            description="Needs an API key that is not configured.",
            toolset_or_backend="remote",
            requires_env=["MULTI_LOOP_TEST_MISSING_ENV"],
            setup_hint="Export MULTI_LOOP_TEST_MISSING_ENV first.",
        )
    )
    registry.register(
        Capability(
            name="sender",
            description="Messages a person; requires scoped approval.",
            toolset_or_backend="messaging",
            side_effect_class=SideEffectClass.MESSAGE_PERSON,
        )
    )
    return registry


class DraftReadinessTests(unittest.TestCase):
    def test_draft_readiness_classifies_every_gap_kind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root, capabilities=_registry())
            session_id = service.open(interface="mcp", mission_seed="Operate")["session"]["id"]
            service.update_draft(
                session_id,
                {
                    "success_criteria": "Produce evidence",
                    "requested_capabilities": ["ready_cap", "needs_env", "sender", "ghost"],
                },
            )

            report = service.readiness(session_id)
            statuses = {item["name"]: item["status"] for item in report["capabilities"]}

            self.assertFalse(report["ready"])
            self.assertEqual(statuses["ready_cap"], "ready")
            self.assertEqual(statuses["needs_env"], "needs_setup")
            self.assertEqual(statuses["sender"], "needs_approval")
            self.assertEqual(statuses["ghost"], "unknown")
            needs_env = next(
                item for item in report["capabilities"] if item["name"] == "needs_env"
            )
            self.assertEqual(needs_env["missing_env"], ["MULTI_LOOP_TEST_MISSING_ENV"])
            self.assertIn("MULTI_LOOP_TEST_MISSING_ENV", needs_env["fix"])
            self.assertTrue(report["blockers"])
            self.assertTrue(any("sender" in action for action in report["next_actions"]))

    def test_approval_closes_a_draft_readiness_gap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root, capabilities=_registry())
            session_id = service.open(interface="mcp", mission_seed="Operate")["session"]["id"]
            service.update_draft(
                session_id,
                {
                    "success_criteria": "Produce evidence",
                    "requested_capabilities": ["sender"],
                },
            )
            self.assertFalse(service.readiness(session_id)["ready"])

            service.capability_setup_apply(
                session_id, ["sender"], confirmation_quote="Yes, approve sender messaging."
            )

            report = service.readiness(session_id)
            self.assertTrue(report["ready"])
            self.assertEqual(report["capabilities"][0]["status"], "ready")


class MissionReadinessTests(unittest.TestCase):
    def test_mission_readiness_blocks_then_clears_after_operator_fixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            registry = _registry()
            service = MainLoopService(root, capabilities=registry)
            orchestrator = MissionOrchestrator(
                store=service.missions, capabilities=registry
            )
            mission = orchestrator.create_mission(
                "Operate the mission",
                "Produce verified evidence",
                selected_capabilities=["sender"],
                schedule="every 1h",
            )

            before = service.mission_readiness(mission.id)
            self.assertFalse(before["ready"])
            self.assertTrue(any("sender" in blocker for blocker in before["blockers"]))
            self.assertTrue(
                any("unattended runner" in blocker for blocker in before["blockers"])
            )

            orchestrator.approve_capability(mission.id, "sender", approved_by="user")
            orchestrator.configure_mission(
                mission.id,
                {"execution_profile": {"runner": "shell", "runner_command": "true"}},
                changed_by="operator",
            )

            after = service.mission_readiness(mission.id)
            self.assertTrue(after["ready"])
            self.assertEqual(after["blockers"], [])
            self.assertEqual(after["next_actions"], ["run the next generation"])

    def test_paused_schedule_is_a_notice_not_a_blocker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            registry = _registry()
            service = MainLoopService(root, capabilities=registry)
            orchestrator = MissionOrchestrator(store=service.missions, capabilities=registry)
            mission = orchestrator.create_mission(
                "Operate", "Evidence", schedule="every 1h"
            )
            orchestrator.configure_mission(
                mission.id,
                {"execution_profile": {"runner": "shell", "runner_command": "true"}},
                changed_by="operator",
            )
            orchestrator.pause_schedule(mission.id, reason="holding for review")

            report = service.mission_readiness(mission.id)

            self.assertTrue(report["ready"])
            self.assertTrue(any("paused" in notice for notice in report["notices"]))
            self.assertTrue(any("holding for review" in notice for notice in report["notices"]))


if __name__ == "__main__":
    unittest.main()
