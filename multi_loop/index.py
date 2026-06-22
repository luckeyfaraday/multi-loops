"""SQLite search/lineage index over mission state.

This is an *additive, derived* index: the JSON files under
``.multi-loop/runs/<mission-id>/`` remain the source of truth, and the index can
be rebuilt from them at any time. It exists to answer questions the flat files
cannot answer efficiently — cross-mission ledger search and candidate lineage —
using only the standard-library ``sqlite3`` module, so the core stays
dependency-free.

The schema is versioned via ``PRAGMA user_version``; on a version mismatch the
index is dropped and rebuilt rather than migrated, which is safe precisely
because the index is derived.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import Mission

if TYPE_CHECKING:
    from .storage import MissionStore

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE missions (
    id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    schedule_state TEXT,
    generation_count INTEGER NOT NULL,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    role TEXT,
    state TEXT,
    fitness REAL
);
CREATE TABLE candidate_parents (
    candidate_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (candidate_id, parent_id)
);
CREATE TABLE ledger (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    generation_index INTEGER,
    candidate_loop_id TEXT,
    event_type TEXT,
    summary TEXT,
    created_at TEXT
);
CREATE TABLE outcomes (
    candidate_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    role TEXT,
    capability TEXT,
    success INTEGER NOT NULL,
    failure_class TEXT,
    failure_subreason TEXT,
    remedy_hint TEXT,
    fitness REAL,
    created_at TEXT
);
CREATE INDEX idx_candidates_mission ON candidates(mission_id);
CREATE INDEX idx_ledger_mission ON ledger(mission_id);
CREATE INDEX idx_outcomes_lookup ON outcomes(role, capability, failure_class);
"""


@dataclass(slots=True)
class LedgerHit:
    mission_id: str
    event_type: str
    summary: str
    generation_index: int | None
    candidate_loop_id: str | None
    created_at: str | None


@dataclass(slots=True)
class Lesson:
    """A failed-loop outcome retrieved as cross-mission learning."""

    candidate_id: str
    mission_id: str
    role: str | None
    capability: str | None
    failure_class: str | None
    failure_subreason: str | None
    remedy_hint: str | None
    fitness: float | None
    created_at: str | None


