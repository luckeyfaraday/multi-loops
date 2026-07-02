"""Headless codex driver for the operator chat.

Each turn shells out to ``codex exec --json`` (resuming one persistent codex
thread), parses the JSONL event stream, and returns the agent's message. The
multi-loop MCP server registered in codex gives the engine its mission tools;
the TUI injects a fresh state snapshot every turn so the operator never has to
be told where to look.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TURN_TIMEOUT_SECONDS = 300.0

OPERATOR_PREAMBLE = """You are the mission operator inside multi-loop's own console. The user is
laid back: they state missions and make approval decisions; you own everything
else through the multi-loop MCP tools (always pass root='{root}').

Rules:
- Speak like a chief of staff: short, concrete, evidence-backed. Never dump raw JSON.
- A state snapshot is injected before every user message; trust it and act, do not
  re-ask for information it already contains.
- Use mission_report (not raw status) when summarizing progress for the user.
- External side effects require the user's explicit yes in this chat before you
  record approve_capability. Never grant authority on your own.
- When something is blocked, say exactly what decision or setup would unblock it."""


@dataclass(slots=True)
class OperatorReply:
    """One operator turn: the message plus the evidence of how it happened."""

    text: str
    thread_id: str | None
    ok: bool
    error: str = ""
    usage: dict = field(default_factory=dict)


class CodexOperatorEngine:
    """Persistent codex-backed operator conversation."""

    def __init__(
        self,
        root: Path,
        *,
        executable: str = "codex",
        timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    ) -> None:
        self.root = Path(root).resolve()
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.thread_id: str | None = None

    @property
    def started(self) -> bool:
        return self.thread_id is not None

    def turn(self, user_message: str, *, snapshot: str = "") -> OperatorReply:
        """Send one user message (with injected state snapshot) to the operator."""
        sections: list[str] = []
        if not self.started:
            sections.append(OPERATOR_PREAMBLE.format(root=self.root / ".multi-loop"))
        if snapshot:
            sections.append(f"[state snapshot — not written by the user]\n{snapshot}")
        sections.append(user_message)
        prompt = "\n\n".join(sections)

        # `codex exec resume` accepts only a subset of `codex exec` flags (no
        # --cd), so the working directory is set on the subprocess instead.
        command = [self.executable, "exec"]
        if self.thread_id:
            command += ["resume", self.thread_id]
        command += ["--json", "--skip-git-repo-check", prompt]

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.root),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return OperatorReply(
                text="", thread_id=self.thread_id, ok=False,
                error=f"operator engine timed out after {self.timeout_seconds:.0f}s",
            )
        except FileNotFoundError:
            return OperatorReply(
                text="", thread_id=self.thread_id, ok=False,
                error=f"codex executable not found: {self.executable}",
            )

        text, usage = self._parse_events(completed.stdout)
        if completed.returncode != 0 and not text:
            return OperatorReply(
                text="", thread_id=self.thread_id, ok=False,
                error=_stderr_reason(completed.stderr, completed.returncode),
            )
        return OperatorReply(text=text, thread_id=self.thread_id, ok=True, usage=usage)

    def _parse_events(self, stdout: str) -> tuple[str, dict]:
        messages: list[str] = []
        usage: dict = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                self.thread_id = str(event["thread_id"])
            elif event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    messages.append(str(item["text"]))
            elif event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
        return "\n\n".join(messages), usage


def _stderr_reason(stderr: str, returncode: int | None) -> str:
    """Pick the informative line out of CLI stderr (clap buries it above usage)."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    for line in lines:
        if "error" in line.lower():
            return line
    return lines[0] if lines else f"codex exited {returncode}"
