"""Stage 1 Hermes subprocess bridge.

The direction doc locks the Stage 1 adapter contract to exactly two methods:
``run_agent`` and ``collect_artifacts``. Hermes stays process-isolated behind
its CLI; multi-loop keeps ownership of the mission, permissions, ledger, and
evidence. Further methods (``schedule_job``, ``spawn_subagents``,
``interrupt``, ...) are added only when the stage that needs them arrives.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import new_id, utc_now_iso

# Toolsets granted to a worker when the candidate does not choose its own.
# Matches the default permission posture: read the web, write local files.
DEFAULT_TOOLSETS: tuple[str, ...] = ("web", "file")

DEFAULT_TIMEOUT_SECONDS = 900.0

# `hermes chat --pass-session-id` reports the session on stderr as
# "session_id: <id>" while quiet mode keeps stdout response-only.
_SESSION_ID_PATTERN = re.compile(r"^session_id:\s*(\S+)", re.MULTILINE)

_ARTIFACT_INSTRUCTION = (
    "Write every file you produce under this directory; it is the only place "
    "the mission collects evidence from: {directory}"
)


@dataclass(slots=True)
class HermesRunOutcome:
    """What one Hermes subprocess run actually did."""

    run_id: str
    command: list[str]
    exit_code: int | None
    timed_out: bool
    response: str
    stderr: str
    duration_seconds: float
    started_at: str
    finished_at: str
    session_id: str | None = None
    artifact_dir: str | None = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class HermesRuntimeAdapter:
    """Run one Hermes agent per subprocess (``hermes chat -q ... -Q``).

    Quiet mode keeps stdout response-only and ``--pass-session-id`` reports the
    Hermes session on stderr, so the bridge needs no Hermes imports. Artifacts
    are exchanged through a directory contract: the caller passes
    ``artifact_dir``, the prompt tells the agent to write files there, and
    ``collect_artifacts`` returns what actually exists on disk — evidence, not
    self-report.
    """

    def __init__(
        self,
        executable: str = "hermes",
        *,
        default_toolsets: Sequence[str] = DEFAULT_TOOLSETS,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.executable = executable
        self.default_toolsets = list(default_toolsets)
        self.default_timeout_seconds = default_timeout_seconds
        self._artifact_dirs: dict[str, Path] = {}

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def run_agent(
        self,
        prompt: str,
        *,
        toolsets: Sequence[str] | None = None,
        workspace: Path | None = None,
        permissions: str = "",
        model: str | None = None,
        timeout_seconds: float | None = None,
        artifact_dir: Path | None = None,
    ) -> HermesRunOutcome:
        """Run one prompt through Hermes and return what happened.

        ``permissions`` is the side-effect directive that bounds the agent's
        outward actions; it leads the prompt so it cannot be buried by task
        detail.
        """
        run_id = new_id("hermes")
        started_at = utc_now_iso()

        sections = [permissions.strip(), prompt.strip()]
        if artifact_dir is not None:
            artifact_dir = artifact_dir.resolve()
            artifact_dir.mkdir(parents=True, exist_ok=True)
            self._artifact_dirs[run_id] = artifact_dir
            sections.append(_ARTIFACT_INSTRUCTION.format(directory=artifact_dir))
        full_prompt = "\n\n".join(section for section in sections if section)

        command = [
            self.executable,
            "chat",
            "-q",
            full_prompt,
            "-Q",
            "--pass-session-id",
            "-t",
            ",".join(toolsets or self.default_toolsets),
        ]
        if model:
            command.extend(["-m", model])

        timeout = (
            timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        )
        monotonic_start = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace) if workspace is not None else None,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            timed_out = False
            exit_code: int | None = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = _coerce_output(exc.stdout)
            stderr = _coerce_output(exc.stderr)
        except FileNotFoundError:
            timed_out = False
            exit_code = 127
            stdout = ""
            stderr = f"hermes executable not found: {self.executable}"

        session_match = _SESSION_ID_PATTERN.search(stderr)
        return HermesRunOutcome(
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            timed_out=timed_out,
            response=stdout.strip(),
            stderr=stderr,
            duration_seconds=time.monotonic() - monotonic_start,
            started_at=started_at,
            finished_at=utc_now_iso(),
            session_id=session_match.group(1) if session_match else None,
            artifact_dir=str(artifact_dir) if artifact_dir is not None else None,
        )

    def collect_artifacts(self, run_id: str) -> list[Path]:
        """Return the files that actually exist in the run's artifact directory.

        Consuming the run's entry keeps the adapter bounded in long-lived
        processes (one shared adapter serves every scheduled generation).
        """
        directory = self._artifact_dirs.pop(run_id, None)
        if directory is None or not directory.exists():
            return []
        return sorted(path for path in directory.rglob("*") if path.is_file())


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
