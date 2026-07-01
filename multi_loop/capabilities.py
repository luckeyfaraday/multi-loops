"""Searchable capability registry."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from typing import Any

from .models import Capability, SideEffectClass, Toolset


AvailabilityCheck = Callable[[], bool]

# Tokens that resolve to every registered capability.
_ALL_TOKENS = frozenset({"all", "*"})
_COMMAND_CHECK_CACHE: dict[tuple[str, ...], tuple[float, bool]] = {}


class CapabilityRegistry:
    """Registry for capability cards, availability checks, and toolsets."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._checks: dict[str, AvailabilityCheck] = {}
        self._toolsets: dict[str, Toolset] = {}

    def register(
        self,
        capability: Capability,
        check: AvailabilityCheck | None = None,
        *,
        override: bool = False,
    ) -> None:
        if capability.name in self._capabilities and not override:
            raise ValueError(f"Capability already registered: {capability.name}")
        self._capabilities[capability.name] = capability
        if check is not None:
            self._checks[capability.name] = check

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def require(self, name: str) -> Capability:
        capability = self.get(name)
        if capability is None:
            raise KeyError(f"Unknown capability: {name}")
        return capability

    def names(self) -> list[str]:
        return sorted(self._capabilities)

    def list(self) -> list[Capability]:
        return [self._capabilities[name] for name in self.names()]

    def available(self, name: str) -> bool:
        capability = self.require(name)
        if self.missing_env(name):
            return False
        check = self._checks.get(name)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:
            return False

    def missing_env(self, name: str) -> list[str]:
        """Return the capability's declared env vars that are not currently set."""
        capability = self.require(name)
        return [var for var in capability.requires_env if not os.environ.get(var)]

    def filter_available(self) -> list[Capability]:
        return [capability for capability in self.list() if self.available(capability.name)]

    def describe(self, name: str) -> dict[str, Any]:
        """Return a structured capability card for the search/describe bridge."""
        capability = self.require(name)
        return {
            "name": capability.name,
            "description": capability.description,
            "toolset_or_backend": capability.toolset_or_backend,
            "side_effect_class": capability.side_effect_class.value,
            "inputs": list(capability.inputs),
            "outputs": list(capability.outputs),
            "artifact_types": list(capability.artifact_types),
            "cost_class": capability.cost_class,
            "latency_class": capability.latency_class,
            "verification": capability.verification,
            "tags": list(capability.tags),
            "available": self.available(name),
            "requires_env": list(capability.requires_env),
            "missing_env": self.missing_env(name),
            "availability_check": capability.availability_check,
            "runner": capability.runner,
            "runner_command": capability.runner_command,
            "setup_hint": capability.setup_hint,
        }

    def search_cards(
        self,
        query: str,
        *,
        limit: int = 5,
        include_unavailable: bool = False,
    ) -> list[dict[str, Any]]:
        """Search and return structured cards instead of raw capabilities."""
        return [
            self.describe(capability.name)
            for capability in self.search(query, limit=limit, include_unavailable=include_unavailable)
        ]

    # -- Toolsets ---------------------------------------------------------

    def register_toolset(self, toolset: Toolset, *, override: bool = False) -> None:
        if toolset.name in self._toolsets and not override:
            raise ValueError(f"Toolset already registered: {toolset.name}")
        self._toolsets[toolset.name] = toolset

    def get_toolset(self, name: str) -> Toolset | None:
        return self._toolsets.get(name)

    def toolset_names(self) -> list[str]:
        return sorted(self._toolsets)

    def resolve_names(self, names: str | Iterable[str]) -> list[str]:
        """Resolve toolset names, capability names, and ``all``/``*`` to capabilities.

        Returns capability names in first-seen order with duplicates removed.
        Raises ``KeyError`` for a name that is neither a toolset nor a capability.
        Cyclic toolset includes are resolved safely.
        """
        if isinstance(names, str):
            names = [names]

        ordered: list[str] = []
        seen: set[str] = set()
        expanding: set[str] = set()

        def add_capability(capability_name: str) -> None:
            if capability_name not in seen:
                seen.add(capability_name)
                ordered.append(capability_name)

        def expand(name: str) -> None:
            if name in _ALL_TOKENS:
                for capability_name in self.names():
                    add_capability(capability_name)
            elif name in self._toolsets:
                if name in expanding:
                    return  # cyclic include; already being resolved
                expanding.add(name)
                toolset = self._toolsets[name]
                for member in (*toolset.includes, *toolset.capabilities):
                    expand(member)
                expanding.discard(name)
            elif name in self._capabilities:
                add_capability(name)
            else:
                raise KeyError(f"Unknown capability or toolset: {name}")

        for name in names:
            expand(name)
        return ordered

    def resolve(
        self,
        names: str | Iterable[str],
        *,
        include_unavailable: bool = True,
    ) -> list[Capability]:
        """Resolve names to capabilities, optionally dropping unavailable ones."""
        return [
            self._capabilities[name]
            for name in self.resolve_names(names)
            if include_unavailable or self.available(name)
        ]

    def describe_toolset(self, name: str) -> dict[str, Any]:
        toolset = self._toolsets.get(name)
        if toolset is None:
            raise KeyError(f"Unknown toolset: {name}")
        resolved = self.resolve_names(name)
        return {
            "name": toolset.name,
            "description": toolset.description,
            "capabilities": list(toolset.capabilities),
            "includes": list(toolset.includes),
            "resolved": resolved,
            "available": [name for name in resolved if self.available(name)],
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        include_unavailable: bool = False,
    ) -> list[Capability]:
        """Return capabilities matching a simple token-overlap search."""
        tokens = _tokens(query)
        if not tokens or limit <= 0:
            return []

        scored: list[tuple[int, str, Capability]] = []
        for capability in self.list():
            if not include_unavailable and not self.available(capability.name):
                continue
            haystack = _capability_search_text(capability)
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, capability.name, capability))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [capability for _, _, capability in scored[:limit]]


