import tempfile
import unittest

from multi_loop import (
    Capability,
    CapabilityRef,
    CapabilityRegistry,
    CandidateLoop,
    MissionOrchestrator,
    MissionStore,
    PermissionRecord,
    SideEffectClass,
)


def _registry_with_publisher() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="publisher",
            description="Publish content to an external service.",
            toolset_or_backend="test",
            side_effect_class=SideEffectClass.PUBLIC_PUBLISH,
        )
    )
    return registry


class PermissionStoreTests(unittest.TestCase):
    def test_permission_records_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            record = PermissionRecord(
                mission_id="mission_test",
                action="granted",
                capability="publisher",
                actor="alan",
                side_effect_class=SideEffectClass.PUBLIC_PUBLISH,
            )

            store.append_permission(record)
            loaded = store.read_permissions("mission_test")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].action, "granted")
            self.assertEqual(loaded[0].capability, "publisher")
            self.assertEqual(loaded[0].actor, "alan")
            self.assertEqual(loaded[0].side_effect_class, SideEffectClass.PUBLIC_PUBLISH)


class PermissionLedgerTests(unittest.TestCase):
    def test_approve_records_grant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )
            mission = orchestrator.create_mission("Test", "Done")

            orchestrator.approve_capability(mission.id, "publisher", approved_by="alan")

            records = store.read_permissions(mission.id)
            self.assertEqual(
                [(record.action, record.capability, record.actor) for record in records],
                [("granted", "publisher", "alan")],
            )

    def test_revoke_removes_approval_and_records_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )
            mission = orchestrator.create_mission("Test", "Done")
            orchestrator.approve_capability(mission.id, "publisher", approved_by="alan")

            updated = orchestrator.revoke_capability(
                mission.id, "publisher", revoked_by="alan"
            )

            self.assertNotIn("publisher", updated.approvals)
            actions = [record.action for record in store.read_permissions(mission.id)]
            self.assertEqual(actions, ["granted", "revoked"])

    def test_revoke_without_grant_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )
            mission = orchestrator.create_mission("Test", "Done")

            with self.assertRaises(ValueError):
                orchestrator.revoke_capability(mission.id, "publisher", revoked_by="alan")

    def test_intake_approvals_are_recorded_as_grants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )

            mission = orchestrator.create_mission(
                "Test", "Done", approvals={"publisher": "alan"}
            )

            records = store.read_permissions(mission.id)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].action, "granted")
            self.assertEqual(records[0].note, "granted during mission intake")

    def test_running_an_approved_side_effect_records_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )
            mission = orchestrator.create_mission("Test", "Done")
            orchestrator.approve_capability(mission.id, "publisher", approved_by="alan")
            mission = store.load_mission(mission.id)
            candidate = CandidateLoop(
                goal="Publish the post",
                success_criteria="Post is live",
                runner="mock",
                required_capabilities=[CapabilityRef(name="publisher")],
            )

            orchestrator._run_candidate(mission, 0, candidate, False)

            used = [
                record
                for record in store.read_permissions(mission.id)
                if record.action == "used"
            ]
            self.assertEqual(len(used), 1)
            self.assertEqual(used[0].capability, "publisher")
            self.assertEqual(used[0].candidate_loop_id, candidate.id)
            self.assertEqual(used[0].generation_index, 0)
            self.assertIn("approved by alan", used[0].note)

    def test_unapproved_side_effect_records_no_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(tmpdir)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=_registry_with_publisher()
            )
            mission = orchestrator.create_mission("Test", "Done")
            candidate = CandidateLoop(
                goal="Publish the post",
                success_criteria="Post is live",
                runner="mock",
                required_capabilities=[CapabilityRef(name="publisher")],
            )

            orchestrator._run_candidate(mission, 0, candidate, False)

            actions = [record.action for record in store.read_permissions(mission.id)]
            self.assertNotIn("used", actions)


if __name__ == "__main__":
    unittest.main()
