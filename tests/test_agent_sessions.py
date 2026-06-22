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
