import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from multi_loop import OnboardingEngine, format_capability_brief
from multi_loop.cli import main


class OnboardingTests(unittest.TestCase):
    def test_company_mission_recommends_configured_and_setup_capabilities(self):
        engine = OnboardingEngine()
        answers = engine.default_answers("Run a company with ad campaign experiments")
        answers["mission_statement"] = "Run a company with ad campaign experiments"

        plan = engine.build_plan(answers)
        names = [item.name for item in plan.recommended_capabilities]
        setup_required = [item.name for item in plan.setup_required_capabilities]
        brief = format_capability_brief(plan)

        self.assertIn("agent_loop", names)
        self.assertIn("scheduled_tick", names)
        self.assertIn("web_research", names)
        self.assertIn("paid_ads", names)
        self.assertIn("paid_ads", setup_required)
        self.assertIn("approval required", brief)

    def test_onboard_cli_defaults_creates_mission_with_clarifications(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main([
                        "--root",
                        root,
                        "onboard",
                        "--mission",
                        "Run a company",
                        "--defaults",
                    ]),
                    0,
                )
            payload = json.loads(stdout.getvalue())

        self.assertTrue(payload["created"])
        self.assertEqual(payload["mission"]["mission"]["statement"], "Run a company")
        self.assertIn("approval_policy", payload["mission"]["mission"]["clarifications"])
        self.assertEqual(payload["mission"]["mission"]["clarifications"]["preferred_tools"], "mock")
        self.assertIn("capability_brief", payload)


if __name__ == "__main__":
    unittest.main()
