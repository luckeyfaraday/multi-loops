"""Deterministic verification command support."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class VerificationResult:
    command: str
    success: bool
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False


@dataclass(slots=True)
class VerificationReport:
    results: list[VerificationResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(result.success for result in self.results)


def run_verification(
    commands: list[str],
    *,
    cwd: str | Path,
    timeout_seconds: float | None = None,
) -> VerificationReport:
    """Run verification commands sequentially and collect structured results."""
    results: list[VerificationResult] = []
    for command in commands:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                VerificationResult(
                    command=command,
                    success=False,
                    exit_code=None,
                    stdout=_coerce_output(exc.stdout),
                    stderr=_coerce_output(exc.stderr),
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                )
            )
            continue

        results.append(
            VerificationResult(
                command=command,
                success=completed.returncode == 0,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=time.monotonic() - started,
            )
        )
    return VerificationReport(results=results)


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
