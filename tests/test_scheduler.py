import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from multi_loop import (
    MissionOrchestrator,
    MissionScheduler,
    MissionStore,
    ScheduleNotConfigured,
    ScheduleState,
)


def _due_scheduled_mission(store, orchestrator, schedule="every 1d", *, now=None):
    """Create a mission whose schedule is due right now."""
    mission = orchestrator.create_mission(
        "Monitor a recurring task",
        "Produce one generation per tick",
        schedule=schedule,
    )
    mission.schedule.next_run_at = (now or datetime.now(timezone.utc)).isoformat()
    store.save_mission(mission)
    return mission


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


    def test_paused_schedule_is_skipped_but_visible(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = _due_scheduled_mission(store, orchestrator)
            orchestrator.pause_schedule(mission.id, reason="manual hold")

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick()
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(report.ticked), 0)
        self.assertEqual(len(report.skipped), 1)
        self.assertEqual(report.skipped[0].skipped_reason, "paused")
        self.assertEqual(loaded.schedule.state, ScheduleState.PAUSED)
        self.assertEqual(loaded.schedule.paused_reason, "manual hold")
        # Paused, not terminal: still enabled so resume can re-activate it.
        self.assertTrue(loaded.schedule.enabled)

    def test_resume_then_tick_runs_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = _due_scheduled_mission(store, orchestrator)
            orchestrator.pause_schedule(mission.id)
            now = datetime.now(timezone.utc)
            orchestrator.resume_schedule(mission.id, now=now)
            orchestrator.trigger_schedule(mission.id)

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick()
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(report.ticked), 1)
        self.assertEqual(loaded.schedule.state, ScheduleState.SCHEDULED)
        self.assertEqual(loaded.schedule.last_status, "ok")

    def test_one_shot_completes_and_disables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = _due_scheduled_mission(store, orchestrator, schedule="30m")
            scheduler = MissionScheduler(store=store, orchestrator=orchestrator)

            first = scheduler.tick()
            after_first = store.load_mission(mission.id)
            second = scheduler.tick()

        self.assertEqual(len(first.ticked), 1)
        self.assertEqual(after_first.schedule.state, ScheduleState.COMPLETED)
        self.assertFalse(after_first.schedule.enabled)
        self.assertIsNone(after_first.schedule.next_run_at)
        # A completed one-shot is no longer reconsidered.
        self.assertEqual(len(second.ticked), 0)
        self.assertEqual(len(second.skipped), 0)

    def test_stale_recurring_run_is_fast_forwarded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            now = datetime.now(timezone.utc)
            mission = orchestrator.create_mission(
                "Hourly monitor",
                "One generation per hour",
                schedule="every 1h",
            )
            # Scheduled for an hour ago — past the catch-up grace window (30m).
            mission.schedule.next_run_at = (now - timedelta(hours=1)).isoformat()
            store.save_mission(mission)

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick(now=now)
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(report.ticked), 0)
        self.assertEqual(report.skipped[0].skipped_reason, "fast_forwarded")
        # No generation ran, and the next run was moved into the future.
        self.assertEqual(len(loaded.generations), 0)
        self.assertGreater(datetime.fromisoformat(loaded.schedule.next_run_at), now)

    def test_failed_generation_records_error_and_consumes_budget(self):
        class _Boom(MissionOrchestrator):
            def run_generation(self, *args, **kwargs):
                raise RuntimeError("planner exploded")

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = _Boom(store=store)
            now = datetime.now(timezone.utc)
            mission = _due_scheduled_mission(store, orchestrator, now=now)
            mission.schedule.max_generation_steps = 1
            store.save_mission(mission)

            report = MissionScheduler(store=store, orchestrator=orchestrator).tick(now=now)
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(report.ticked), 1)
        self.assertEqual(report.ticked[0].last_status, "error")
        self.assertIn("planner exploded", loaded.schedule.last_error)
        # Budget is consumed even on failure so a broken mission can't loop forever.
        self.assertFalse(loaded.schedule.enabled)

    def test_schedule_ops_require_a_schedule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("No schedule", "Do a thing")
            with self.assertRaises(ScheduleNotConfigured):
                orchestrator.pause_schedule(mission.id)


if __name__ == "__main__":
    unittest.main()
