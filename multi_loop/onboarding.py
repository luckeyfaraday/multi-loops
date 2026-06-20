"""Mission onboarding and capability recommendation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .capabilities import CapabilityRegistry, default_capabilities
from .models import Capability, SideEffectClass


@dataclass(slots=True)
class OnboardingQuestion:
    key: str
    prompt: str
    default: str = ""
    required: bool = False
    help_text: str = ""


@dataclass(slots=True)
class CapabilityRecommendation:
    name: str
    description: str
    reason: str
    available: bool
    side_effect_class: SideEffectClass
    approval_required: bool
    setup_note: str | None = None


@dataclass(slots=True)
class OnboardingPlan:
    mission_statement: str
    success_criteria: str
    clarifications: dict[str, str]
    recommended_capabilities: list[CapabilityRecommendation] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def configured_capabilities(self) -> list[CapabilityRecommendation]:
        return [item for item in self.recommended_capabilities if item.available]

    @property
    def setup_required_capabilities(self) -> list[CapabilityRecommendation]:
        return [item for item in self.recommended_capabilities if not item.available]


class OnboardingEngine:
    """Build the mission intake dialogue and capability recommendations."""

    def __init__(self, capabilities: CapabilityRegistry | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    def questions(self, mission_statement: str = "") -> list[OnboardingQuestion]:
        return [
            OnboardingQuestion(
                key="mission_statement",
                prompt="What mission should multi-loop run?",
                default=mission_statement,
                required=True,
                help_text="Example: Run a company, launch a product, produce a video essay.",
            ),
            OnboardingQuestion(
                key="success_criteria",
                prompt="What would count as success?",
                default="Make measurable progress and produce durable artifacts.",
                required=True,
            ),
            OnboardingQuestion(
                key="time_horizon",
                prompt="What time horizon should the mission optimize for?",
                default="first useful generation now; longer mission can continue by scheduled ticks",
            ),
            OnboardingQuestion(
                key="constraints",
                prompt="What constraints should the orchestrator respect?",
                default="ask before external side effects, spending, or public publishing",
            ),
            OnboardingQuestion(
                key="resources",
                prompt="What resources, accounts, tools, or workspace paths are available?",
                default="local workspace and configured command-line tools only",
            ),
            OnboardingQuestion(
                key="autonomy_level",
                prompt="How autonomous should it be?",
                default="draft plans and local artifacts autonomously; ask before external actions",
            ),
            OnboardingQuestion(
                key="approval_policy",
                prompt="Which actions require approval?",
                default="messaging people, publishing, paid ads, spending money, credentialed external writes",
            ),
            OnboardingQuestion(
                key="schedule",
                prompt="Should this mission recur on a schedule?",
                default="no schedule yet",
            ),
            OnboardingQuestion(
                key="preferred_tools",
                prompt="Any specific tools or agents to use?",
                default="mock",
            ),
        ]

    def build_plan(self, answers: dict[str, str]) -> OnboardingPlan:
        mission_statement = _required(answers, "mission_statement")
        success_criteria = _required(answers, "success_criteria")
        clarifications = {
            key: value.strip()
            for key, value in answers.items()
            if key not in {"mission_statement", "success_criteria"} and value.strip()
        }
        recommendations = self.recommend_capabilities(mission_statement, clarifications)
        return OnboardingPlan(
            mission_statement=mission_statement,
            success_criteria=success_criteria,
            clarifications=clarifications,
            recommended_capabilities=recommendations,
            next_steps=_next_steps(recommendations),
        )

    def default_answers(self, mission_statement: str = "") -> dict[str, str]:
        return {question.key: question.default for question in self.questions(mission_statement)}

    def recommend_capabilities(
        self,
        mission_statement: str,
        clarifications: dict[str, str] | None = None,
    ) -> list[CapabilityRecommendation]:
        text = " ".join([mission_statement, *(clarifications or {}).values()]).lower()
        names = ["agent_loop", "manual_task", "shell_command", "agent_command"]

        if _matches(text, "company", "startup", "business", "market", "customer", "competitor", "research"):
            names.extend(["web_research", "scheduled_tick"])
        if _matches(text, "campaign", "ad", "ads", "growth", "launch", "marketing"):
            names.extend(["paid_ads", "public_messaging", "media_generation"])
        if _matches(text, "website", "dashboard", "browser", "form", "web app", "saas"):
            names.append("browser_automation")
        if _matches(text, "video", "youtube", "image", "thumbnail", "audio", "creative", "content"):
            names.extend(["media_generation", "web_research"])
        if _matches(text, "daily", "weekly", "monitor", "recurring", "schedule", "long-running", "company"):
            names.append("scheduled_tick")

        recommendations: list[CapabilityRecommendation] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            capability = self.capabilities.get(name)
            if capability is None:
                continue
            recommendations.append(self._recommendation(capability, text))
        return recommendations

    def _recommendation(self, capability: Capability, context_text: str) -> CapabilityRecommendation:
        available = self.capabilities.available(capability.name)
        approval_required = capability.side_effect_class not in {
            SideEffectClass.READ_ONLY,
            SideEffectClass.LOCAL_WRITE,
        }
        setup_note = None if available else capability.availability_check or "requires setup"
        return CapabilityRecommendation(
            name=capability.name,
            description=capability.description,
            reason=_reason_for(capability, context_text),
            available=available,
            side_effect_class=capability.side_effect_class,
            approval_required=approval_required,
            setup_note=setup_note,
        )


def collect_answers(
    questions: list[OnboardingQuestion],
    *,
    input_func: Callable[[str], str] = input,
) -> dict[str, str]:
    """Collect answers from an interactive prompt."""
    answers: dict[str, str] = {}
    for question in questions:
        suffix = f" [{question.default}]" if question.default else ""
        raw = input_func(f"{question.prompt}{suffix}: ").strip()
        answer = raw or question.default
        if question.required and not answer:
            raise ValueError(f"Required onboarding answer missing: {question.key}")
        answers[question.key] = answer
    return answers


def format_capability_brief(plan: OnboardingPlan) -> str:
    """Return a user-facing explanation of relevant capabilities."""
    lines = ["Relevant capabilities for this mission:", ""]
    for item in plan.recommended_capabilities:
        status = "configured" if item.available else "needs setup"
        approval = "; approval required" if item.approval_required else ""
        line = f"- {item.name} ({status}{approval}): {item.reason}"
        if item.setup_note:
            line += f" Setup: {item.setup_note}."
        lines.append(line)
    return "\n".join(lines)


def _required(answers: dict[str, str], key: str) -> str:
    value = answers.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required onboarding answer: {key}")
    return value


def _matches(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _reason_for(capability: Capability, context_text: str) -> str:
    if capability.name == "agent_loop":
        return "core execution unit for candidate loops"
    if capability.name == "manual_task":
        return "captures user checkpoints and approvals during broad missions"
    if capability.name == "shell_command":
        return "runs local scripts, tests, and deterministic checks"
    if capability.name == "agent_command":
        return "lets a configured CLI agent execute candidate loops"
    if capability.name == "scheduled_tick":
        return "keeps long-running missions moving through bounded recurring steps"
    if capability.name == "web_research":
        return "business/company missions need outside evidence and market research"
    if capability.name == "browser_automation":
        return "useful when the mission touches websites, dashboards, or web forms"
    if capability.name == "media_generation":
        return "useful for campaign, content, image, audio, or video assets"
    if capability.name == "public_messaging":
        return "useful for outreach or publishing, but it affects real people"
    if capability.name == "paid_ads":
        return "useful for ad experiments, but it spends money and needs approval"
    return capability.description.lower() if context_text else capability.description


def _next_steps(recommendations: list[CapabilityRecommendation]) -> list[str]:
    steps = [
        "Create the mission with the captured clarifications.",
        "Run one dry generation with configured local capabilities.",
    ]
    if any(not item.available for item in recommendations):
        steps.append("Configure missing capabilities before assigning loops that depend on them.")
    if any(item.approval_required for item in recommendations):
        steps.append("Record approval rules before external side effects, publishing, messaging, or spend.")
    return steps
