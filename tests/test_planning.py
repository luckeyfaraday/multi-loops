import unittest

from multi_loop import HeuristicPortfolioPlanner, Mission, prepare_candidate
from multi_loop.capabilities import default_capabilities
from multi_loop.models import CandidateState, ExecutionProfile, Generation


class PlanningTests(unittest.TestCase):
    def test_generation_zero_adds_mission_specific_loop_for_company_mission(self):
        mission = Mission(
            statement="Start a company",
            success_criteria="Produce a launch plan",
        )
        plan = HeuristicPortfolioPlanner().plan(mission, 0)
        roles = [candidate.role for candidate in plan.candidates]

        self.assertIn("market_research", roles)
        self.assertEqual(len(plan.mutations), 0)

    def test_generation_one_mutates_selected_lineage(self):
        planner = HeuristicPortfolioPlanner()
        mission = Mission(statement="Build a useful tool", success_criteria="Ship a prototype")
        first = planner.plan(mission, 0)
        mission.generations.append(
            Generation(
                index=0,
                candidate_loops=first.candidates,
                selected_lineage=[first.candidates[0].id, first.candidates[1].id],
            )
        )
        for candidate in mission.generations[0].candidate_loops:
            if candidate.id in mission.generations[0].selected_lineage:
                candidate.state = CandidateState.COMPLETED
                candidate.result = "Completed prior output."

        second = planner.plan(mission, 1)
        roles = {candidate.role for candidate in second.candidates}

        self.assertIn("research_refined", roles)
        self.assertIn("strategy_refined", roles)
        self.assertIn("crossover", roles)
        self.assertIn("synthesis_worker", roles)
        self.assertGreaterEqual(len(second.mutations), 3)

    def test_hermes_execution_profile_overrides_candidate_runner(self):
        mission = Mission(
            statement="Grow the project",
            success_criteria="More stars",
            execution_profile=ExecutionProfile(runner="hermes"),
        )

        plan = HeuristicPortfolioPlanner().plan(mission, 0)

        self.assertTrue(plan.candidates)
        for candidate in plan.candidates:
            self.assertEqual(candidate.runner, "hermes")

    def test_unavailable_required_capability_blocks_candidate(self):
        mission = Mission(statement="Start a company", success_criteria="Launch")
        candidate = HeuristicPortfolioPlanner().plan(mission, 0).candidates[-1]
        capabilities = default_capabilities()

        reason = prepare_candidate(candidate, mission, capabilities)

        self.assertIsNotNone(reason)
        self.assertIn("web_research", reason)


if __name__ == "__main__":
    unittest.main()