def default_capabilities() -> CapabilityRegistry:
    """Return the built-in MVP capability cards."""
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="agent_loop",
            description="Run a bounded goal loop with an agent backend.",
            toolset_or_backend="agent",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["goal", "success_criteria", "workspace"],
            outputs=["summary", "artifacts", "fitness_evidence"],
            artifact_types=["markdown", "files", "events"],
            cost_class="model_calls",
            latency_class="medium",
            verification="Review result artifacts and optional verification commands.",
            tags=["agent", "goal-loop", "worker"],
        )
    )
    registry.register(
        Capability(
            name="codex_oauth_runner",
            description=(
                "Run unattended candidate work through the installed Codex CLI using its "
                "existing ChatGPT OAuth session."
            ),
            toolset_or_backend="codex_cli",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["candidate prompt", "workspace", "Codex OAuth session"],
            outputs=["agent result", "artifacts", "execution transcript"],
            artifact_types=["markdown", "terminal transcript", "files"],
            availability_check="requires the codex CLI to be installed and logged in with ChatGPT",
            cost_class="chatgpt_subscription",
            latency_class="medium",
            verification="Require candidate evidence and configured verification commands.",
            tags=["codex", "oauth", "chatgpt", "agent", "scheduled", "runner"],
            runner="agent_command",
            runner_command="codex exec --sandbox workspace-write --skip-git-repo-check -",
            setup_hint="Run 'codex login' and complete ChatGPT sign-in.",
        ),
        check=_codex_oauth_available,
    )
    registry.register(
        Capability(
            name="hermes_runner",
            description=(
                "Run unattended candidate work through the installed Hermes CLI, one "
                "agent per subprocess (Stage 1 bridge)."
            ),
            toolset_or_backend="hermes_cli",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["candidate prompt", "workspace", "toolset grant"],
            outputs=["agent response", "artifacts", "execution transcript"],
            artifact_types=["markdown", "files"],
            availability_check="requires the hermes CLI on PATH with a configured model provider",
            cost_class="user_configured_provider",
            latency_class="medium",
            verification="Require candidate evidence and configured verification commands.",
            tags=["hermes", "agent", "scheduled", "runner", "oneshot", "subprocess"],
            runner="hermes",
            runner_command="hermes",
            setup_hint="Install Hermes and run 'hermes setup' to configure a model provider.",
        ),
        check=lambda: shutil.which("hermes") is not None,
    )
    registry.register(
        Capability(
            name="github_read",
            description="Inspect GitHub repositories, pull requests, diffs, checks, and metadata with gh.",
            toolset_or_backend="gh_cli",
            side_effect_class=SideEffectClass.READ_ONLY,
            inputs=["repository scope", "pull request"],
            outputs=["diff", "checks", "metadata", "URLs"],
            artifact_types=["json", "markdown", "URLs"],
            availability_check="requires the gh CLI and an authenticated GitHub session",
            cost_class="network_calls",
            latency_class="short",
            verification="Record repository, PR number, head SHA, and source URLs.",
            tags=["github", "gh", "pull request", "review", "checks", "repository"],
            setup_hint="Install gh, then run 'gh auth login'.",
        ),
        check=_github_cli_available,
    )
    registry.register(
        Capability(
            name="github_pr_comment",
            description="Post issue-style or inline review comments on GitHub pull requests with gh.",
            toolset_or_backend="gh_cli",
            side_effect_class=SideEffectClass.MESSAGE_PERSON,
            inputs=["repository", "pull request", "comment body", "explicit approval"],
            outputs=["comment URL", "comment ID", "delivery status"],
            artifact_types=["URL", "receipt", "markdown"],
            availability_check="requires the gh CLI, GitHub authentication, and scoped approval",
            cost_class="external_side_effect",
            latency_class="short",
            verification="Return the created comment URL or ID and verify it through gh api.",
            tags=["github", "gh", "pull request", "review", "comment", "message"],
            setup_hint="Install gh, run 'gh auth login', and approve PR-comment scope.",
        ),
        check=_github_cli_available,
    )
    registry.register(
        Capability(
            name="manual_task",
            description="Create a human-action task that blocks or checkpoints mission progress.",
            toolset_or_backend="human",
            side_effect_class=SideEffectClass.READ_ONLY,
            inputs=["instruction", "context"],
            outputs=["decision", "notes"],
            artifact_types=["markdown"],
            cost_class="human_time",
            latency_class="unknown",
            verification="Require explicit user response or approval record.",
            tags=["checkpoint", "approval", "human"],
        )
    )
    registry.register(
        Capability(
            name="shell_command",
            description="Run a bounded local shell command as a candidate loop.",
            toolset_or_backend="shell",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["command", "cwd", "timeout_seconds"],
            outputs=["stdout", "stderr", "exit_code", "artifact"],
            artifact_types=["markdown", "terminal transcript"],
            cost_class="local_compute",
            latency_class="short",
            verification="Check command exit code and captured output.",
            tags=["shell", "terminal", "local", "automation", "verification"],
        )
    )
    registry.register(
        Capability(
            name="agent_command",
            description="Run an external agent CLI with a self-contained candidate prompt on stdin.",
            toolset_or_backend="agent_command",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["command", "prompt", "cwd", "timeout_seconds"],
            outputs=["summary", "stdout", "stderr", "artifacts"],
            artifact_types=["markdown", "terminal transcript"],
            cost_class="external_agent_calls",
            latency_class="medium",
            verification="Require returned artifacts or follow-up verification commands.",
            tags=["agent", "cli", "claude", "codex", "opencode", "worker"],
        )
    )
    registry.register(
        Capability(
            name="scheduled_tick",
            description="Resume a mission on a schedule for one bounded generation step.",
            toolset_or_backend="scheduler",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["mission_id", "schedule"],
            outputs=["ledger_entry", "status_report"],
            artifact_types=["jsonl", "markdown"],
            cost_class="bounded_model_calls",
            latency_class="scheduled",
            verification="Check ledger append and next_run_at advancement.",
            tags=["cron", "schedule", "resume"],
        )
    )
    registry.register(
        Capability(
            name="web_research",
            description="Research markets, competitors, regulations, and source evidence on the web.",
            toolset_or_backend="future:web_or_mcp",
            side_effect_class=SideEffectClass.READ_ONLY,
            inputs=["query", "source constraints"],
            outputs=["sources", "evidence", "summary"],
            artifact_types=["markdown", "citations"],
            availability_check="requires a web-capable backend or MCP tool",
            cost_class="network_model_calls",
            latency_class="medium",
            verification="Record source URLs and quote/evidence snippets.",
            tags=["web", "research", "market", "company", "competitor", "evidence"],
        ),
        check=lambda: False,
    )
    registry.register(
        Capability(
            name="browser_automation",
            description="Use a browser to interact with websites, dashboards, forms, and web apps.",
            toolset_or_backend="future:browser",
            side_effect_class=SideEffectClass.EXTERNAL_WRITE,
            inputs=["url", "task", "approval policy"],
            outputs=["screenshots", "logs", "external handles"],
            artifact_types=["screenshots", "markdown", "logs"],
            availability_check="requires browser automation backend",
            cost_class="model_calls_plus_browser",
            latency_class="medium",
            verification="Return URLs, screenshots, or external object IDs for parent verification.",
            tags=["browser", "website", "dashboard", "forms", "automation"],
        ),
        check=lambda: False,
    )
    registry.register(
        Capability(
            name="media_generation",
            description="Generate creative assets such as images, audio, or video for campaigns/content.",
            toolset_or_backend="future:media_generation",
            side_effect_class=SideEffectClass.LOCAL_WRITE,
            inputs=["creative brief", "format", "style constraints"],
            outputs=["media artifacts", "metadata"],
            artifact_types=["image", "audio", "video", "markdown"],
            availability_check="requires media generation backend",
            cost_class="paid_or_metered_generation",
            latency_class="slow",
            verification="Store generated files and prompt/settings metadata.",
            tags=["image", "video", "audio", "creative", "campaign", "content"],
        ),
        check=lambda: False,
    )
    registry.register(
        Capability(
            name="public_messaging",
            description="Send messages or publish updates to people or public channels.",
            toolset_or_backend="future:messaging_or_social",
            side_effect_class=SideEffectClass.MESSAGE_PERSON,
            inputs=["message", "recipient", "approval"],
            outputs=["message id", "delivery status"],
            artifact_types=["receipt", "markdown"],
            availability_check="requires messaging/social backend and explicit approval",
            cost_class="external_side_effect",
            latency_class="short",
            verification="Return message IDs, URLs, or delivery receipts.",
            tags=["message", "publish", "social", "email", "outreach", "company"],
        ),
        check=lambda: False,
    )
    registry.register(
        Capability(
            name="paid_ads",
            description="Launch or modify paid advertising campaigns.",
            toolset_or_backend="future:ads",
            side_effect_class=SideEffectClass.SPEND_MONEY,
            inputs=["campaign", "budget", "approval"],
            outputs=["campaign id", "spend receipt", "metrics"],
            artifact_types=["receipt", "dashboard export", "markdown"],
            availability_check="requires ads backend, credentials, and spend approval",
            cost_class="spend_money",
            latency_class="scheduled",
            verification="Return campaign IDs, budget caps, and receipts.",
            tags=["ads", "campaign", "marketing", "company", "spend", "growth"],
        ),
        check=lambda: False,
    )
    for toolset in default_toolsets():
        registry.register_toolset(toolset)
    return registry


