import tempfile
import unittest
from pathlib import Path

from multi_loop import (
    Artifact,
    CandidateLoop,
    CapabilityRef,
    Event,
    Generation,
    LedgerEntry,
    Mission,
    MissionSchedule,
    MissionStore,
)


class MissionStoreTests(unittest.TestCase):
    def test_mission_and_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            mission = Mission(
                statement="Start a company.",
                success_criteria="Produce a validated 30-day launch plan.",
                schedule=MissionSchedule(expression="every 1d"),
            )
            mission.generations.append(
                Generation(
                    index=0,
                    candidate_loops=[
                        CandidateLoop(
                            goal="Research markets",
                            success_criteria="Return three scored markets.",
                            required_capabilities=[CapabilityRef(name="agent_loop")],
                        )
                    ],
                )
            )

            store.create_mission(mission)
            entry = LedgerEntry(
                mission_id=mission.id,
                generation_index=0,
                candidate_loop_id=mission.generations[0].candidate_loops[0].id,
                event_type="candidate_completed",
                summary="Market research loop completed.",
                artifacts=[Artifact(path="artifacts/market-research.md")],
            )
            store.append_ledger(entry)
            store.append_event(
                Event(
                    mission_id=mission.id,
                    event_type="candidate_completed",
                    candidate_loop_id=mission.generations[0].candidate_loops[0].id,
                    data={"ok": True},
                )
            )

            loaded = store.load_mission(mission.id)
            ledger = store.read_ledger(mission.id)
            events = store.read_events(mission.id)

        self.assertEqual(loaded.id, mission.id)
        self.assertEqual(loaded.schedule.expression, "every 1d")
        self.assertEqual(loaded.generations[0].candidate_loops[0].required_capabilities[0].name, "agent_loop")
        self.assertEqual(ledger[0].summary, "Market research loop completed.")
        self.assertEqual(ledger[0].artifacts[0].path, "artifacts/market-research.md")
        self.assertEqual(events[0].event_type, "candidate_completed")
        self.assertTrue(events[0].data["ok"])

    def test_mission_id_must_not_escape_runs_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".multi-loop"
            store = MissionStore(root)
            (root / "runs").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "single safe path segment"):
                store.load_mission("../../outside")
            with self.assertRaisesRegex(ValueError, "single safe path segment"):
                store.create_mission(
                    Mission(id="../outside", statement="x", success_criteria="y")
                )

            self.assertFalse((Path(tmpdir) / "outside").exists())


if __name__ == "__main__":
    unittest.main()
