"""File-backed mission storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeVar

from .models import Event, LedgerEntry, Mission, PermissionRecord, from_dict, to_dict, utc_now_iso
from .policy import resolve_within

_R = TypeVar("_R")


class MissionNotFound(FileNotFoundError):
    """Raised when a mission is requested but does not exist on disk."""

    def __init__(self, mission_id: str) -> None:
        super().__init__(f"Mission not found: {mission_id}")
        self.mission_id = mission_id


class MissionStore:
    """Persist missions under `.multi-loop/runs/<mission-id>/`."""

    def __init__(self, root: str | Path = ".multi-loop") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"

    def mission_dir(self, mission_id: str) -> Path:
        return resolve_within(self.runs_dir, _safe_mission_id(mission_id))

    def create_mission(self, mission: Mission) -> Path:
        mission_dir = self.mission_dir(mission.id)
        mission_dir.mkdir(parents=True, exist_ok=False)
        (mission_dir / "artifacts").mkdir()
        self.save_mission(mission)
        return mission_dir

    def save_mission(self, mission: Mission) -> Path:
        mission.updated_at = utc_now_iso()
        mission_dir = self.mission_dir(mission.id)
        mission_dir.mkdir(parents=True, exist_ok=True)
        path = mission_dir / "mission.json"
        _atomic_write_json(path, to_dict(mission))
        return path

    def load_mission(self, mission_id: str) -> Mission:
        path = self.mission_dir(mission_id) / "mission.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                return from_dict(Mission, json.load(handle))
        except FileNotFoundError as exc:
            raise MissionNotFound(mission_id) from exc

    def list_missions(self) -> list[Mission]:
        if not self.runs_dir.exists():
            return []
        missions: list[Mission] = []
        for mission_path in sorted(self.runs_dir.glob("*/mission.json")):
            with mission_path.open("r", encoding="utf-8") as handle:
                missions.append(from_dict(Mission, json.load(handle)))
        return missions

    def append_ledger(self, entry: LedgerEntry) -> Path:
        return self._append_jsonl(entry.mission_id, "ledger.jsonl", entry)

    def read_ledger(self, mission_id: str) -> list[LedgerEntry]:
        return self._read_jsonl(mission_id, "ledger.jsonl", LedgerEntry)

    def append_event(self, event: Event) -> Path:
        return self._append_jsonl(event.mission_id, "events.jsonl", event)

    def read_events(self, mission_id: str) -> list[Event]:
        return self._read_jsonl(mission_id, "events.jsonl", Event)

    def append_permission(self, record: PermissionRecord) -> Path:
        return self._append_jsonl(record.mission_id, "permissions.jsonl", record)

    def read_permissions(self, mission_id: str) -> list[PermissionRecord]:
        return self._read_jsonl(mission_id, "permissions.jsonl", PermissionRecord)

    def _append_jsonl(self, mission_id: str, filename: str, record: object) -> Path:
        mission_dir = self.mission_dir(mission_id)
        mission_dir.mkdir(parents=True, exist_ok=True)
        path = mission_dir / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_dict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return path

    def _read_jsonl(self, mission_id: str, filename: str, cls: type[_R]) -> list[_R]:
        path = self.mission_dir(mission_id) / filename
        if not path.exists():
            return []
        records: list[_R] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    records.append(from_dict(cls, json.loads(stripped)))
        return records

    def write_artifact(self, mission_id: str, relative_path: str, content: str) -> Path:
        path = resolve_within(self.mission_dir(mission_id), relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_result(self, mission_id: str, relative_path: str, data: dict) -> Path:
        path = resolve_within(self.mission_dir(mission_id), relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, data)
        return path


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())  # durably persist before the atomic replace
    tmp_path.replace(path)


def _safe_mission_id(mission_id: str) -> str:
    """Return a mission id that is safe to use as one run-directory segment."""
    clean = str(mission_id)
    if (
        not clean
        or clean != clean.strip()
        or clean in {".", ".."}
        or "/" in clean
        or "\\" in clean
        or Path(clean).name != clean
    ):
        raise ValueError("Mission id must be a single safe path segment.")
    return clean