def default_toolsets() -> list[Toolset]:
    """Return the built-in capability bundles missions commonly request."""
    return [
        Toolset(
            name="local_workers",
            description="Local execution backends for running candidate loops.",
            capabilities=["agent_loop", "agent_command", "shell_command", "manual_task"],
        ),
        Toolset(
            name="research",
            description="Gather outside-world evidence and source material.",
            capabilities=["web_research", "browser_automation"],
        ),
        Toolset(
            name="media",
            description="Generate creative assets for campaigns and content.",
            capabilities=["media_generation"],
        ),
        Toolset(
            name="outreach",
            description="Message people, publish updates, and run paid distribution.",
            capabilities=["public_messaging", "paid_ads"],
        ),
        Toolset(
            name="scheduling",
            description="Resume long-running missions through bounded recurring steps.",
            capabilities=["scheduled_tick"],
        ),
        Toolset(
            name="github_review",
            description="Read GitHub pull requests and optionally post scoped review comments.",
            capabilities=["github_read", "github_pr_comment", "codex_oauth_runner"],
        ),
        Toolset(
            name="company",
            description="Capabilities a company-building mission typically draws on.",
            includes=["research", "outreach", "media"],
        ),
    ]


def _tokens(text: str) -> set[str]:
    return {token for token in text.lower().replace("_", " ").replace("-", " ").split() if token}


def _capability_search_text(capability: Capability) -> set[str]:
    parts: Iterable[str] = (
        capability.name,
        capability.description,
        capability.toolset_or_backend,
        capability.side_effect_class.value,
        capability.cost_class,
        capability.latency_class,
        capability.verification,
        " ".join(capability.inputs),
        " ".join(capability.outputs),
        " ".join(capability.artifact_types),
        " ".join(capability.tags),
    )
    return _tokens(" ".join(parts))


def _command_succeeds(command: list[str]) -> bool:
    key = tuple(command)
    cached = _COMMAND_CHECK_CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < 5:
        return cached[1]
    if shutil.which(command[0]) is None:
        _COMMAND_CHECK_CACHE[key] = (time.monotonic(), False)
        return False
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _COMMAND_CHECK_CACHE[key] = (time.monotonic(), False)
        return False
    available = completed.returncode == 0
    _COMMAND_CHECK_CACHE[key] = (time.monotonic(), available)
    return available


def _github_cli_available() -> bool:
    return _command_succeeds(["gh", "auth", "status"])


def _codex_oauth_available() -> bool:
    return _command_succeeds(["codex", "login", "status"])
