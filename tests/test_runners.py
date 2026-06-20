import tempfile
import unittest
from pathlib import Path

from multi_loop import CandidateLoop, Mission, MockRunner, RunRequest, ShellRunner


class RunnerTests(unittest.TestCase):
    def test_mock_runner_writes_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mission = Mission(statement="Test mission", success_criteria="Finish")
            candidate = CandidateLoop(goal="Do useful work", success_criteria="Return output")
            request = RunRequest(
                mission=mission,
                generation_index=0,
                candidate=candidate,
                mission_dir=Path(tmpdir),
            )

            result = MockRunner().run(request)

            self.assertTrue(result.success)
            self.assertEqual(len(result.artifacts), 1)
            self.assertTrue((Path(tmpdir) / result.artifacts[0].path).exists())

    def test_shell_runner_executes_configured_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mission = Mission(statement="Test mission", success_criteria="Finish")
            candidate = CandidateLoop(
                goal="Run command",
                success_criteria="Command exits zero",
                runner="shell",
                runner_config={"command": "printf runner-ok"},
            )
            request = RunRequest(
                mission=mission,
                generation_index=0,
                candidate=candidate,
                mission_dir=Path(tmpdir),
            )

            result = ShellRunner().run(request)

            self.assertTrue(result.success)
            self.assertIn("runner-ok", result.output)


if __name__ == "__main__":
    unittest.main()