class MissionIndex:
    """A rebuildable SQLite index over mission, candidate, and ledger data."""

    def __init__(self, root: str | Path = ".multi-loop") -> None:
        self.root = Path(root)
        self.path = self.root / "index.db"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION and _has_tables(conn):
            return
        # Derived index: drop and recreate rather than migrate.
        for table in ("missions", "candidates", "candidate_parents", "ledger", "outcomes"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def rebuild(self, store: MissionStore) -> int:
        """Repopulate the index from the store's JSON state. Returns mission count."""
        missions = store.list_missions()
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("DELETE FROM missions")
            conn.execute("DELETE FROM candidates")
            conn.execute("DELETE FROM candidate_parents")
            conn.execute("DELETE FROM ledger")
            conn.execute("DELETE FROM outcomes")
            for mission in missions:
                self._index_mission(conn, mission)
                for entry in store.read_ledger(mission.id):
                    conn.execute(
                        "INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?)",
                        (
                            entry.id,
                            entry.mission_id,
                            entry.generation_index,
                            entry.candidate_loop_id,
                            entry.event_type,
                            entry.summary,
                            entry.created_at,
                        ),
                    )
            conn.commit()
        return len(missions)

    def _index_mission(self, conn: sqlite3.Connection, mission: Mission) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO missions VALUES (?,?,?,?,?,?,?)",
            (
                mission.id,
                mission.statement,
                mission.success_criteria,
                mission.schedule.state.value if mission.schedule else None,
                len(mission.generations),
                mission.created_at,
                mission.updated_at,
            ),
        )
        for generation in mission.generations:
            for candidate in generation.candidate_loops:
                conn.execute(
                    "INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?)",
                    (
                        candidate.id,
                        mission.id,
                        generation.index,
                        candidate.role,
                        candidate.state.value,
                        candidate.fitness.score if candidate.fitness else None,
                    ),
                )
                for parent_id in candidate.parent_ids:
                    conn.execute(
                        "INSERT OR REPLACE INTO candidate_parents VALUES (?,?)",
                        (candidate.id, parent_id),
                    )
                outcome = candidate.outcome
                if outcome is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate.id,
                            mission.id,
                            candidate.role,
                            _primary_capability(candidate),
                            1 if outcome.success else 0,
                            outcome.failure_class.value if outcome.failure_class else None,
                            outcome.failure_subreason,
                            outcome.remedy_hint,
                            candidate.fitness.score if candidate.fitness else None,
                            outcome.created_at,
                        ),
                    )

    def search_ledger(self, query: str, *, limit: int = 20) -> list[LedgerHit]:
        like = f"%{query.strip()}%"
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT mission_id, event_type, summary, generation_index, "
                "candidate_loop_id, created_at FROM ledger "
                "WHERE summary LIKE ? ORDER BY created_at DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [
            LedgerHit(
                mission_id=row["mission_id"],
                event_type=row["event_type"],
                summary=row["summary"],
                generation_index=row["generation_index"],
                candidate_loop_id=row["candidate_loop_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def search_missions(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{query.strip()}%"
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT id, statement, success_criteria, schedule_state, generation_count, "
                "updated_at FROM missions "
                "WHERE statement LIKE ? OR success_criteria LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def relevant_lessons(
        self,
        *,
        roles: list[str] | None = None,
        capabilities: list[str] | None = None,
        exclude_mission_id: str | None = None,
        limit: int = 5,
    ) -> list[Lesson]:
        """Return failed-loop lessons matching a role or capability, newest first.

        Ordering by ``created_at`` descending is the recency weighting: the most
        recent failures surface first, and the ``limit`` caps how many a caller
        injects. Successes and outcomes without a remedy hint are excluded since
        they carry no lesson.
        """
        roles = [role for role in (roles or []) if role]
        capabilities = [name for name in (capabilities or []) if name]
        if not roles and not capabilities:
            return []

        match_clauses: list[str] = []
        params: list[object] = []
        if roles:
            match_clauses.append(f"role IN ({','.join('?' * len(roles))})")
            params.extend(roles)
        if capabilities:
            match_clauses.append(f"capability IN ({','.join('?' * len(capabilities))})")
            params.extend(capabilities)

        where = (
            "success = 0 AND remedy_hint IS NOT NULL AND remedy_hint != '' "
            f"AND ({' OR '.join(match_clauses)})"
        )
        if exclude_mission_id:
            where += " AND mission_id != ?"
            params.append(exclude_mission_id)
        params.append(limit)

        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT candidate_id, mission_id, role, capability, failure_class, "
                "failure_subreason, remedy_hint, fitness, created_at FROM outcomes "
                f"WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            Lesson(
                candidate_id=row["candidate_id"],
                mission_id=row["mission_id"],
                role=row["role"],
                capability=row["capability"],
                failure_class=row["failure_class"],
                failure_subreason=row["failure_subreason"],
                remedy_hint=row["remedy_hint"],
                fitness=row["fitness"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def lineage(self, candidate_id: str) -> list[str]:
        """Return the candidate's ancestor ids (parents, grandparents, ...)."""
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                WITH RECURSIVE ancestors(id) AS (
                    SELECT parent_id FROM candidate_parents WHERE candidate_id = ?
                    UNION
                    SELECT cp.parent_id FROM candidate_parents cp
                    JOIN ancestors a ON cp.candidate_id = a.id
                )
                SELECT id FROM ancestors
                """,
                (candidate_id,),
            ).fetchall()
        return [row["id"] for row in rows]


def _primary_capability(candidate: Any) -> str:
    """The capability a lesson is keyed by; mirrors planning's default of agent_loop."""
    refs = candidate.required_capabilities
    return refs[0].name if refs else "agent_loop"


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='missions'"
    ).fetchone()
    return row is not None
