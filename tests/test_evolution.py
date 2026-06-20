import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionStore
from multi_loop.capabilities import default_capabilities


class EvolutionTests(unittest.TestCase):
    def test_second_generation_differs_from_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Build a useful internal tool",
                "Produce an implementation plan with risks and next actions.",
            )

            orchestrator.run_generation(mission.id)
            orchestrator.run_generation(mission.id)
            loaded = store.load_mission(mission.id)

        gen0_goals = [candidate.goal for candidate in loaded.generations[0].candidate_loops]
        gen1_goals = [candidate.goal for candidate in loaded.generations[1].candidate_loops]
        gen1_mutations = loaded.generations[1].mutations

        self.assertNotEqual(gen0_goals, gen1_goals)
        self.assertGreater(len(gen1_mutations), 0)
        self.assertTrue(any(candidate.parent_ids for candidate in loaded.generations[1].candidate_loops))

    def test_approval_unblocks_side_effecting_capability(self):
        capabilities = default_capabilities()
        capabilities.register(capabilities.require("paid_ads"), check=lambda: True, override=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store, capabilities=capabilities)
            mission = orchestrator.create_mission(
                "Run ad campaign experiments",
                "Produce campaign variants",
            )
            orchestrator.approve_capability(mission.id, "paid_ads", approved_by="user")
            result = orchestrator.run_generation(mission.id)
            loaded = store.load_mission(mission.id)

        campaign = next(
            candidate
            for candidate in loaded.generations[0].candidate_loops
            if candidate.role == "campaign_experiment"
        )

        self.assertEqual(campaign.state.value, "completed")
        self.assertNotIn(campaign.id, result.blocked_candidates)


if __name__ == "__main__":
    unittest.main()