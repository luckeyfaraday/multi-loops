import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multi_loop import (
    CandidateLoop,
    HermesRunner,
    HermesRuntimeAdapter,
    Mission,
    RunRequest,
    default_runner_registry,
)

_FAKE_HERMES = """#!/usr/bin/env bash
prompt=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-q" ]; then prompt="$arg"; fi
  prev="$arg"
done
if [ -n "$FAKE_ARTIFACT_DIR" ]; then
  mkdir -p "$FAKE_ARTIFACT_DIR"
  printf 'evidence' > "$FAKE_ARTIFACT_DIR/report.md"
fi
echo "session_id: sess_fake_123" >&2
if [ -n "$FAKE_ECHO_PROMPT" ]; then
  printf '%s\\n' "$prompt"
else
  echo "FAKE RESPONSE DONE"
fi
exit "${FAKE_EXIT_CODE:-0}"
"""

_SLOW_HERMES = """#!/usr/bin/env bash
sleep 5
"""


def _write_script(directory: Path, name: str, content: str) -> str:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


class HermesRuntimeAdapterTests(unittest.TestCase):
    def test_run_agent_captures_response_and_session_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), "fake-hermes", _FAKE_HERMES)
            adapter = HermesRuntimeAdapter(fake)

            outcome = adapter.run_agent("do the work", timeout_seconds=30)

            self.assertTrue(outcome.success)
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.response, "FAKE RESPONSE DONE")
            self.assertEqual(outcome.session_id, "sess_fake_123")

    def test_permissions_directive_leads_the_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), "fake-hermes", _FAKE_HERMES)
            adapter = HermesRuntimeAdapter(fake)

            with mock.patch.dict(os.environ, {"FAKE_ECHO_PROMPT": "1"}):
                outcome = adapter.run_agent(
                    "do the work",
                    permissions="PERMISSIONS FIRST",
                    timeout_seconds=30,
                )

            self.assertTrue(outcome.response.startswith("PERMISSIONS FIRST"))
            self.assertIn("do the work", outcome.response)

    def test_artifact_directory_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), "fake-hermes", _FAKE_HERMES)
            adapter = HermesRuntimeAdapter(fake)
            artifact_dir = Path(tmpdir) / "artifacts"

            with mock.patch.dict(os.environ, {"FAKE_ARTIFACT_DIR": str(artifact_dir)}):
                outcome = adapter.run_agent(
                    "produce a report", artifact_dir=artifact_dir, timeout_seconds=30
                )

            collected = adapter.collect_artifacts(outcome.run_id)
            self.assertEqual([path.name for path in collected], ["report.md"])
            self.assertEqual(outcome.artifact_dir, str(artifact_dir.resolve()))

    def test_collect_artifacts_returns_empty_for_unknown_run(self):
        adapter = HermesRuntimeAdapter("hermes")
        self.assertEqual(adapter.collect_artifacts("no-such-run"), [])

    def test_nonzero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _write_script(Path(tmpdir), "fake-hermes", _FAKE_HERMES)
            adapter = HermesRuntimeAdapter(fake)

            with mock.patch.dict(os.environ, {"FAKE_EXIT_CODE": "3"}):
                outcome = adapter.run_agent("do the work", timeout_seconds=30)

            self.assertFalse(outcome.success)
            self.assertEqual(outcome.exit_code, 3)

    def test_timeout_is_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            slow = _write_script(Path(tmpdir), "slow-hermes", _SLOW_HERMES)
            adapter = HermesRuntimeAdapter(slow)

            outcome = adapter.run_agent("do the work", timeout_seconds=0.3)

            self.assertTrue(outcome.timed_out)
            self.assertFalse(outcome.success)
            self.assertIsNone(outcome.exit_code)

    def test_missing_executable_is_failure_not_crash(self):
        adapter = HermesRuntimeAdapter("definitely-missing-hermes-xyz-123")

        self.assertFalse(adapter.available())
        outcome = adapter.run_agent("do the work", timeout_seconds=5)

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.exit_code, 127)
        self.assertIn("not found", outcome.stderr)


class HermesRunnerTests(unittest.TestCase):
    def test_registered_in_default_registry(self):
        self.assertIn("hermes", default_runner_registry().names())

    def test_run_collects_evidence_and_transcript(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = Path(tmpdir)
            fake = _write_script(mission_dir, "fake-hermes", _FAKE_HERMES)
            mission = Mission(statement="Test mission", success_criteria="Finish")
            candidate = CandidateLoop(
                goal="Produce a report",
                success_criteria="Report exists",
                runner="hermes",
                runner_config={"executable": fake, "toolsets": ["web", "file"]},
            )
            request = RunRequest(
                mission=mission,
                generation_index=0,
                candidate=candidate,
                mission_dir=mission_dir,
                safety_directive="SIDE EFFECTS: NONE PERMITTED.",
            )
            artifact_dir = mission_dir / f"artifacts/generation-0/{candidate.id}"

            with mock.patch.dict(os.environ, {"FAKE_ARTIFACT_DIR": str(artifact_dir)}):
                result = HermesRunner().run(request)

            self.assertTrue(result.success)
            paths = [artifact.path for artifact in result.artifacts]
            self.assertIn(f"artifacts/generation-0/{candidate.id}/report.md", paths)
            self.assertIn(f"artifacts/generation-0/{candidate.id}-hermes.md", paths)
            for artifact in result.artifacts:
                self.assertTrue((mission_dir / artifact.path).exists())
            self.assertEqual(result.metadata["session_id"], "sess_fake_123")
            self.assertEqual(result.metadata["toolsets"], ["web", "file"])
            self.assertEqual(result.summary, "FAKE RESPONSE DONE")

    def test_run_failure_reports_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = Path(tmpdir)
            fake = _write_script(mission_dir, "fake-hermes", _FAKE_HERMES)
            mission = Mission(statement="Test mission", success_criteria="Finish")
            candidate = CandidateLoop(
                goal="Produce a report",
                success_criteria="Report exists",
                runner="hermes",
                runner_config={"executable": fake},
            )
            request = RunRequest(
                mission=mission,
                generation_index=0,
                candidate=candidate,
                mission_dir=mission_dir,
            )

            with mock.patch.dict(os.environ, {"FAKE_EXIT_CODE": "9"}):
                result = HermesRunner().run(request)

            self.assertFalse(result.success)
            self.assertEqual(result.metadata["exit_code"], 9)


if __name__ == "__main__":
    unittest.main()
