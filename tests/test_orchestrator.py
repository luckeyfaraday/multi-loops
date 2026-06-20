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


if __name__ == "__main__":
    unittest.main()
