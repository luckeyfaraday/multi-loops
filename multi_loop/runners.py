"""Runner abstractions for candidate loops."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import Artifact, CandidateLoop, Mission, to_dict, utc_now_iso
from .verification import VerificationResult


@dataclass(slots=True)
class RunRequest:
    mission: Mission
    generation_index: int
    candidate: CandidateLoop
    mission_dir: Path
    workspace: Path | None = None
    # Safety directive constraining a spawned agent's outward actions; injected
    # into the agent prompt by the orchestrator (see policy.side_effect_directive).
    safety_directive: str = ""


@dataclass(slots=True)
class RunResult:
    candidate_loop_id: str
    success: bool
    summary: str
    output: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    verification: list[VerificationResult] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)


class Runner(Protocol):
    name: str

    def run(self, request: RunRequest) -> RunResult:
        """Run one candidate loop."""


class RunnerRegistry:
    """Map runner names to runner implementations."""

    def __init__(self, runners: list[Runner] | None = None) -> None:
        self._runners: dict[str, Runner] = {}
        for runner in runners or []:
            self.register(runner)

    def register(self, runner: Runner, *, override: bool = False) -> None:
        if runner.name in self._runners and not override:
            raise ValueError(f"Runner already registered: {runner.name}")
        self._runners[runner.name] = runner

    def require(self, name: str) -> Runner:
        try:
            return self._runners[name]
        except KeyError as exc:
            raise KeyError(f"Unknown runner: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._runners)


class MockRunner:
    """Deterministic runner used for tests and offline demos."""

    name = "mock"

    def run(self, request: RunRequest) -> RunResult:
        candidate = request.candidate
        started_at = utc_now_iso()
        relative_path = f"artifacts/generation-{request.generation_index}/{candidate.id}.md"
        artifact_path = request.mission_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"# Candidate Loop Result\n\n"
            f"Mission: {request.mission.statement}\n\n"
            f"Goal: {candidate.goal}\n\n"
            f"Success Criteria: {candidate.success_criteria}\n\n"
            "Mock runner completed this candidate deterministically.\n"
        )
        artifact_path.write_text(content, encoding="utf-8")
        return RunResult(
            candidate_loop_id=candidate.id,
            success=True,
            summary=f"Mock completed: {candidate.goal}",
            output=content,
            artifacts=[Artifact(path=relative_path, kind="markdown", description="Mock candidate result")],
            metadata={"runner": self.name},
            started_at=started_at,
            finished_at=utc_now_iso(),
        )


class ShellRunner:
    """Run a configured shell command as a candidate loop."""

    name = "shell"

    def run(self, request: RunRequest) -> RunResult:
        candidate = request.candidate
        started_at = utc_now_iso()
        command = str(candidate.runner_config.get("command") or "").strip()
        if not command:
            return RunResult(
                candidate_loop_id=candidate.id,
                success=False,
                summary="Shell runner requires runner_config.command.",
                metadata={"runner": self.name},
                started_at=started_at,
                finished_at=utc_now_iso(),
            )

        cwd = _resolve_cwd(request)
        timeout = _resolve_timeout(candidate)
        monotonic_start = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
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

        output = _format_shell_output(command, exit_code, stdout, stderr, timed_out)
        relative_path = f"artifacts/generation-{request.generation_index}/{candidate.id}-shell.md"
        artifact_path = request.mission_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(output, encoding="utf-8")
        success = bool(exit_code == 0 and not timed_out)
        return RunResult(
            candidate_loop_id=candidate.id,
            success=success,
            summary=(
                f"Shell command succeeded: {command}"
                if success
                else f"Shell command failed: {command}"
            ),
            output=output,
            artifacts=[Artifact(path=relative_path, kind="markdown", description="Shell command transcript")],
            metadata={
                "runner": self.name,
                "command": command,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "duration_seconds": time.monotonic() - monotonic_start,
            },
            started_at=started_at,
            finished_at=utc_now_iso(),
        )


class AgentCommandRunner:
    """Run an external agent CLI command with a self-contained prompt on stdin."""

    name = "agent_command"

    def run(self, request: RunRequest) -> RunResult:
        candidate = request.candidate
        started_at = utc_now_iso()
        command = candidate.runner_config.get("command")
        if not command:
            return RunResult(
                candidate_loop_id=candidate.id,
                success=False,
                summary="Agent command runner requires runner_config.command.",
                metadata={"runner": self.name},
                started_at=started_at,
                finished_at=utc_now_iso(),
            )

        prompt = _agent_prompt(request)
        cwd = _resolve_cwd(request)
        timeout = _resolve_timeout(candidate)
        shell = isinstance(command, str)
        try:
            completed = subprocess.run(
                command,
                shell=shell,
                cwd=str(cwd),
                input=prompt,
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

        output = _format_shell_output(str(command), exit_code, stdout, stderr, timed_out)
        relative_path = f"artifacts/generation-{request.generation_index}/{candidate.id}-agent.md"
        artifact_path = request.mission_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(output, encoding="utf-8")
        success = bool(exit_code == 0 and not timed_out)
        return RunResult(
            candidate_loop_id=candidate.id,
            success=success,
            summary=(stdout.strip().splitlines()[-1] if stdout.strip() else output.splitlines()[0]),
            output=output,
            artifacts=[Artifact(path=relative_path, kind="markdown", description="Agent command transcript")],
            metadata={"runner": self.name, "command": command, "exit_code": exit_code, "timed_out": timed_out},
            started_at=started_at,
            finished_at=utc_now_iso(),
        )


def default_runner_registry() -> RunnerRegistry:
    return RunnerRegistry([MockRunner(), ShellRunner(), AgentCommandRunner()])


def _resolve_cwd(request: RunRequest) -> Path:
    configured = request.candidate.runner_config.get("cwd")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    if request.workspace is not None:
        return request.workspace
    return request.mission_dir


def _resolve_timeout(candidate: CandidateLoop) -> float | None:
    configured = candidate.runner_config.get("timeout_seconds")
    if configured is not None:
        return float(configured)
    return candidate.budget.max_seconds


def _format_shell_output(command: str, exit_code: int | None, stdout: str, stderr: str, timed_out: bool) -> str:
    status = "timed out" if timed_out else f"exit code {exit_code}"
    return (
        f"# Command Result\n\n"
        f"Command: `{command}`\n\n"
        f"Status: {status}\n\n"
        f"## Stdout\n\n```text\n{stdout}\n```\n\n"
        f"## Stderr\n\n```text\n{stderr}\n```\n"
    )


def _agent_prompt(request: RunRequest) -> str:
    candidate = request.candidate
    safety = f"{request.safety_directive}\n\n" if request.safety_directive else ""
    return (
        "You are running one candidate loop inside a multi-loop mission.\n\n"
        f"{safety}"
        f"Mission: {request.mission.statement}\n"
        f"Mission success criteria: {request.mission.success_criteria}\n\n"
        f"Candidate goal: {candidate.goal}\n"
        f"Candidate success criteria: {candidate.success_criteria}\n\n"
        "Return a concise summary, artifacts created, verification performed, and any blockers.\n"
    )


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_result_to_dict(result: RunResult) -> dict[str, object]:
    return to_dict(result)
