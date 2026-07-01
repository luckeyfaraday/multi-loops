import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionStore, SideEffectClass
from multi_loop.models import (
    Artifact,
    CandidateLoop,
    CandidateState,
    FitnessScore,
    Generation,
    GenerationState,
    Mission,
    MissionSchedule,
    PermissionRecord,
    PolicyGate,
)
from multi_loop.reports import render_mission_report, write_generation_report


def _mission_with_generation() -> Mission:
    delivered = CandidateLoop(
        goal="Collect baseline stars",
        success_criteria="Baseline recorded",
        state=CandidateState.COMPLETED,
        result="Baseline is 108 stars.",
        artifacts=[Artifact(path="artifacts/generation-0/report.md")],
    )
    blocked = CandidateLoop(
        goal="Run a paid campaign",
        success_criteria="Campaign live",
        state=CandidateState.DISCARDED,
        policy_gates=[
            PolicyGate(
                capability="paid_ads",
                side_effect_class=SideEffectClass.SPEND_MONEY,
                requires_approval=True,
            )
        ],
    )
    generation = Generation(
        index=0,
        state=GenerationState.COMPLETED,
        candidate_loops=[delivered, blocked],
        fitness_scores=[
            FitnessScore(candidate_loop_id=delivered.id, score=0.9, rationale="did the work")
        ],
        selected_lineage=[delivered.id],
    )
    return Mission(
        statement="Grow GitHub stars",
        success_criteria="Reach 1000 stars",
        approvals={"github_pr_comment": "alan"},
        generations=[generation],
        schedule=MissionSchedule(expression="every 30m", display="every 30m"),
    )


class RenderReportTests(unittest.TestCase):
    def test_report_covers_progress_evidence_authority_and_attention(self):
        mission = _mission_with_generation()
        permissions = [
            PermissionRecord(
                mission_id=mission.id,
                action="used",
                capability="github_pr_comment",
                actor="loop_x",
            )
        ]

        report = render_mission_report(mission, permissions)

        self.assertIn("# Mission Report: Grow GitHub stars", report)
        self.assertIn("1 delivered, 0 failed, 1 blocked by policy", report)
        self.assertIn("**Collect baseline stars** (selected), score 0.90", report)
        self.assertIn("`artifacts/generation-0/report.md`", report)
        self.assertIn("`github_pr_comment` granted by alan; exercised 1 time(s).", report)
        self.assertIn("waiting on your approval of `paid_ads` (spend_money)", report)
        self.assertIn("## Next", report)

    def test_report_without_generations_says_so(self):
        mission = Mission(statement="New mission", success_criteria="Done")

        report = render_mission_report(mission, [])

        self.assertIn("No generations have run yet.", report)
        self.assertIn("No external-action authority has been granted", report)


class GenerationReportTests(unittest.TestCase):
    def test_run_generation_writes_report_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Test", "Done")

            orchestrator.run_generation(mission.id)

            report_path = store.mission_dir(mission.id) / "reports/generation-0.md"
            self.assertTrue(report_path.exists())
            self.assertTrue(
                report_path.read_text(encoding="utf-8").startswith("# Mission Report: Test")
            )
            events = store.read_events(mission.id)
            finished = [e for e in events if e.event_type == "generation_finished"]
            self.assertEqual(finished[-1].data["report_path"], "reports/generation-0.md")

    def test_write_generation_report_returns_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            mission = _mission_with_generation()
            store.create_mission(mission)

            path = write_generation_report(store, mission, 0)

            self.assertEqual(path, "reports/generation-0.md")
            self.assertTrue((Path(store.mission_dir(mission.id)) / path).exists())


if __name__ == "__main__":
    unittest.main()
