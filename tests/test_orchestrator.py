import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionStore


class MissionOrchestratorTests(unittest.TestCase):
    def test_run_generation_persists_results_events_and_synthesis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Build a practical internal newsletter workflow.",
                "Produce a launch strategy with risks and next actions.",
            )

            result = orchestrator.run_generation(mission.id)
            loaded = store.load_mission(mission.id)
            ledger = store.read_ledger(mission.id)
            events = store.read_events(mission.id)

            synthesis_path = store.mission_dir(mission.id) / "artifacts/generation-0/synthesis.md"
            synthesis_exists = synthesis_path.exists()

        self.assertEqual(result.generation_index, 0)
        self.assertEqual(len(result.result_paths), 3)
        self.assertEqual(len(loaded.generations), 1)
        self.assertEqual(len(loaded.generations[0].candidate_loops), 3)
        self.assertGreaterEqual(len(ledger), 5)
        self.assertGreaterEqual(len(events), 8)
        self.assertTrue(synthesis_exists)
        self.assertGreaterEqual(len(loaded.generations[0].selected_lineage), 1)

    def test_failed_candidates_are_not_selected_as_lineage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Build a practical internal newsletter workflow.",
                "Produce a launch strategy with risks and next actions.",
            )

            result = orchestrator.run_generation(mission.id, runner_name="shell")
            loaded = store.load_mission(mission.id)
            generation = loaded.generations[0]

        self.assertEqual(result.selected_loop_ids, [])
        self.assertEqual(generation.selected_lineage, [])
        self.assertTrue(all(candidate.state.value == "failed" for candidate in generation.candidate_loops))

    def test_runner_command_executes_for_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Demo a real run", "Produce output")

            orchestrator.run_generation(
                mission.id, runner_name="shell", runner_command="echo MULTILOOP_OK"
            )
            loaded = store.load_mission(mission.id)
            generation = loaded.generations[0]
            # The shell command's real output is captured in the candidate artifact.
            first = generation.candidate_loops[0]
            artifact_text = (store.mission_dir(mission.id) / first.artifacts[0].path).read_text()

        self.assertTrue(all(c.runner == "shell" for c in generation.candidate_loops))
        self.assertTrue(all(c.state.value == "completed" for c in generation.candidate_loops))
        self.assertIn("MULTILOOP_OK", artifact_text)

    def test_runner_command_defaults_to_agent_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Demo agent default", "Produce output")

            # `cat` echoes the candidate prompt piped to stdin and exits 0.
            orchestrator.run_generation(mission.id, runner_command="cat")
            loaded = store.load_mission(mission.id)
            generation = loaded.generations[0]

        self.assertTrue(all(c.runner == "agent_command" for c in generation.candidate_loops))
        self.assertTrue(all(c.state.value == "completed" for c in generation.candidate_loops))

    def test_verification_rescues_a_failed_runner(self):
        # The runner exits non-zero (e.g. timed out after doing the work), but
        # verification confirms the result — success should reflect the evidence.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("verify rescue", "ok")

            orchestrator.run_generation(
                mission.id, runner_name="shell", runner_command="false", verification=["true"]
            )
            generation = store.load_mission(mission.id).generations[0]

        self.assertTrue(all(c.state.value == "completed" for c in generation.candidate_loops))
        self.assertGreaterEqual(len(generation.selected_lineage), 1)
        self.assertEqual(generation.candidate_loops[0].fitness.rubric["verification"], 0.15)

    def test_verification_revokes_an_unverified_success(self):
        # The runner exits 0 but cannot prove its claimed work — fail it.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("verify fail", "ok")

            orchestrator.run_generation(
                mission.id, runner_name="shell", runner_command="true", verification=["false"]
            )
            generation = store.load_mission(mission.id).generations[0]

        self.assertTrue(all(c.state.value == "failed" for c in generation.candidate_loops))
        self.assertEqual(generation.selected_lineage, [])

    def test_spawned_agent_is_denied_side_effects_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Manage github PRs", "Clear the backlog")

            # `cat` echoes the prompt the agent would receive, so the artifact
            # captures exactly what the spawned agent was instructed.
            orchestrator.run_generation(mission.id, runner_command="cat")
            loaded = store.load_mission(mission.id)
            art = loaded.generations[0].candidate_loops[0].artifacts[0].path
            prompt = (store.mission_dir(mission.id) / art).read_text()

        self.assertIn("SIDE EFFECTS: NONE PERMITTED", prompt)
        self.assertIn("Do NOT merge", prompt)

    def test_allow_side_effects_lifts_the_directive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Manage github PRs", "Clear the backlog")

            orchestrator.run_generation(mission.id, runner_command="cat", allow_side_effects=True)
            loaded = store.load_mission(mission.id)
            art = loaded.generations[0].candidate_loops[0].artifacts[0].path
            prompt = (store.mission_dir(mission.id) / art).read_text()

        self.assertIn("SIDE EFFECTS: APPROVED", prompt)
        self.assertNotIn("NONE PERMITTED", prompt)


if __name__ == "__main__":
    unittest.main()
