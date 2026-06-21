import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from multi_loop import (
    MissionBusy,
    MissionOrchestrator,
    MissionScheduler,
    MissionStore,
    acquire_mission_lease,
)


class LeaseTests(unittest.TestCase):
    def test_second_acquire_is_busy_until_released(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            mission = MissionOrchestrator(store=store).create_mission("M", "C")
            mission_dir = store.mission_dir(mission.id)

            lease = acquire_mission_lease(mission_dir, mission.id)
            with self.assertRaises(MissionBusy):
                acquire_mission_lease(mission_dir, mission.id)

            lease.release()
            # Released lease can be re-acquired (context-manager form).
            with acquire_mission_lease(mission_dir, mission.id):
                pass

    def test_run_generation_blocked_while_leased(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            mission = orchestrator.create_mission("M", "C")

            lease = acquire_mission_lease(store.mission_dir(mission.id), mission.id)
            try:
                with self.assertRaises(MissionBusy):
                    orchestrator.run_generation(mission.id)
            finally:
                lease.release()

            # Once the lease is freed the generation runs normally.
            result = orchestrator.run_generation(mission.id)
            self.assertEqual(result.generation_index, 0)

    def test_scheduler_skips_running_mission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            orchestrator = MissionOrchestrator(store=store)
            now = datetime.now(timezone.utc)
            mission = orchestrator.create_mission("M", "C", schedule="every 1d")
            mission.schedule.next_run_at = now.isoformat()
            store.save_mission(mission)

            lease = acquire_mission_lease(store.mission_dir(mission.id), mission.id)
            try:
                report = MissionScheduler(store=store, orchestrator=orchestrator).tick(now=now)
            finally:
                lease.release()
            loaded = store.load_mission(mission.id)

        self.assertEqual(len(report.ticked), 0)
        self.assertEqual(report.skipped[0].skipped_reason, "already_running")
        self.assertEqual(len(loaded.generations), 0)


if __name__ == "__main__":
    unittest.main()
