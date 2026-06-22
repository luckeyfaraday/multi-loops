import json
import tempfile
import unittest
from pathlib import Path

from multi_loop.agent_sessions import (
    AgentInterface,
    AgentPhase,
    MainLoopSession,
    MainLoopSessionStore,
    SessionConflict,
)
from multi_loop.main_agent import MainLoopService
from multi_loop.capabilities import default_capabilities
from multi_loop.orchestrator import MissionOrchestrator


class MainLoopSessionStoreTests(unittest.TestCase):
    def test_append_only_session_round_trip_and_revision_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MainLoopSessionStore(Path(tmpdir) / ".multi-loop")
            session = MainLoopSession(interface=AgentInterface.MCP)
            store.create(session)

            updated = store.append_message(session.id, "user", "Help me scope a mission")
            entries = store.read_entries(session.id)

            self.assertEqual(updated.revision, 1)
            self.assertEqual(entries[0].data["role"], "user")
            with self.assertRaises(SessionConflict):
                store.append_message(
                    session.id,
                    "assistant",
                    "Stale write",
                    expected_revision=0,
                )

    def test_session_snapshot_never_contains_provider_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MainLoopSessionStore(Path(tmpdir) / ".multi-loop")
            session = MainLoopSession(interface=AgentInterface.CLI, provider_id="provider_demo")
            path = store.create(session) / "session.json"
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["provider_id"], "provider_demo")
        self.assertNotIn("api_key", payload)


class MainLoopServiceTests(unittest.TestCase):
    @staticmethod
    def _available_setup_registry():
        registry = default_capabilities()
        for name in ("github_read", "github_pr_comment", "codex_oauth_runner"):
            registry.register(registry.require(name), check=lambda: True, override=True)
        return registry

    def test_onboarder_plans_and_applies_required_capabilities_before_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root, capabilities=self._available_setup_registry())
            session_id = service.open(
                interface="mcp", mission_seed="Review GitHub pull requests"
            )["session"]["id"]
            service.update_draft(
                session_id,
                {
                    "success_criteria": "Review every open PR and post scoped comments.",
                    "schedule": "every 12h",
                    "workspace": str(Path(tmpdir)),
                },
            )

            before = service.validate(session_id)
            plan = service.capability_setup_plan(
                session_id, ["github_read", "github_pr_comment"]
            )
            applied = service.capability_setup_apply(
                session_id,
                ["github_read", "github_pr_comment"],
                confirmation_quote="Yes, add GitHub read access and PR comments.",
            )
            confirmed = service.confirm(session_id)

        self.assertFalse(before["valid"])
        self.assertTrue(plan["can_apply"])
        self.assertIn("github_pr_comment", plan["side_effect_approvals"][0]["capability"])
        self.assertTrue(applied["validation"]["valid"])
        mission = confirmed["mission"]
        self.assertEqual(mission["execution_profile"]["runner"], "agent_command")
        self.assertIn("codex exec", mission["execution_profile"]["runner_command"])
        self.assertEqual(mission["approvals"]["github_pr_comment"], "user")
        self.assertIn("codex_oauth_runner", mission["selected_capabilities"])
        self.assertIn("scheduled_tick", mission["selected_capabilities"])

    def test_user_can_add_approved_command_as_persistent_capability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="mcp", mission_seed="Use a custom tool")[
                "session"
            ]["id"]
            service.update_draft(session_id, {"success_criteria": "Tool returns evidence"})

            result = service.add_command_capability(
                session_id,
                name="custom_tool",
                description="Run a user-approved local command tool.",
                command="true",
                side_effect_class="read_only",
                confirmation_quote="Yes, add this command tool.",
                runner="shell",
            )
            resumed = MainLoopService(root).context(session_id)

        self.assertEqual(result["configured_capability"]["capability"]["name"], "custom_tool")
        self.assertIn("custom_tool", resumed["session"]["draft"]["requested_capabilities"])
        self.assertTrue(
            any(card["name"] == "custom_tool" and card["available"] for card in resumed["capabilities"])
        )

    def test_existing_mission_can_receive_approved_capability_and_runner_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            registry = self._available_setup_registry()
            service = MainLoopService(root, capabilities=registry)
            mission = MissionOrchestrator(
                store=service.missions, capabilities=registry
            ).create_mission("Review PRs", "Post useful comments", schedule="every 12h")

            plan = service.mission_capability_setup_plan(
                mission.id, ["github_read", "github_pr_comment"]
            )
            applied = service.mission_capability_setup_apply(
                mission.id,
                ["github_read", "github_pr_comment"],
                confirmation_quote="Yes, configure this mission.",
            )

        self.assertTrue(plan["can_apply"])
        configured = applied["mission"]
        self.assertEqual(configured["execution_profile"]["runner"], "agent_command")
        self.assertEqual(configured["approvals"]["github_pr_comment"], "user")

    def test_mcp_session_can_scope_confirm_and_resume_same_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MainLoopService(Path(tmpdir) / ".multi-loop")
            opened = service.open(interface="mcp", mission_seed="Build a useful product")
            session_id = opened["session"]["id"]

            scoped = service.update_draft(
                session_id,
                {
                    "success_criteria": "Ship a tested MVP.",
                    "requested_capabilities": ["agent_loop", "shell_command"],
                    "clarifications": {"constraint": "local work first"},
                    "budget": {"max_iterations": 4, "max_tokens": 5000},
                },
            )
            confirmed = service.confirm(session_id)
            resumed = service.open(session_id=session_id)

        self.assertTrue(scoped["validation"]["valid"])
        self.assertTrue(confirmed["created"])
        self.assertEqual(resumed["session"]["phase"], AgentPhase.ACTIVE.value)
        self.assertEqual(
            resumed["session"]["active_mission_id"],
            confirmed["mission"]["id"],
        )
        self.assertEqual(confirmed["mission"]["execution_profile"]["controller"], "mcp_host")
        self.assertEqual(confirmed["mission"]["budget"]["max_iterations"], 4)

    def test_cli_session_requires_connected_provider_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MainLoopService(Path(tmpdir) / ".multi-loop")
            opened = service.open(interface="cli", mission_seed="Build a tool")
            session_id = opened["session"]["id"]
            service.update_draft(session_id, {"success_criteria": "Working prototype"})
            validation = service.validate(session_id)

        self.assertFalse(validation["valid"])
        self.assertIn("provider connection", " ".join(validation["errors"]))

    def test_checkpoint_and_compaction_preserve_canonical_draft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MainLoopService(Path(tmpdir) / ".multi-loop")
            opened = service.open(interface="mcp", mission_seed="Research a market")
            session_id = opened["session"]["id"]
            service.checkpoint(
                session_id,
                summary="The user wants a narrow market study.",
                decisions=["Use public evidence only"],
                open_questions=["Which geography?"],
            )
            compacted = service.compact(session_id, summary="Narrow market study; geography open.")

        self.assertEqual(compacted["session"]["draft"]["statement"], "Research a market")
        self.assertEqual(compacted["session"]["confirmed_decisions"], ["Use public evidence only"])
        self.assertEqual(compacted["recent_entries"], [])


if __name__ == "__main__":
    unittest.main()
