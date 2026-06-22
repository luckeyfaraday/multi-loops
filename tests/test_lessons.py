import tempfile
import unittest
from pathlib import Path

from multi_loop import (
    CandidateLoop,
    CapabilityRef,
    FailureClass,
    Mission,
    MissionIndex,
    MissionOrchestrator,
    MissionStore,
    RunRequest,
    RunResult,
)
from multi_loop.capabilities import default_capabilities
from multi_loop.models import Generation, Outcome
from multi_loop.runners import RunnerRegistry


def _failed_candidate(cid: str, role: str, *, capability: str | None, remedy: str,
                      failure_class: FailureClass, created_at: str) -> CandidateLoop:
    refs = [CapabilityRef(name=capability)] if capability else []
    candidate = CandidateLoop(goal="g", success_criteria="s", id=cid, role=role, required_capabilities=refs)
    candidate.outcome = Outcome(
        candidate_loop_id=cid,
        success=False,
        failure_class=failure_class,
        remedy_hint=remedy,
        signals={"capability": capability} if capability and failure_class is FailureClass.TOOL_UNAVAILABLE else {},
        created_at=created_at,
    )
    return candidate


def _store_with_outcomes(tmpdir: str, candidates: list[CandidateLoop], *, mission_id="mission_a") -> MissionStore:
    store = MissionStore(Path(tmpdir) / ".multi-loop")
    mission = Mission(statement="prior", success_criteria="s", id=mission_id)
    mission.generations.append(Generation(index=0, candidate_loops=candidates))
    store.create_mission(mission)
    return store


class RelevantLessonsTests(unittest.TestCase):
    def test_matches_by_role_and_excludes_successes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            failed = _failed_candidate(
                "c1", "research", capability=None, remedy="reduce scope",
                failure_class=FailureClass.RESOURCE_EXHAUSTED, created_at="2026-01-01T00:00:00+00:00",
            )
            ok = CandidateLoop(goal="g", success_criteria="s", id="c2", role="research")
            ok.outcome = Outcome(candidate_loop_id="c2", success=True)
            store = _store_with_outcomes(tmpdir, [failed, ok])
            index = MissionIndex(store.root)
            index.rebuild(store)

            lessons = index.relevant_lessons(roles=["research"], capabilities=[])

            self.assertEqual([lesson.remedy_hint for lesson in lessons], ["reduce scope"])

    def test_requires_a_role_or_capability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _store_with_outcomes(tmpdir, [])
            index = MissionIndex(store.root)
            index.rebuild(store)
            self.assertEqual(index.relevant_lessons(roles=[], capabilities=[]), [])

    def test_excludes_named_mission_and_orders_by_recency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            older = _failed_candidate(
                "c1", "strategy", capability="agent_loop", remedy="older",
                failure_class=FailureClass.BAD_OUTPUT, created_at="2026-01-01T00:00:00+00:00",
            )
            newer = _failed_candidate(
                "c2", "strategy", capability="agent_loop", remedy="newer",
                failure_class=FailureClass.BAD_OUTPUT, created_at="2026-03-01T00:00:00+00:00",
            )
            store = _store_with_outcomes(tmpdir, [older, newer], mission_id="mission_keep")
            index = MissionIndex(store.root)
            index.rebuild(store)

            ordered = index.relevant_lessons(roles=[], capabilities=["agent_loop"])
            self.assertEqual([lesson.remedy_hint for lesson in ordered], ["newer", "older"])

            excluded = index.relevant_lessons(
                roles=[], capabilities=["agent_loop"], exclude_mission_id="mission_keep"
            )
            self.assertEqual(excluded, [])

    def test_matches_any_required_capability_and_tracks_the_failed_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            failed = _failed_candidate(
                "c1", "content_research", capability="media_generation",
                remedy="configure media generation",
                failure_class=FailureClass.TOOL_UNAVAILABLE,
                created_at="2026-01-01T00:00:00+00:00",
            )
            failed.required_capabilities = [
                CapabilityRef(name="web_research"),
                CapabilityRef(name="media_generation"),
            ]
            store = _store_with_outcomes(tmpdir, [failed])
            index = MissionIndex(store.root)
            index.rebuild(store)

            lessons = index.relevant_lessons(
                roles=[], capabilities=["media_generation"]
            )
            capabilities = default_capabilities()
            capabilities.register(
                capabilities.require("web_research"), check=lambda: True, override=True
            )
            orchestrator = MissionOrchestrator(
                store=store, capabilities=capabilities, lessons_index=index
            )
            target_mission = Mission(statement="b", success_criteria="s", id="mission_b")
            target = CandidateLoop(
                goal="g",
                success_criteria="s",
                role="content_research",
                required_capabilities=[
                    CapabilityRef(name="web_research"),
                    CapabilityRef(name="media_generation"),
                ],
            )
            hints = orchestrator._cross_mission_pitfalls(target_mission, target)

        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].capability, "media_generation")
        self.assertEqual(hints, ["configure media generation"])


