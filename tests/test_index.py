import sqlite3
import tempfile
import unittest
from pathlib import Path

from multi_loop import MissionIndex, MissionOrchestrator, MissionStore


def _store_with_runs(tmpdir, *, generations=2):
    store = MissionStore(Path(tmpdir) / ".multi-loop")
    orchestrator = MissionOrchestrator(store=store)
    mission = orchestrator.create_mission("Build a project management SaaS", "Ship an MVP")
    for _ in range(generations):
        orchestrator.run_generation(mission.id)
    return store, mission


class MissionIndexTests(unittest.TestCase):
    def test_rebuild_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, _ = _store_with_runs(tmpdir)
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            self.assertEqual(index.rebuild(store), 1)
            self.assertEqual(index.rebuild(store), 1)

    def test_search_ledger_finds_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, mission = _store_with_runs(tmpdir)
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            index.rebuild(store)

            hits = index.search_ledger("synthesized")

            self.assertTrue(hits)
            self.assertTrue(all(hit.mission_id == mission.id for hit in hits))

    def test_search_missions_by_statement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, mission = _store_with_runs(tmpdir, generations=1)
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            index.rebuild(store)

            results = index.search_missions("SaaS")

            self.assertEqual(results[0]["id"], mission.id)
            self.assertEqual(index.search_missions("no-such-text-xyz"), [])

    def test_lineage_walks_multiple_levels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, mission = _store_with_runs(tmpdir, generations=3)
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            index.rebuild(store)
            loaded = store.load_mission(mission.id)

            child = next(c for c in loaded.generations[2].candidate_loops if c.parent_ids)
            parent_id = child.parent_ids[0]
            parent = next(c for c in loaded.generations[1].candidate_loops if c.id == parent_id)

            ancestors = index.lineage(child.id)

            self.assertIn(parent_id, ancestors)
            if parent.parent_ids:  # grandparent should be reachable transitively
                self.assertIn(parent.parent_ids[0], ancestors)

    def test_rebuild_recovers_from_bad_schema_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store, _ = _store_with_runs(tmpdir, generations=1)
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            index.rebuild(store)
            with sqlite3.connect(index.path) as conn:
                conn.execute("PRAGMA user_version=999")

            self.assertEqual(index.rebuild(store), 1)

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MissionStore(Path(tmpdir) / ".multi-loop")
            index = MissionIndex(Path(tmpdir) / ".multi-loop")
            self.assertEqual(index.rebuild(store), 0)
            self.assertEqual(index.search_ledger("anything"), [])
            self.assertEqual(index.lineage("loop_missing"), [])


if __name__ == "__main__":
    unittest.main()
