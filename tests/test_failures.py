import tempfile
import unittest
from pathlib import Path

from multi_loop import (
    Artifact,
    Budget,
    CandidateLoop,
    FailureClass,
    Mission,
    MissionOrchestrator,
    MissionStore,
    RuleBasedClassifier,
    RunRequest,
    RunResult,
    collect_pitfalls,
)
from multi_loop.models import Generation, Outcome
from multi_loop.planning import _raised_budget
from multi_loop.runners import RunnerRegistry, _agent_prompt
from multi_loop.verification import VerificationResult


def _failure(summary="failed", **metadata) -> RunResult:
    return RunResult(candidate_loop_id="c", success=False, summary=summary, metadata=metadata)


class RuleBasedClassifierTests(unittest.TestCase):
    classifier = RuleBasedClassifier()
    candidate = CandidateLoop(goal="g", success_criteria="s", id="c")

    def _classify(self, result: RunResult) -> Outcome:
        return self.classifier.classify(self.candidate, result)

    def test_success_has_no_failure_class(self):
        outcome = self._classify(
            RunResult(candidate_loop_id="c", success=True, summary="A complete useful result.")
        )
        self.assertTrue(outcome.success)
        self.assertIsNone(outcome.failure_class)
        self.assertEqual(outcome.remedy_hint, "")

    def test_policy_block_is_classified_separately_from_unavailable_tool(self):
        blocked = self._classify(
            _failure(summary="Policy gate blocked candidate: paid_ads requires approval", blocked_by_policy=True)
        )
        self.assertEqual(blocked.failure_class, FailureClass.POLICY_BLOCKED)
        self.assertEqual(blocked.severity, "blocking")

        unavailable = self._classify(
            _failure(summary="Required capability unavailable: web_research (needs setup)", blocked_by_policy=True)
        )
        self.assertEqual(unavailable.failure_class, FailureClass.TOOL_UNAVAILABLE)

    def test_timeout_is_resource_exhausted(self):
        outcome = self._classify(_failure(timed_out=True))
        self.assertEqual(outcome.failure_class, FailureClass.RESOURCE_EXHAUSTED)
        self.assertTrue(outcome.remedy_hint)

    def test_error_metadata_is_execution_error(self):
        outcome = self._classify(_failure(error="RuntimeError"))
        self.assertEqual(outcome.failure_class, FailureClass.EXECUTION_ERROR)
        self.assertEqual(outcome.signals.get("error"), "RuntimeError")

    def test_nonzero_exit_code_is_execution_error(self):
        outcome = self._classify(_failure(exit_code=2))
        self.assertEqual(outcome.failure_class, FailureClass.EXECUTION_ERROR)
        self.assertEqual(outcome.failure_subreason, "exit_2")

    def test_failed_verification_is_verification_failed(self):
        result = _failure(summary="ran but unproven", exit_code=0)
        result.verification = [
            VerificationResult(command="test -f out.txt", success=False, exit_code=1)
        ]
        outcome = self._classify(result)
        self.assertEqual(outcome.failure_class, FailureClass.VERIFICATION_FAILED)

    def test_thin_result_is_bad_output(self):
        outcome = self._classify(_failure(summary="nope"))
        self.assertEqual(outcome.failure_class, FailureClass.BAD_OUTPUT)

    def test_unclassified_failure_is_unknown(self):
        result = _failure(summary="A long, detailed but unattributable failure summary.")
        result.artifacts = [Artifact(path="a.md")]
        outcome = self._classify(result)
        self.assertEqual(outcome.failure_class, FailureClass.UNKNOWN)


class RaisedBudgetTests(unittest.TestCase):
    def test_doubles_bounded_dimensions_and_leaves_unset_alone(self):
        raised = _raised_budget(Budget(max_iterations=2, max_seconds=10.0, max_tokens=None))
        self.assertEqual(raised.max_iterations, 4)
        self.assertEqual(raised.max_seconds, 20.0)
        self.assertIsNone(raised.max_tokens)


