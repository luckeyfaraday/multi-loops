"""Runner abstractions for candidate loops."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .hermes_runtime import HermesRuntimeAdapter, _coerce_output
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
    # Lessons from earlier failures in this mission, injected into the prompt so a
    # spawned agent avoids the same pitfalls (see planning.collect_pitfalls).
    pitfalls: list[str] = field(default_factory=list)


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


class HermesRunner:
    """Run a candidate through the Hermes CLI (Stage 1 subprocess bridge).

    The runner owns mission-side concerns — prompt composition, artifact
    placement, transcript, RunResult shape — and delegates the subprocess
    contract to HermesRuntimeAdapter. Collected artifacts are whatever files
    the agent actually wrote into its artifact directory, not what it claims.
    """

    name = "hermes"

    def __init__(self, adapter: HermesRuntimeAdapter | None = None) -> None:
        self.adapter = adapter or HermesRuntimeAdapter()

    def run(self, request: RunRequest) -> RunResult:
        candidate = request.candidate
        started_at = utc_now_iso()
        config = candidate.runner_config
        adapter = self.adapter
        executable = str(config.get("executable") or "").strip()
        if executable and executable != adapter.executable:
            adapter = HermesRuntimeAdapter(
                executable,
                default_toolsets=self.adapter.default_toolsets,
                default_timeout_seconds=self.adapter.default_timeout_seconds,
            )

        toolsets = _configured_toolsets(config.get("toolsets"))
        artifact_relative = f"artifacts/generation-{request.generation_index}/{candidate.id}"
        outcome = adapter.run_agent(
            _mission_prompt(request),
            toolsets=toolsets,
            workspace=_resolve_cwd(request),
            permissions=request.safety_directive,
            model=str(config.get("model")) if config.get("model") else None,
            timeout_seconds=_resolve_timeout(candidate),
            artifact_dir=request.mission_dir / artifact_relative,
        )

        artifacts = [
            Artifact(
                path=str(path.relative_to(request.mission_dir.resolve())),
                kind="file",
                description="File written by the Hermes agent",
            )
            for path in adapter.collect_artifacts(outcome.run_id)
        ]
        status = "timed out" if outcome.timed_out else f"exit code {outcome.exit_code}"
        transcript = (
            f"# Hermes Run\n\n"
            f"Session: {outcome.session_id or 'unknown'}\n\n"
            f"Status: {status}\n\n"
            f"## Response\n\n{outcome.response}\n\n"
            f"## Stderr\n\n```text\n{outcome.stderr}\n```\n"
        )
        transcript_relative = (
            f"artifacts/generation-{request.generation_index}/{candidate.id}-hermes.md"
        )
        transcript_path = request.mission_dir / transcript_relative
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(transcript, encoding="utf-8")
        artifacts.append(
            Artifact(path=transcript_relative, kind="markdown", description="Hermes run transcript")
        )

        if outcome.response:
            summary = outcome.response.strip().splitlines()[-1]
        else:
            summary = f"Hermes run produced no response ({status})."
        return RunResult(
            candidate_loop_id=candidate.id,
            success=outcome.success,
            summary=summary,
            output=outcome.response,
            artifacts=artifacts,
            metadata={
                "runner": self.name,
                "executable": adapter.executable,
                "session_id": outcome.session_id,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "duration_seconds": outcome.duration_seconds,
                "toolsets": toolsets or adapter.default_toolsets,
                "artifact_dir": outcome.artifact_dir,
            },
            started_at=started_at,
            finished_at=utc_now_iso(),
        )


def default_runner_registry() -> RunnerRegistry:
    return RunnerRegistry([MockRunner(), ShellRunner(), AgentCommandRunner(), HermesRunner()])


def _configured_toolsets(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        names = [name.strip() for name in value.split(",")]
    else:
        names = [str(name).strip() for name in value]
    return [name for name in names if name] or None


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
    safety = f"{request.safety_directive}\n\n" if request.safety_directive else ""
    return f"{safety}{_mission_prompt(request)}"


def _mission_prompt(request: RunRequest) -> str:
    """The candidate prompt without the safety directive.

    The Hermes bridge carries the directive through its ``permissions``
    parameter so it always leads the final prompt; other runners fold it in
    via ``_agent_prompt``.
    """
    candidate = request.candidate
    pitfalls = ""
    if request.pitfalls:
        bullets = "\n".join(f"- {pitfall}" for pitfall in request.pitfalls)
        pitfalls = (
            "Known pitfalls from earlier mission runs (avoid repeating them):\n"
            f"{bullets}\n\n"
        )
    return (
        "You are running one candidate loop inside a multi-loop mission.\n\n"
        f"{pitfalls}"
        f"Mission: {request.mission.statement}\n"
        f"Mission success criteria: {request.mission.success_criteria}\n\n"
        f"Candidate goal: {candidate.goal}\n"
        f"Candidate success criteria: {candidate.success_criteria}\n\n"
        "Return a concise summary, artifacts created, verification performed, and any blockers.\n"
    )


def run_result_to_dict(result: RunResult) -> dict[str, object]:
    return to_dict(result)
