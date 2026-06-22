import os
import tempfile
import unittest
from pathlib import Path

from multi_loop import (
    Capability,
    CapabilityRef,
    CapabilityRegistry,
    CandidateLoop,
    Mission,
    MissionStore,
    PathEscape,
    SideEffectClass,
    prepare_candidate,
    resolve_within,
    side_effect_directive,
)
from multi_loop.policy import candidate_blocked_now


def _registry_with_available(side_effect: SideEffectClass) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="publish_site",
            description="Publish to an external site.",
            toolset_or_backend="external",
            side_effect_class=side_effect,
        ),
        check=lambda: True,
    )
    return registry


def _candidate() -> CandidateLoop:
    return CandidateLoop(
        goal="Publish the landing page",
        success_criteria="Page is live",
        required_capabilities=[CapabilityRef(name="publish_site")],
    )


class PolicyGateTests(unittest.TestCase):
    def test_side_effecting_capability_blocks_without_approval(self):
        registry = _registry_with_available(SideEffectClass.EXTERNAL_WRITE)
        candidate = _candidate()
        mission = Mission(statement="m", success_criteria="c")

        reason = prepare_candidate(candidate, mission, registry)

        self.assertIsNotNone(reason)
        self.assertIn("requires approval", reason)
        self.assertTrue(candidate_blocked_now(_candidate(), mission, registry))

    def test_recorded_approval_clears_the_gate(self):
        registry = _registry_with_available(SideEffectClass.SPEND_MONEY)
        candidate = _candidate()
        mission = Mission(statement="m", success_criteria="c", approvals={"publish_site": "user"})

        reason = prepare_candidate(candidate, mission, registry)

        self.assertIsNone(reason)
        self.assertFalse(candidate_blocked_now(_candidate(), mission, registry))

    def test_read_only_capability_needs_no_gate(self):
        registry = _registry_with_available(SideEffectClass.READ_ONLY)
        candidate = _candidate()
        mission = Mission(statement="m", success_criteria="c")

        self.assertIsNone(prepare_candidate(candidate, mission, registry))
        self.assertEqual(candidate.policy_gates, [])


class SideEffectDirectiveTests(unittest.TestCase):
    def test_denies_outward_actions_by_default(self):
        registry = _registry_with_available(SideEffectClass.READ_ONLY)
        mission = Mission(statement="m", success_criteria="c")
        directive = side_effect_directive(_candidate(), mission, registry)
        self.assertIn("NONE PERMITTED", directive)
        self.assertIn("Do NOT merge", directive)

    def test_global_flag_does_not_bypass_capability_scope(self):
        registry = _registry_with_available(SideEffectClass.READ_ONLY)
        mission = Mission(statement="m", success_criteria="c")
        directive = side_effect_directive(_candidate(), mission, registry, allow_side_effects=True)
        self.assertIn("NONE PERMITTED", directive)

    def test_allows_with_approved_side_effecting_capability(self):
        registry = _registry_with_available(SideEffectClass.EXTERNAL_WRITE)
        mission = Mission(statement="m", success_criteria="c", approvals={"publish_site": "user"})
        directive = side_effect_directive(_candidate(), mission, registry)
        self.assertIn("APPROVED", directive)

    def test_unapproved_side_effecting_capability_still_denies(self):
        registry = _registry_with_available(SideEffectClass.EXTERNAL_WRITE)
        mission = Mission(statement="m", success_criteria="c")  # no approval recorded
        directive = side_effect_directive(_candidate(), mission, registry)
        self.assertIn("NONE PERMITTED", directive)


class ResolveWithinTests(unittest.TestCase):
    def test_valid_relative_path_stays_inside(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target = resolve_within(base, "artifacts/generation-0/x.md")
            self.assertTrue(str(target).startswith(str(base.resolve())))

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(PathEscape):
                resolve_within(Path(tmpdir), "../escape.md")

    def test_absolute_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(PathEscape):
                resolve_within(Path(tmpdir), "/etc/passwd")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
            base = Path(tmpdir)
            os.symlink(outside, base / "link")
            with self.assertRaises(PathEscape):
                resolve_within(base, "link/secret.md")

    def test_store_write_artifact_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            mission = Mission(statement="m", success_criteria="c")
            store.create_mission(mission)
            with self.assertRaises(PathEscape):
                store.write_artifact(mission.id, "../../escape.md", "nope")


if __name__ == "__main__":
    unittest.main()