class _RecordingFailRunner:
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


class CrossMissionLearningTests(unittest.TestCase):
    def test_lesson_from_one_mission_reaches_a_later_mission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            index = MissionIndex(store.root)

            runner_a = _RecordingFailRunner(timed_out=True)
            orchestrator_a = MissionOrchestrator(
                store=store, runners=RunnerRegistry([runner_a]), lessons_index=index
            )
            mission_a = orchestrator_a.create_mission("Build tool A", "Ship A")
            orchestrator_a.run_generation(mission_a.id)

            runner_b = _RecordingFailRunner(timed_out=True)
            orchestrator_b = MissionOrchestrator(
                store=store, runners=RunnerRegistry([runner_b]), lessons_index=index
            )
            mission_b = orchestrator_b.create_mission("Build tool B", "Ship B")
            orchestrator_b.run_generation(mission_b.id)

        # Mission B's very first candidate has no prior sibling of its own, so the
        # pitfalls it carries can only have come from mission A across the index.
        self.assertTrue(runner_b.seen_pitfalls[0])
        first_hint = runner_b.seen_pitfalls[0][0]
        self.assertTrue("budget" in first_hint or "time" in first_hint)

    def test_without_index_first_candidate_has_no_pitfalls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            runner_a = _RecordingFailRunner(timed_out=True)
            orchestrator_a = MissionOrchestrator(store=store, runners=RunnerRegistry([runner_a]))
            mission_a = orchestrator_a.create_mission("Build tool A", "ship")
            orchestrator_a.run_generation(mission_a.id)

            runner_b = _RecordingFailRunner(timed_out=True)
            orchestrator_b = MissionOrchestrator(store=store, runners=RunnerRegistry([runner_b]))
            mission_b = orchestrator_b.create_mission("Build tool B", "ship")
            orchestrator_b.run_generation(mission_b.id)

        # No index means no cross-mission carryover: the first candidate (which has
        # no prior same-mission sibling either) sees nothing from mission A.
        self.assertEqual(runner_b.seen_pitfalls[0], [])


class StaleToolLessonTests(unittest.TestCase):
    def _index_with_tool_lesson(self, tmpdir: str) -> tuple[MissionStore, MissionIndex]:
        failed = _failed_candidate(
            "ca", "market_research", capability="web_research",
            remedy="tool unavailable; configure it",
            failure_class=FailureClass.TOOL_UNAVAILABLE, created_at="2026-01-01T00:00:00+00:00",
        )
        store = _store_with_outcomes(tmpdir, [failed])
        index = MissionIndex(store.root)
        index.rebuild(store)
        return store, index

    def _target(self) -> tuple[Mission, CandidateLoop]:
        mission = Mission(statement="b", success_criteria="s", id="mission_b")
        candidate = CandidateLoop(
            goal="g", success_criteria="s", role="market_research",
            required_capabilities=[CapabilityRef(name="web_research")],
        )
        return mission, candidate

    def test_lesson_kept_while_capability_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, index = self._index_with_tool_lesson(tmpdir)
            orchestrator = MissionOrchestrator(store=store, lessons_index=index)
            mission, candidate = self._target()
            hints = orchestrator._cross_mission_pitfalls(mission, candidate)
            self.assertIn("tool unavailable; configure it", hints)

    def test_lesson_suppressed_once_capability_is_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, index = self._index_with_tool_lesson(tmpdir)
            capabilities = default_capabilities()
            capabilities.register(capabilities.require("web_research"), check=lambda: True, override=True)
            orchestrator = MissionOrchestrator(
                store=store, capabilities=capabilities, lessons_index=index
            )
            mission, candidate = self._target()
            hints = orchestrator._cross_mission_pitfalls(mission, candidate)
            self.assertNotIn("tool unavailable; configure it", hints)

    def test_stale_and_duplicate_lessons_do_not_hide_an_older_valid_lesson(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = [
                _failed_candidate(
                    "valid", "research", capability="agent_loop", remedy="use smaller steps",
                    failure_class=FailureClass.RESOURCE_EXHAUSTED,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
                *[
                    _failed_candidate(
                        f"stale-{index}", "research", capability="agent_loop",
                        remedy="configure agent loop",
                        failure_class=FailureClass.TOOL_UNAVAILABLE,
                        created_at=f"2026-0{index + 2}-01T00:00:00+00:00",
                    )
                    for index in range(3)
                ],
            ]
            store = _store_with_outcomes(tmpdir, candidates)
            index = MissionIndex(store.root)
            index.rebuild(store)
            orchestrator = MissionOrchestrator(store=store, lessons_index=index)
            mission = Mission(statement="b", success_criteria="s", id="mission_b")
            candidate = CandidateLoop(goal="g", success_criteria="s", role="research")

            hints = orchestrator._cross_mission_pitfalls(mission, candidate)

        self.assertEqual(hints, ["use smaller steps"])


if __name__ == "__main__":
    unittest.main()
