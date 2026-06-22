import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionStore


class MissionOrchestratorTests(unittest.TestCase):
    def test_host_agent_generation_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Host executes this", "Produce evidence")

            generation = orchestrator.prepare_generation(mission.id)
            repeated = orchestrator.prepare_generation(mission.id)
            self.assertEqual(repeated.index, generation.index)
            self.assertEqual(len(store.load_mission(mission.id).generations), 1)

            for index, planned in enumerate(generation.candidate_loops):
                claim = orchestrator.claim_candidate(mission.id, generation.index, planned.id)
                self.assertFalse(claim.blocked)
                recorded = orchestrator.submit_candidate_result(
                    mission.id,
                    generation.index,
                    planned.id,
                    success=index != 1,
                    summary=f"host result {index}",
                    submission_id=f"submission-{index}",
                    claim_token=claim.claim_token,
                )
                repeated_result = orchestrator.submit_candidate_result(
                    mission.id,
                    generation.index,
                    planned.id,
                    success=index != 1,
                    summary=f"host result {index}",
                    submission_id=f"submission-{index}",
                    claim_token=claim.claim_token,
                )
                self.assertEqual(recorded.id, repeated_result.id)

            result = orchestrator.finalize_generation(mission.id, generation.index)
            loaded = store.load_mission(mission.id).generations[0]

        self.assertEqual(loaded.state.value, "completed")
        self.assertEqual(len(result.selected_loop_ids), 2)
        self.assertIn("host result", result.synthesis)

    def test_host_generation_cannot_finalize_unclaimed_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Host executes", "Done")
            generation = orchestrator.prepare_generation(mission.id)

            with self.assertRaisesRegex(ValueError, "unfinished candidates"):
                orchestrator.finalize_generation(mission.id, generation.index)

    def test_candidate_claim_token_prevents_duplicate_host_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Host executes", "Done")
            generation = orchestrator.prepare_generation(mission.id)
            candidate = generation.candidate_loops[0]
            claim = orchestrator.claim_candidate(
                mission.id, generation.index, candidate.id, claimant_id="codex-a"
            )

            with self.assertRaisesRegex(ValueError, "already claimed"):
                orchestrator.claim_candidate(
                    mission.id, generation.index, candidate.id, claimant_id="codex-b"
                )
            resumed = orchestrator.claim_candidate(
                mission.id,
                generation.index,
                candidate.id,
                claimant_id="codex-a",
                claim_token=claim.claim_token,
            )

        self.assertEqual(resumed.claim_token, claim.claim_token)

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

    def test_global_side_effect_flag_cannot_bypass_scoped_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Manage github PRs", "Clear the backlog")

            orchestrator.run_generation(mission.id, runner_command="cat", allow_side_effects=True)
            loaded = store.load_mission(mission.id)
            art = loaded.generations[0].candidate_loops[0].artifacts[0].path
            prompt = (store.mission_dir(mission.id) / art).read_text()

        self.assertIn("SIDE EFFECTS: NONE PERMITTED", prompt)


if __name__ == "__main__":
    unittest.main()
