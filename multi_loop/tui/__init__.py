"""Multi-loop's own agent environment: dashboard, operator chat, settings.

The TUI owns the experience; the LLM engine (codex today) is swappable
inference behind it. Context is assembled by the app on every turn, so the
operator always already knows the mission state — the user never points the
agent at files.
"""

from .engine import CodexOperatorEngine, OperatorReply

__all__ = ["CodexOperatorEngine", "OperatorReply"]
