import tempfile
import unittest
from pathlib import Path

from multi_loop import ExecutionProfile, MissionOrchestrator, MissionStore


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
                    summary=f"host candidate result number {index}",
                    submission_id=f"submission-{index}",
                    claim_token=claim.claim_token,
                )
                repeated_result = orchestrator.submit_candidate_result(
                    mission.id,
                    generation.index,
                    planned.id,
                    success=index != 1,
                    summary=f"host candidate result number {index}",
                    submission_id=f"submission-{index}",
                    claim_token=claim.claim_token,
                )
                self.assertEqual(recorded.id, repeated_result.id)

            result = orchestrator.finalize_generation(mission.id, generation.index)
            loaded = store.load_mission(mission.id).generations[0]

        self.assertEqual(loaded.state.value, "completed")
        self.assertEqual(len(result.selected_loop_ids), 2)
        self.assertIn("host candidate result", result.synthesis)

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

    def test_host_result_runs_configured_verification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Host executes",
                "Verification decides success",
                execution_profile=ExecutionProfile(verification=["false"]),
            )
            generation = orchestrator.prepare_generation(mission.id)
            candidate = generation.candidate_loops[0]
            claim = orchestrator.claim_candidate(mission.id, generation.index, candidate.id)

            recorded = orchestrator.submit_candidate_result(
                mission.id,
                generation.index,
                candidate.id,
                success=True,
                summary="host reports success",
                claim_token=claim.claim_token,
            )

        self.assertEqual(recorded.state.value, "failed")
        self.assertEqual(len(recorded.fitness.rubric), 5)
        self.assertEqual(recorded.fitness.rubric["verification"], 0.0)
        self.assertIn("Verification failed", recorded.result)

    def test_candidate_artifact_requires_active_claim_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Host executes", "Produce evidence")
            generation = orchestrator.prepare_generation(mission.id)
            candidate = generation.candidate_loops[0]
            claim = orchestrator.claim_candidate(mission.id, generation.index, candidate.id)

            with self.assertRaisesRegex(ValueError, "active claim token"):
                orchestrator.write_candidate_artifact(
                    mission.id,
                    generation.index,
                    candidate.id,
                    claim_token="wrong-token",
                    filename="evidence.md",
                    content="untrusted evidence",
                )
            artifact = orchestrator.write_candidate_artifact(
                mission.id,
                generation.index,
                candidate.id,
                claim_token=claim.claim_token,
                filename="evidence.md",
                content="trusted evidence",
            )
            artifact_exists = (store.mission_dir(mission.id) / artifact.path).exists()

        self.assertTrue(artifact_exists)

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


class ConfigureMissionTests(unittest.TestCase):
    def test_operator_configures_every_mutable_field_with_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Operate", "Old criteria", clarifications={"stale": "drop me"}
            )

            orchestrator.configure_mission(
                mission.id,
                {
                    "success_criteria": "New verified criteria",
                    "clarifications": {"time_horizon": "30 days", "stale": ""},
                    "budget": {"max_tokens": 500, "max_seconds": 60},
                    "schedule": "every 1d",
                    "execution_profile": {
                        "runner": "shell",
                        "runner_command": "true",
                        "verification": ["true", "  "],
                        "autonomy_level": "scheduled",
                    },
                    "selected_capabilities": ["shell_command", "shell_command"],
                },
                changed_by="operator",
            )
            loaded = store.load_mission(mission.id)
            events = store.read_events(mission.id)
            ledger = store.read_ledger(mission.id)

        self.assertEqual(loaded.success_criteria, "New verified criteria")
        self.assertEqual(loaded.clarifications, {"time_horizon": "30 days"})
        self.assertEqual(loaded.budget.max_tokens, 500)
        self.assertEqual(loaded.budget.max_seconds, 60)
        self.assertEqual(loaded.schedule.expression, "every 1d")
        self.assertIsNotNone(loaded.schedule.next_run_at)
        self.assertEqual(loaded.execution_profile.runner, "shell")
        self.assertEqual(loaded.execution_profile.runner_command, "true")
        self.assertEqual(loaded.execution_profile.verification, ["true"])
        self.assertEqual(loaded.execution_profile.autonomy_level, "scheduled")
        self.assertEqual(loaded.selected_capabilities, ["shell_command"])
        configured_events = [e for e in events if e.event_type == "mission_configured"]
        self.assertEqual(len(configured_events), 1)
        self.assertEqual(configured_events[0].data["changed_by"], "operator")
        self.assertIn("budget", configured_events[0].data["changes"])
        configured_entries = [e for e in ledger if e.event_type == "mission_configured"]
        self.assertEqual(len(configured_entries), 1)
        self.assertIn(configured_entries[0].id, loaded.ledger)

    def test_configure_clears_schedule_with_null(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Operate", "Criteria", schedule="every 1h")

            orchestrator.configure_mission(mission.id, {"schedule": None}, changed_by="operator")

            self.assertIsNone(store.load_mission(mission.id).schedule)

    def test_configure_rejects_protected_and_invalid_fields_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("Operate", "Original criteria")

            with self.assertRaisesRegex(ValueError, "protected"):
                orchestrator.configure_mission(
                    mission.id, {"statement": "hijacked"}, changed_by="operator"
                )
            with self.assertRaisesRegex(ValueError, "protected"):
                orchestrator.configure_mission(
                    mission.id, {"approvals": {"paid_ads": "operator"}}, changed_by="operator"
                )
            with self.assertRaisesRegex(ValueError, "empty"):
                orchestrator.configure_mission(mission.id, {}, changed_by="operator")
            with self.assertRaisesRegex(ValueError, "changed_by"):
                orchestrator.configure_mission(
                    mission.id, {"budget": {"max_tokens": 10}}, changed_by="  "
                )
            with self.assertRaisesRegex(ValueError, "must be positive"):
                orchestrator.configure_mission(
                    mission.id, {"budget": {"max_iterations": 0}}, changed_by="operator"
                )
            with self.assertRaisesRegex(ValueError, "max_cost_usd"):
                orchestrator.configure_mission(
                    mission.id, {"budget": {"max_cost_usd": 5}}, changed_by="operator"
                )
            with self.assertRaisesRegex(ValueError, "Unknown runner"):
                orchestrator.configure_mission(
                    mission.id,
                    {
                        "success_criteria": "should not persist",
                        "execution_profile": {"runner": "warp_drive"},
                    },
                    changed_by="operator",
                )
            with self.assertRaisesRegex(ValueError, "Unknown capabilities"):
                orchestrator.configure_mission(
                    mission.id,
                    {"selected_capabilities": ["ghost_capability"]},
                    changed_by="operator",
                )
            loaded = store.load_mission(mission.id)

        # A rejected patch must leave the stored mission untouched, even when
        # an earlier field in the same patch was individually valid.
        self.assertEqual(loaded.success_criteria, "Original criteria")
        self.assertIsNone(loaded.budget.max_iterations)
        self.assertEqual(loaded.execution_profile.runner, "mock")
        self.assertEqual(
            [e for e in store.read_events(mission.id) if e.event_type == "mission_configured"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
