import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from multi_loop.mcp_server import (
    build_server,
    candidate_artifact_write_impl,
    candidate_claim_impl,
    candidate_submit_result_impl,
    capability_describe_impl,
    capability_add_command_impl,
    capability_list_impl,
    capability_search_impl,
    create_mission_impl,
    doctor_impl,
    generation_finalize_impl,
    generation_prepare_impl,
    list_backends_impl,
    list_missions_impl,
    mission_status_impl,
    onboard_impl,
    run_generation_impl,
    run_list_impl,
    run_result_impl,
    lineage_impl,
    main_loop_checkpoint_impl,
    main_loop_context_impl,
    main_loop_open_impl,
    main_loop_record_turn_impl,
    mission_confirm_impl,
    mission_draft_update_impl,
    mission_draft_validate_impl,
    run_status_impl,
    run_tail_impl,
    search_impl,
    toolset_list_impl,
    toolset_resolve_impl,
)


class McpServerTests(unittest.TestCase):
    def test_mcp_host_executes_generation_without_nested_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            created = create_mission_impl("Host-driven mission", "Return verified work", root=root)
            prepared = generation_prepare_impl(created["mission_id"], root=root)
            generation = prepared["generation"]

            for index, candidate in enumerate(generation["candidate_loops"]):
                claim = candidate_claim_impl(
                    created["mission_id"], generation["index"], candidate["id"], root=root
                )
                self.assertFalse(claim["claim"]["blocked"])
                artifact = candidate_artifact_write_impl(
                    created["mission_id"],
                    generation["index"],
                    candidate["id"],
                    f"evidence-{index}.md",
                    f"Evidence from Codex candidate {index}",
                    root=root,
                    kind="markdown",
                )
                submitted = candidate_submit_result_impl(
                    created["mission_id"],
                    generation["index"],
                    candidate["id"],
                    True,
                    f"Codex completed candidate {index}",
                    root=root,
                    submission_id=f"codex-{index}",
                    claim_token=claim["claim"]["claim_token"],
                    artifacts=[artifact["artifact"]],
                )
                self.assertEqual(submitted["candidate"]["state"], "completed")

            finalized = generation_finalize_impl(
                created["mission_id"], generation["index"], root=root
            )

        self.assertNotIn("error", finalized)
        self.assertEqual(finalized["mission"]["generations"][0]["state"], "completed")
        json.dumps(finalized)

    def test_mcp_host_can_drive_durable_main_loop_onboarding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            opened = main_loop_open_impl(root=root, mission_seed="Launch a useful service")
            session_id = opened["session"]["id"]
            revision = opened["session"]["revision"]
            updated = mission_draft_update_impl(
                session_id,
                {
                    "success_criteria": "Acquire five validated design partners.",
                    "requested_capabilities": ["agent_loop", "shell_command"],
                },
                root=root,
                expected_revision=revision,
            )
            checkpointed = main_loop_checkpoint_impl(
                session_id,
                root=root,
                summary="Service launch scoped around design partners.",
                decisions=["Validate before building"],
                open_questions=[],
            )
            recorded = main_loop_record_turn_impl(
                session_id,
                "Proceed with this scope",
                "I will show the draft before creating it.",
                root=root,
            )
            validation = mission_draft_validate_impl(session_id, root=root)
            confirmed = mission_confirm_impl(session_id, root=root)
            resumed = main_loop_context_impl(session_id, root=root)

        self.assertNotIn("error", updated)
        self.assertNotIn("error", checkpointed)
        self.assertNotIn("error", recorded)
        self.assertTrue(validation["valid"])
        self.assertTrue(confirmed["created"])
        self.assertEqual(resumed["session"]["active_mission_id"], confirmed["mission"]["id"])
        json.dumps(resumed)

    def test_create_run_and_status_impls_are_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            created = create_mission_impl(
                "Build a useful workflow",
                "Produce one generation of artifacts.",
                root=root,
            )

            run = run_generation_impl(created["mission_id"], root=root, detach=False)
            status = mission_status_impl(created["mission_id"], root=root)
            listing = list_missions_impl(root=root)

        self.assertEqual(run["generation_index"], 0)
        self.assertEqual(status["mission"]["id"], created["mission_id"])
        self.assertEqual(status["ledger_count"], 5)
        self.assertEqual(len(listing["missions"]), 1)
        json.dumps({"created": created, "run": run, "status": status, "listing": listing})

    def test_detached_generation_run_can_be_monitored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            created = create_mission_impl(
                "Build a useful workflow",
                "Produce one generation of artifacts.",
                root=root,
            )

            started = run_generation_impl(created["mission_id"], root=root)
            self.assertEqual(started["status"], "running")
            run_id = started["run_id"]
            result = run_result_impl(run_id, wait=True, timeout=10)
            status = run_status_impl(run_id)
            tail = run_tail_impl(run_id, cursor=0)
            listing = run_list_impl()
            events_path_exists = os.path.exists(started["events_path"])

        self.assertNotIn("error", result)
        self.assertEqual(result["generation_index"], 0)
        self.assertFalse(status["running"])
        self.assertTrue(events_path_exists)
        kinds = [event["kind"] for event in tail["events"]]
        self.assertIn("run_started", kinds)
        self.assertIn("generation_started", kinds)
        self.assertIn("generation_finished", kinds)
        self.assertIn("run_finished", kinds)
        self.assertTrue(any(item["run_id"] == run_id for item in listing["runs"]))

    def test_run_generation_impl_threads_runner_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            created = create_mission_impl("Real MCP run", "Produce output", root=root)
            run = run_generation_impl(
                created["mission_id"],
                root=root,
                runner="shell",
                runner_command="echo MCP_REAL_RUN",
                detach=False,
            )
            status = mission_status_impl(created["mission_id"], root=root)

        candidates = status["mission"]["generations"][0]["candidate_loops"]
        self.assertEqual(run["generation_index"], 0)
        self.assertTrue(all(c["runner"] == "shell" for c in candidates))
        self.assertTrue(all(c["state"] == "completed" for c in candidates))

    def test_onboard_impl_can_plan_without_creating(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            out = onboard_impl("Run a company", root=root, create=False)

        self.assertFalse(out["created"])
        self.assertEqual(out["onboarding_plan"]["mission_statement"], "Run a company")
        self.assertIn("capability_brief", out)

    def test_doctor_and_backends_report_operational_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            doctor = doctor_impl(root=root, cwd=tmpdir)
            backends = list_backends_impl()

        self.assertTrue(doctor["ok"])
        self.assertTrue(doctor["cwd"]["exists"])
        self.assertIn("mock", backends["runners"])
        self.assertIn("capabilities", backends)

    def test_unknown_ids_return_error_payloads(self):
        self.assertIn("error", mission_status_impl("missing"))
        self.assertIn("error", run_status_impl("missing"))
        self.assertIn("error", run_tail_impl("missing"))
        self.assertIn("error", run_result_impl("missing"))

    def test_capability_discovery_impls(self):
        listing = capability_list_impl()
        available = capability_list_impl(available_only=True)
        search = capability_search_impl("shell command local")
        described = capability_describe_impl("shell_command")
        unknown = capability_describe_impl("does_not_exist")
        github = capability_search_impl(
            "GitHub pull request review comments", include_unavailable=True
        )

        self.assertEqual(listing["count"], len(listing["capabilities"]))
        self.assertLessEqual(available["count"], listing["count"])
        self.assertTrue(all(card["available"] for card in available["capabilities"]))
        self.assertIn("shell_command", [card["name"] for card in search["results"]])
        self.assertTrue(described["available"])
        self.assertIn("error", unknown)
        self.assertIn("github_pr_comment", [card["name"] for card in github["results"]])
        json.dumps({"listing": listing, "search": search, "described": described})

    def test_mcp_onboarder_can_add_user_approved_command_capability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            opened = main_loop_open_impl(root=root, mission_seed="Use a command tool")
            session_id = opened["session"]["id"]
            mission_draft_update_impl(
                session_id,
                {"success_criteria": "Produce command evidence"},
                root=root,
            )

            configured = capability_add_command_impl(
                session_id,
                "approved_command",
                "A user-approved local command.",
                "true",
                "read_only",
                "Yes, add this command.",
                root=root,
                runner="shell",
            )
            context = main_loop_context_impl(session_id, root=root)

        self.assertNotIn("error", configured)
        self.assertIn(
            "approved_command", context["session"]["draft"]["requested_capabilities"]
        )

    def test_toolset_impls(self):
        listing = toolset_list_impl()
        resolved = toolset_resolve_impl(["company", "agent_loop"])
        unknown = toolset_resolve_impl(["nope"])

        names = [card["name"] for card in listing["toolsets"]]
        self.assertIn("company", names)
        self.assertIn("agent_loop", resolved["resolved"])
        self.assertIn("web_research", resolved["resolved"])
        self.assertIn("agent_loop", resolved["available"])
        self.assertIn("error", unknown)
        json.dumps({"listing": listing, "resolved": resolved})

    def test_search_and_lineage_impls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = str(Path(tmpdir) / ".multi-loop")
            created = create_mission_impl("Build a SaaS product", "Ship an MVP", root=root)
            mission_id = created["mission_id"]
            run_generation_impl(mission_id, root=root, detach=False)

            ledger_search = search_impl("synthesized", root=root)
            mission_search = search_impl("SaaS", root=root, missions=True)
            status = mission_status_impl(mission_id, root=root)

        candidate_id = status["mission"]["generations"][0]["candidate_loops"][0]["id"]
        self.assertTrue(ledger_search["hits"])
        self.assertEqual(mission_search["missions"][0]["id"], mission_id)
        # gen-0 candidates have no parents.
        with tempfile.TemporaryDirectory() as tmpdir:
            empty = lineage_impl(candidate_id, root=str(Path(tmpdir) / ".multi-loop"))
        self.assertEqual(empty["ancestors"], [])
        json.dumps({"ledger": ledger_search, "missions": mission_search})

    def test_build_server_constructs_when_mcp_sdk_is_installed(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp SDK is not installed")
        server = build_server()
        self.assertEqual(server.name, "multi-loop")


if __name__ == "__main__":
    unittest.main()
