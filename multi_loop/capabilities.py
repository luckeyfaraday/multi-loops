"""Searchable capability registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .models import Capability, SideEffectClass


AvailabilityCheck = Callable[[], bool]


class CapabilityRegistry:
    """Registry for capability cards and lightweight availability checks."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._checks: dict[str, AvailabilityCheck] = {}

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
        self.require(name)
        check = self._checks.get(name)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:
            return False

    def filter_available(self) -> list[Capability]:
        return [capability for capability in self.list() if self.available(capability.name)]

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
    return registry


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
