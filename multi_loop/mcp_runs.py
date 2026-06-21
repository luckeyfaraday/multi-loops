"""Detached MCP run records for monitorable generation runs."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

Emit = Callable[[str, dict[str, Any]], None]
Thunk = Callable[[Emit], dict[str, Any]]


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]


def mcp_runs_dir(root: str | Path = ".multi-loop") -> Path:
    return Path(root) / "mcp-runs"


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


@dataclass(slots=True)
class RunHandle:
    run_id: str
    run_dir: Path
    writer: "RunWriter"
    thread: threading.Thread


class RunWriter:
    """Thread-safe event/status/result writer for one detached run."""

    def __init__(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.events_path = run_dir / "events.jsonl"
        self.status_path = run_dir / "status.json"
        self.result_path = run_dir / "result.json"
        self._lock = threading.RLock()
        self._seq = 0
        self._events: list[dict[str, Any]] = []
        self.running = True
        self.phase = "starting"
        self.result: dict[str, Any] | None = None
        self.started_at = time.time()
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def begin(self, meta: dict[str, Any]) -> None:
        _atomic_write_json(self.run_dir / "meta.json", meta)
        self.emit("run_started", {"meta": meta})

    def emit(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": time.time(),
                "kind": kind,
                "data": data,
            }
            self._events.append(event)
            self.phase = kind
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            _atomic_write_json(self.status_path, self.status())
            return event

    def finish(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.result = result
            self.running = False
            _atomic_write_json(self.result_path, result)
            self.emit(
                "run_finished",
                {
                    "ok": "error" not in result,
                    "summary": result.get("summary") or result.get("message") or "finished",
                },
            )

    def fail(self, exc: BaseException) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        self.finish({"error": detail, "summary": f"failed: {detail}"})

    def status(self) -> dict[str, Any]:
        with self._lock:
            status: dict[str, Any] = {
                "run_id": self.run_id,
                "run_dir": str(self.run_dir),
                "running": self.running,
                "phase": self.phase,
                "events": self._seq,
                "started_at": self.started_at,
            }
            if self.result is not None:
                status["completed"] = "error" not in self.result
            return status

    def tail(self, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
        with self._lock:
            newer = [event for event in self._events if event["seq"] > cursor]
            page = newer[:limit]
            next_cursor = page[-1]["seq"] if page else cursor
            return {
                "run_id": self.run_id,
                "events": page,
                "cursor": next_cursor,
                "running": self.running,
                "more": len(newer) > len(page),
            }


class RunManager:
    """In-process detached run registry."""

    def __init__(self) -> None:
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()

    def start(self, *, thunk: Thunk, meta: dict[str, Any], base: str | Path) -> RunHandle:
        run_id = new_run_id()
        run_dir = Path(base) / run_id
        writer = RunWriter(run_id, run_dir)
        writer.begin(meta)

        def target() -> None:
            try:
                writer.finish(thunk(writer.emit))
            except BaseException as exc:  # noqa: BLE001 - keep failures inspectable
                writer.fail(exc)

        thread = threading.Thread(target=target, name=f"multi-loop-mcp-{run_id}", daemon=True)
        handle = RunHandle(run_id=run_id, run_dir=run_dir, writer=writer, thread=thread)
        with self._lock:
            self._runs[run_id] = handle
        thread.start()
        return handle

    def _get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        handle = self._get(run_id)
        if handle is None:
            return _unknown(run_id)
        return handle.writer.status()

    def tail(self, run_id: str, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
        handle = self._get(run_id)
        if handle is None:
            return _unknown(run_id)
        return handle.writer.tail(cursor, limit)

    def result(self, run_id: str, wait: bool = False, timeout: float | None = None) -> dict[str, Any]:
        handle = self._get(run_id)
        if handle is None:
            return _unknown(run_id)
        if wait and handle.thread.is_alive():
            handle.thread.join(timeout)
        if handle.writer.result is not None:
            return handle.writer.result
        status = handle.writer.status()
        status["status"] = "running"
        return status

    def list_runs(self) -> dict[str, Any]:
        with self._lock:
            handles = list(self._runs.values())
        return {"runs": [handle.writer.status() for handle in handles]}


def _unknown(run_id: str) -> dict[str, Any]:
    return {"error": f"unknown run_id {run_id!r}", "run_id": run_id}


MANAGER = RunManager()
