import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from multi_loop.mcp_server import (
    build_server,
    capability_describe_impl,
    capability_list_impl,
    capability_search_impl,
    create_mission_impl,
    doctor_impl,
    list_backends_impl,
    list_missions_impl,
    mission_status_impl,
    onboard_impl,
    run_generation_impl,
    run_list_impl,
    run_result_impl,
    run_status_impl,
    run_tail_impl,
)


class McpServerTests(unittest.TestCase):
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

        self.assertEqual(listing["count"], len(listing["capabilities"]))
        self.assertLessEqual(available["count"], listing["count"])
        self.assertTrue(all(card["available"] for card in available["capabilities"]))
        self.assertIn("shell_command", [card["name"] for card in search["results"]])
        self.assertTrue(described["available"])
        self.assertIn("error", unknown)
        json.dumps({"listing": listing, "search": search, "described": described})

    def test_build_server_constructs_when_mcp_sdk_is_installed(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("mcp SDK is not installed")
        server = build_server()
        self.assertEqual(server.name, "multi-loop")


if __name__ == "__main__":
    unittest.main()
