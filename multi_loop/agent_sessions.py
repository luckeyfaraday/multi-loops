"""Durable sessions for the conversational main-loop agent.

Sessions are append-only.  The metadata snapshot contains canonical state while
``entries.jsonl`` retains messages, decisions, tool activity, and compaction
checkpoints.  Context can therefore be rebuilt after a CLI/MCP process restart
without treating an LLM-generated summary as operational truth.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import Budget, from_dict, new_id, to_dict, utc_now_iso
from .policy import resolve_within

try:  # POSIX is the supported runtime; fallback keeps imports portable.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class AgentInterface(str, Enum):
    CLI = "cli"
    MCP = "mcp"


class AgentPhase(str, Enum):
    EXPLORING = "exploring"
    SCOPING = "scoping"
    READY_TO_CREATE = "ready_to_create"
    ACTIVE = "active"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"


class AgentSessionStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(slots=True)
class MissionDraft:
    statement: str = ""
    success_criteria: str = ""
    clarifications: dict[str, str] = field(default_factory=dict)
    requested_capabilities: list[str] = field(default_factory=list)
    schedule: str | None = None
    budget: Budget = field(default_factory=Budget)
    autonomy_level: str = "local_only"
    approval_policy: str = "ask before external actions"
    workspace: str | None = None
    confirmed_at: str | None = None


@dataclass(slots=True)
class MainLoopSession:
    interface: AgentInterface
    id: str = field(default_factory=lambda: new_id("session"))
    provider_id: str | None = None
    active_mission_id: str | None = None
    phase: AgentPhase = AgentPhase.EXPLORING
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    revision: int = 0
    leaf_entry_id: str | None = None
    working_summary: str = ""
    confirmed_decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    draft: MissionDraft = field(default_factory=MissionDraft)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    system_prompt: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class SessionEntry:
    session_id: str
    entry_type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("turn"))
    parent_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)


class SessionNotFound(FileNotFoundError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Main-loop session not found: {session_id}")
        self.session_id = session_id


class SessionConflict(RuntimeError):
    def __init__(self, session_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Session revision conflict for {session_id}: expected {expected}, actual {actual}."
        )
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


class MainLoopSessionStore:
    """Persist main-loop sessions under ``<root>/main-loop/sessions``."""

    def __init__(self, root: str | Path = ".multi-loop") -> None:
        self.root = Path(root)
        self.sessions_dir = self.root / "main-loop" / "sessions"

    def session_dir(self, session_id: str) -> Path:
        return resolve_within(self.sessions_dir, session_id)

    def create(self, session: MainLoopSession) -> Path:
        directory = self.session_dir(session.id)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "entries.jsonl").touch(mode=0o600)
        self._write_session(session)
        return directory

    def load(self, session_id: str) -> MainLoopSession:
        path = self.session_dir(session_id) / "session.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                return from_dict(MainLoopSession, json.load(handle))
        except FileNotFoundError as exc:
            raise SessionNotFound(session_id) from exc

    def list(self) -> list[MainLoopSession]:
        if not self.sessions_dir.exists():
            return []
        sessions: list[MainLoopSession] = []
        for path in sorted(self.sessions_dir.glob("*/session.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    sessions.append(from_dict(MainLoopSession, json.load(handle)))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def read_entries(self, session_id: str) -> list[SessionEntry]:
        path = self.session_dir(session_id) / "entries.jsonl"
        if not path.exists():
            if not (self.session_dir(session_id) / "session.json").exists():
                raise SessionNotFound(session_id)
            return []
        entries: list[SessionEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entries.append(from_dict(SessionEntry, json.loads(stripped)))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
        return entries

    def update(
        self,
        session: MainLoopSession,
        *,
        expected_revision: int | None = None,
        entry_type: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> tuple[MainLoopSession, SessionEntry | None]:
        """Atomically advance a session snapshot and optionally append an entry."""
        with self._lock(session.id):
            current = self.load(session.id)
            expected = session.revision if expected_revision is None else expected_revision
            if current.revision != expected:
                raise SessionConflict(session.id, expected, current.revision)

            appended = None
            if entry_type is not None:
                appended = SessionEntry(
                    session_id=session.id,
                    entry_type=entry_type,
                    data=data or {},
                    parent_id=current.leaf_entry_id,
                )
                self._append_entry(appended)
                session.leaf_entry_id = appended.id
            else:
                session.leaf_entry_id = current.leaf_entry_id

            session.revision = current.revision + 1
            session.updated_at = utc_now_iso()
            self._write_session(session)
            return session, appended

    def mutate(
        self,
        session_id: str,
        mutation: Callable[[MainLoopSession], None],
        *,
        expected_revision: int | None = None,
        entry_type: str | None = None,
        data: dict[str, Any] | Callable[[MainLoopSession], dict[str, Any]] | None = None,
    ) -> tuple[MainLoopSession, SessionEntry | None]:
        """Load, mutate, and persist under one session lock."""
        with self._lock(session_id):
            session = self.load(session_id)
            if expected_revision is not None and session.revision != expected_revision:
                raise SessionConflict(session_id, expected_revision, session.revision)
            mutation(session)
            appended = None
            if entry_type is not None:
                entry_data = data(session) if callable(data) else (data or {})
                appended = SessionEntry(
                    session_id=session.id,
                    entry_type=entry_type,
                    data=entry_data,
                    parent_id=session.leaf_entry_id,
                )
                self._append_entry(appended)
                session.leaf_entry_id = appended.id
            session.revision += 1
            session.updated_at = utc_now_iso()
            self._write_session(session)
            return session, appended

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        expected_revision: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MainLoopSession:
        session, _ = self.mutate(
            session_id,
            lambda _session: None,
            expected_revision=expected_revision,
            entry_type="message",
            data={"role": role, "content": content, "metadata": metadata or {}},
        )
        return session

    def append_entry(
        self,
        session_id: str,
        entry_type: str,
        data: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> tuple[MainLoopSession, SessionEntry]:
        session, entry = self.mutate(
            session_id,
            lambda _session: None,
            expected_revision=expected_revision,
            entry_type=entry_type,
            data=data,
        )
        assert entry is not None
        return session, entry

    def _append_entry(self, entry: SessionEntry) -> None:
        path = self.session_dir(entry.session_id) / "entries.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_dict(entry), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_session(self, session: MainLoopSession) -> None:
        directory = self.session_dir(session.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "session.json"
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(to_dict(session), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    @contextmanager
    def _lock(self, session_id: str) -> Iterator[None]:
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".session.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