class CollectPitfallsTests(unittest.TestCase):
    def _mission_with_failed_loop(self, capability: str) -> tuple[Mission, CandidateLoop]:
        failed = CandidateLoop(goal="prior", success_criteria="s", id="failed", role="research")
        failed.required_capabilities = []
        failed.outcome = Outcome(
            candidate_loop_id="failed",
            success=False,
            failure_class=FailureClass.RESOURCE_EXHAUSTED,
            remedy_hint="Reduce scope; the prior attempt timed out.",
        )
        # _capability_names defaults to ["agent_loop"] when none are declared.
        mission = Mission(statement="m", success_criteria="s")
        mission.generations.append(Generation(index=0, candidate_loops=[failed]))
        return mission, failed

    def test_returns_hint_for_loop_sharing_a_capability(self):
        mission, _ = self._mission_with_failed_loop("agent_loop")
        sibling = CandidateLoop(goal="next", success_criteria="s")  # also agent_loop by default
        hints = collect_pitfalls(mission, sibling)
        self.assertEqual(hints, ["Reduce scope; the prior attempt timed out."])

    def test_returns_hint_for_child_of_failed_loop(self):
        mission, failed = self._mission_with_failed_loop("agent_loop")
        child = CandidateLoop(goal="retry", success_criteria="s", parent_ids=[failed.id])
        self.assertIn("Reduce scope; the prior attempt timed out.", collect_pitfalls(mission, child))

    def test_skips_successful_and_irrelevant_loops(self):
        mission, _ = self._mission_with_failed_loop("agent_loop")
        mission.generations[0].candidate_loops[0].outcome.success = True
        sibling = CandidateLoop(goal="next", success_criteria="s")
        self.assertEqual(collect_pitfalls(mission, sibling), [])


class AgentPromptInjectionTests(unittest.TestCase):
    def test_pitfalls_are_injected_into_agent_prompt(self):
        request = RunRequest(
            mission=Mission(statement="m", success_criteria="s"),
            generation_index=1,
            candidate=CandidateLoop(goal="g", success_criteria="s"),
            mission_dir=Path("."),
            pitfalls=["Reduce scope; the prior attempt timed out."],
        )
        prompt = _agent_prompt(request)
        self.assertIn("Known pitfalls", prompt)
        self.assertIn("Reduce scope; the prior attempt timed out.", prompt)


class _RecordingFailRunner:
    """Always-failing runner that records the pitfalls each request carried."""

    name = "mock"

    def __init__(self, **metadata: object) -> None:
        self.metadata = metadata
        self.seen_pitfalls: list[list[str]] = []

    def run(self, request: RunRequest) -> RunResult:
        self.seen_pitfalls.append(list(request.pitfalls))
        return RunResult(
            candidate_loop_id=request.candidate.id,
            success=False,
            summary="boom",
            metadata=dict(self.metadata),
        )


class CauseAwareEvolutionTests(unittest.TestCase):
    def test_timeout_drives_rescoped_retry_and_records_outcome(self):
        runner = _RecordingFailRunner(timed_out=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store, runners=RunnerRegistry([runner]))
            mission = orchestrator.create_mission("Build a tool", "Ship it")

            orchestrator.run_generation(mission.id)
            orchestrator.run_generation(mission.id)
            loaded = store.load_mission(mission.id)
            events = store.read_events(mission.id)

        gen0 = loaded.generations[0]
        self.assertTrue(
            all(c.outcome and c.outcome.failure_class == FailureClass.RESOURCE_EXHAUSTED for c in gen0.candidate_loops)
        )

        gen1 = loaded.generations[1]
        self.assertTrue(any(c.role.endswith("_rescoped") for c in gen1.candidate_loops))
        self.assertTrue(any(m.startswith("retry_rescope:") for m in gen1.mutations))

        # The failure class is surfaced on the queryable candidate_finished event.
        finished = [e for e in events if e.event_type == "candidate_finished"]
        self.assertTrue(any(e.data.get("failure_class") == "resource_exhausted" for e in finished))

        # The second generation's runs carried pitfall lessons from the first.
        self.assertTrue(any(pitfalls for pitfalls in runner.seen_pitfalls))


if __name__ == "__main__":
    unittest.main()
