import json
import tempfile
import unittest
from pathlib import Path

from multi_loop.agent_loop import MainLoopAgent
from multi_loop.main_agent import MainLoopService
from multi_loop.providers import ProviderReply, ProviderStore, ProviderToolCall


class FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((json.loads(json.dumps(messages)), json.loads(json.dumps(tools))))
        return self.replies.pop(0)


class MainLoopAgentTests(unittest.TestCase):
    def test_native_tool_loop_persists_input_and_updates_draft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            opened = service.open(interface="cli", provider_id="local")
            session_id = opened["session"]["id"]
            client = FakeProvider(
                [
                    ProviderReply(
                        tool_calls=[
                            ProviderToolCall(
                                id="call-1",
                                name="update_mission_draft",
                                arguments={
                                    "patch": {
                                        "statement": "Build a useful service",
                                        "success_criteria": "Validate with five users",
                                    }
                                },
                            )
                        ],
                        prompt_tokens=10,
                        completion_tokens=3,
                    ),
                    ProviderReply(content="The draft is ready for your review.", prompt_tokens=11, completion_tokens=4),
                ]
            )

            result = MainLoopAgent(root, client).turn(session_id, "Help me scope a service")
            context = service.context(session_id)
            entries = service.sessions.read_entries(session_id)

        self.assertEqual(result.content, "The draft is ready for your review.")
        self.assertEqual(result.tool_iterations, 1)
        self.assertEqual(context["session"]["draft"]["statement"], "Build a useful service")
        self.assertEqual(context["session"]["prompt_tokens"], 21)
        self.assertEqual(entries[1].entry_type, "message")
        self.assertEqual(entries[1].data["role"], "user")
        self.assertTrue(any(entry.entry_type == "tool_result" for entry in entries))
        self.assertEqual(client.calls[1][0][-1]["role"], "tool")

    def test_tool_loop_has_hard_iteration_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="cli", provider_id="local")["session"]["id"]
            reply = ProviderReply(
                tool_calls=[ProviderToolCall(id="repeat", name="validate_mission_draft")]
            )
            client = FakeProvider([reply, reply])

            with self.assertRaisesRegex(RuntimeError, "exceeded"):
                MainLoopAgent(root, client, max_tool_iterations=1).turn(session_id, "continue")

            entries = service.sessions.read_entries(session_id)

        self.assertTrue(any(entry.entry_type == "loop_stopped" for entry in entries))

    def test_cost_budget_stops_before_calling_unpriced_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="cli", provider_id="local")["session"]["id"]
            service.update_draft(session_id, {"budget": {"max_cost_usd": 0.01}})
            client = FakeProvider([ProviderReply(content="must not be called")])

            with self.assertRaisesRegex(RuntimeError, "pricing"):
                MainLoopAgent(root, client).turn(session_id, "continue")

        self.assertEqual(client.calls, [])

    def test_model_cannot_confirm_before_post_draft_user_approval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="cli", provider_id="local")["session"]["id"]
            service.update_draft(
                session_id,
                {"statement": "Build it", "success_criteria": "Five users validate it"},
            )
            agent = MainLoopAgent(root, FakeProvider([]))
            call = ProviderToolCall(
                id="confirm-1",
                name="confirm_mission",
                arguments={"confirmation_quote": "yes, create it"},
            )

            with self.assertRaisesRegex(ValueError, "after the latest draft"):
                agent._dispatch_tool(session_id, call)
            service.sessions.append_message(session_id, "user", "yes, create it")
            confirmed = agent._dispatch_tool(session_id, call)

        self.assertTrue(confirmed["created"])

    def test_trivially_short_confirmation_quote_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="cli", provider_id="local")["session"]["id"]
            service.update_draft(
                session_id,
                {"statement": "Build it", "success_criteria": "Five users validate it"},
            )
            service.sessions.append_message(session_id, "user", "yes")
            agent = MainLoopAgent(root, FakeProvider([]))
            call = ProviderToolCall(
                id="confirm-short",
                name="confirm_mission",
                arguments={"confirmation_quote": "yes"},
            )

            with self.assertRaisesRegex(ValueError, "substantive span"):
                agent._dispatch_tool(session_id, call)

    def test_long_session_compacts_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            service = MainLoopService(root)
            session_id = service.open(interface="cli", provider_id="local")["session"]["id"]
            client = FakeProvider(
                [
                    ProviderReply(content="A normal answer."),
                    ProviderReply(content="Durable summary of decisions and open questions."),
                ]
            )

            MainLoopAgent(root, client, compaction_threshold=1).turn(session_id, "A long discussion")
            session = service.sessions.load(session_id)
            entries = service.sessions.read_entries(session_id)

        self.assertEqual(session.working_summary, "Durable summary of decisions and open questions.")
        self.assertTrue(any(entry.entry_type == "compaction" for entry in entries))
        self.assertTrue(
            any(entry.entry_type == "message" and entry.data.get("role") == "user" for entry in entries)
        )


class ProviderStoreTests(unittest.TestCase):
    def test_provider_profile_stores_env_reference_not_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            store = ProviderStore(root)
            profile = store.connect(
                "local",
                kind="openai_compatible",
                model="local-model",
                api_key_env="LOCAL_LLM_KEY",
            )
            raw = (root / "main-loop/providers/local.json").read_text()
            loaded = store.load("local")

        self.assertEqual(profile.api_key_env, "LOCAL_LLM_KEY")
        self.assertEqual(loaded.model, "local-model")
        self.assertNotIn("api_key\"", raw)
        json.loads(raw)

    def test_provider_rejects_credential_in_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProviderStore(Path(tmpdir) / ".multi-loop")
            with self.assertRaisesRegex(ValueError, "metadata headers"):
                store.connect(
                    "bad",
                    kind="openai_compatible",
                    model="model",
                    headers={"Authorization": "Bearer secret"},
                )


if __name__ == "__main__":
    unittest.main()
