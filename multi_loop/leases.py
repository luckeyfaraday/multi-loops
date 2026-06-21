"""Per-mission run leases.

A mission generation is a long, stateful critical section: it loads the
mission, appends a generation, runs candidates, and saves repeatedly. Two
concurrent runs on the same mission (e.g. a scheduled tick overlapping a
detached MCP run) would both create the same generation index and clobber
each other's writes.

``acquire_mission_lease`` provides mutual exclusion for one mission across
threads and processes:

- An in-process ``threading.Lock`` rejects a second run in the same process.
- A ``flock`` on ``<mission-dir>/.run.lock`` rejects runs from other processes
  (the CLI, the MCP server, a separate scheduler). The kernel releases a
  ``flock`` automatically when the holding process exits, so a crashed runner
  never leaves a permanently stuck mission — no manual staleness handling.

When ``fcntl`` is unavailable (e.g. Windows) the lease degrades to in-process
protection only, which still prevents the common same-process overlap.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:  # POSIX advisory locking; absent on some platforms.
    import fcntl
except ImportError:  # pragma: no cover - exercised only without fcntl
    fcntl = None  # type: ignore[assignment]

_registry_guard = threading.Lock()
_local_locks: dict[str, threading.Lock] = {}


class MissionBusy(RuntimeError):
    """Raised when a mission already has a generation running."""

    def __init__(self, mission_id: str) -> None:
        super().__init__(f"Mission is already running: {mission_id}")
        self.mission_id = mission_id


def _local_lock(mission_id: str) -> threading.Lock:
    with _registry_guard:
        lock = _local_locks.get(mission_id)
        if lock is None:
            lock = threading.Lock()
            _local_locks[mission_id] = lock
        return lock


@dataclass(slots=True)
class MissionLease:
    mission_id: str
    path: Path
    _handle: object | None
    _thread_lock: threading.Lock
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if self._handle is not None:
                if fcntl is not None:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
        finally:
            self._thread_lock.release()

    def __enter__(self) -> "MissionLease":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_mission_lease(mission_dir: Path, mission_id: str) -> MissionLease:
    """Acquire an exclusive run lease for ``mission_id`` or raise ``MissionBusy``.

    The returned lease is a context manager; call ``release()`` (or use ``with``)
    to free it. Acquisition is non-blocking: a held lease raises immediately
    rather than waiting, so a second runner skips instead of stacking up.
    """
    thread_lock = _local_lock(mission_id)
    if not thread_lock.acquire(blocking=False):
        raise MissionBusy(mission_id)

    handle = None
    path = mission_dir / ".run.lock"
    try:
        if fcntl is not None:
            # The lock file is intentionally never deleted: unlinking it would
            # let a later acquirer create a fresh inode and lock it while an
            # existing holder still holds the old inode's lock.
            handle = path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise MissionBusy(mission_id) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "acquired_at": time.time()}))
            handle.flush()
    except BaseException:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        thread_lock.release()
        raise

    return MissionLease(mission_id=mission_id, path=path, _handle=handle, _thread_lock=thread_lock)
