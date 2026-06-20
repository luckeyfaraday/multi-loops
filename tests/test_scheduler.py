import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from multi_loop import MissionOrchestrator, MissionScheduler, MissionStore


class SchedulerTests(unittest.TestCase):
    def test_tick_runs_due_scheduled_mission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Monitor a recurring task",
                "Produce one generation per tick",
                schedule="every 1d",
            )
            mission.schedule.next_run_at = datetime.now(timezone.utc).isoformat()
            store.save_mission(mission)

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick()

        self.assertEqual(len(report.ticked), 1)
        self.assertEqual(report.ticked[0].generation_index, 0)
        self.assertIsNotNone(report.ticked[0].next_run_at)

    def test_tick_runs_recurring_mission_on_later_due_tick(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Monitor a recurring task",
                "Produce one generation per tick",
                schedule="every 1d",
            )
            now = datetime.now(timezone.utc)
            mission.schedule.next_run_at = now.isoformat()
            store.save_mission(mission)
            scheduler = MissionScheduler(store=store, orchestrator=orchestrator)

            first_report = scheduler.tick(now=now)
            refreshed = store.load_mission(mission.id)
            refreshed.schedule.next_run_at = (now + timedelta(days=1)).isoformat()
            store.save_mission(refreshed)
            second_report = scheduler.tick(now=now + timedelta(days=1, seconds=1))
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(first_report.ticked), 1)
        self.assertEqual(len(second_report.ticked), 1)
        self.assertEqual(len(loaded.generations), 2)

    def test_tick_skips_mission_that_is_not_due(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Monitor a recurring task",
                "Produce one generation per tick",
                schedule="every 1d",
            )
            future = datetime.now(timezone.utc) + timedelta(days=2)
            mission.schedule.next_run_at = future.isoformat()
            store.save_mission(mission)

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick()

        self.assertEqual(len(report.ticked), 0)
        self.assertEqual(len(report.skipped), 1)
        self.assertEqual(report.skipped[0].skipped_reason, "not_due")

    def test_tick_stops_after_max_generation_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission(
                "Capped recurring mission",
                "Run a limited number of generations",
                schedule="every 1d",
            )
            now = datetime.now(timezone.utc)
            mission.schedule.next_run_at = now.isoformat()
            mission.schedule.max_generation_steps = 2
            store.save_mission(mission)
            scheduler = MissionScheduler(store=store, orchestrator=orchestrator)

            first = scheduler.tick(now=now)
            second = scheduler.tick(now=now + timedelta(days=1, seconds=1))
            third = scheduler.tick(now=now + timedelta(days=2, seconds=2))
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(first.ticked), 1)
        self.assertEqual(len(second.ticked), 1)
        # Once exhausted the schedule is disabled, so it stops being reconsidered
        # rather than emitting a skip entry on every subsequent tick.
        self.assertEqual(len(third.ticked), 0)
        self.assertEqual(len(third.skipped), 0)
        self.assertFalse(loaded.schedule.enabled)
        self.assertEqual(len(loaded.generations), 2)


if __name__ == "__main__":
    unittest.main()
