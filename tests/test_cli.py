import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionStore
from multi_loop.cli import main


class CliTests(unittest.TestCase):
    def test_provider_connect_and_list_store_only_env_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            connected_stdout = io.StringIO()
            with contextlib.redirect_stdout(connected_stdout):
                self.assertEqual(
                    main(
                        [
                            "--root",
                            root,
                            "provider",
                            "connect",
                            "local",
                            "--kind",
                            "openai_compatible",
                            "--model",
                            "test-model",
                            "--api-key-env",
                            "TEST_LLM_KEY",
                        ]
                    ),
                    0,
                )
            listed_stdout = io.StringIO()
            with contextlib.redirect_stdout(listed_stdout):
                self.assertEqual(main(["--root", root, "provider", "list"]), 0)
            connected = json.loads(connected_stdout.getvalue())
            listed = json.loads(listed_stdout.getvalue())

        self.assertEqual(connected["provider"]["api_key_env"], "TEST_LLM_KEY")
        self.assertEqual(listed["providers"][0]["id"], "local")

    def test_agent_commands_create_update_and_confirm_mcp_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            opened_stdout = io.StringIO()
            with contextlib.redirect_stdout(opened_stdout):
                self.assertEqual(
                    main(["--root", root, "agent", "open", "--mission", "Build a product"]),
                    0,
                )
            opened = json.loads(opened_stdout.getvalue())
            session_id = opened["session"]["id"]

            updated_stdout = io.StringIO()
            with contextlib.redirect_stdout(updated_stdout):
                self.assertEqual(
                    main(
                        [
                            "--root",
                            root,
                            "agent",
                            "draft",
                            session_id,
                            json.dumps({"success_criteria": "Ship a tested MVP"}),
                        ]
                    ),
                    0,
                )

            confirmed_stdout = io.StringIO()
            with contextlib.redirect_stdout(confirmed_stdout):
                self.assertEqual(
                    main(["--root", root, "agent", "confirm", session_id]),
                    0,
                )
            confirmed = json.loads(confirmed_stdout.getvalue())

        self.assertTrue(confirmed["created"])
        self.assertEqual(confirmed["mission"]["onboarding_session_id"], session_id)

    def test_create_run_status_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            create_stdout = io.StringIO()
            with contextlib.redirect_stdout(create_stdout):
                self.assertEqual(
                    main([
                        "--root",
                        root,
                        "create",
                        "Build a small useful product",
                        "--success-criteria",
                        "Produce one generation of artifacts.",
                    ]),
                    0,
                )
            mission_id = json.loads(create_stdout.getvalue())["mission_id"]

            run_stdout = io.StringIO()
            with contextlib.redirect_stdout(run_stdout):
                self.assertEqual(main(["--root", root, "run", mission_id]), 0)
            run_result = json.loads(run_stdout.getvalue())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                self.assertEqual(main(["--root", root, "status", mission_id]), 0)
            status = json.loads(status_stdout.getvalue())

        self.assertEqual(run_result["generation_index"], 0)
        self.assertEqual(status["mission"]["id"], mission_id)
        self.assertEqual(len(status["mission"]["generations"]), 1)
        self.assertGreater(status["ledger_count"], 0)

    def test_run_without_runner_preserves_mission_preferred_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            store = MissionStore(root)
            mission = MissionOrchestrator(store=store).create_mission(
                "Build a small useful product",
                "Produce one generation of artifacts.",
                clarifications={"preferred_tools": "agent_command"},
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["--root", str(root), "run", mission.id]), 0)
            loaded = store.load_mission(mission.id)

        self.assertEqual(
            {candidate.runner for candidate in loaded.generations[0].candidate_loops},
            {"agent_command"},
        )


if __name__ == "__main__":
    unittest.main()
