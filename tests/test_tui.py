import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from multi_loop.models import (
    CandidateLoop,
    CandidateState,
    Generation,
    Mission,
    MissionSchedule,
    PolicyGate,
    SideEffectClass,
)
from multi_loop.storage import MissionStore
from multi_loop.tui.engine import CodexOperatorEngine
from multi_loop.tui.snapshot import build_snapshot

_FAKE_CODEX = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_CODEX_ARGS"
echo '{"type":"thread.started","thread_id":"thread_123"}'
echo '{"type":"item.completed","item":{"type":"agent_message","text":"All missions healthy."}}'
echo '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}'
"""


def _write_script(directory: Path, content: str) -> str:
    path = directory / "fake-codex"
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


class OperatorEngineTests(unittest.TestCase):
    def test_turn_parses_message_and_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), _FAKE_CODEX)
            args_file = Path(tmpdir) / "args.txt"
            engine = CodexOperatorEngine(Path(tmpdir), executable=fake, timeout_seconds=30)

            with unittest.mock.patch.dict(os.environ, {"FAKE_CODEX_ARGS": str(args_file)}):
                reply = engine.turn("how are my missions?", snapshot="Missions: none")

            self.assertTrue(reply.ok)
            self.assertEqual(reply.text, "All missions healthy.")
            self.assertEqual(engine.thread_id, "thread_123")
            recorded = args_file.read_text(encoding="utf-8")
            # First turn: preamble + snapshot lead the prompt, no resume.
            self.assertNotIn("resume", recorded.splitlines())
            self.assertIn("state snapshot", recorded)
            self.assertIn("how are my missions?", recorded)

    def test_second_turn_resumes_the_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), _FAKE_CODEX)
            args_file = Path(tmpdir) / "args.txt"
            engine = CodexOperatorEngine(Path(tmpdir), executable=fake, timeout_seconds=30)

            with unittest.mock.patch.dict(os.environ, {"FAKE_CODEX_ARGS": str(args_file)}):
                engine.turn("first")
                engine.turn("second")

            lines = args_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("resume", lines)
            self.assertIn("thread_123", lines)

    def test_missing_executable_is_error_not_crash(self):
        engine = CodexOperatorEngine(Path("."), executable="definitely-missing-codex")
        reply = engine.turn("hello")
        self.assertFalse(reply.ok)
        self.assertIn("not found", reply.error)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_surfaces_state_and_pending_approvals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            blocked = CandidateLoop(
                goal="Run ads",
                success_criteria="Live",
                state=CandidateState.DISCARDED,
                policy_gates=[
                    PolicyGate(
                        capability="paid_ads",
                        side_effect_class=SideEffectClass.SPEND_MONEY,
                        requires_approval=True,
                    )
                ],
            )
            mission = Mission(
                statement="Grow stars",
                success_criteria="1000 stars",
                schedule=MissionSchedule(expression="every 30m", display="every 30m"),
                generations=[Generation(index=0, candidate_loops=[blocked])],
            )
            store.create_mission(mission)

            snapshot = build_snapshot(store, selected_mission_id=mission.id)

            self.assertIn(mission.id, snapshot)
            self.assertIn("every 30m", snapshot)
            self.assertIn("awaiting user approval: paid_ads", snapshot)
            self.assertIn("granted authority: none", snapshot)

    def test_snapshot_with_no_missions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(build_snapshot(MissionStore(tmpdir)), "No missions exist yet.")


try:  # the console app needs the optional textual extra
    import textual  # noqa: F401

    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


@unittest.skipUnless(HAS_TEXTUAL, "textual not installed")
class ConsoleAppTests(unittest.TestCase):
    def test_app_instantiates(self):
        from multi_loop.tui.app import MultiLoopApp

        with tempfile.TemporaryDirectory() as tmpdir:
            app = MultiLoopApp(tmpdir)
            self.assertIsNotNone(app.store)


if __name__ == "__main__":
    unittest.main()
