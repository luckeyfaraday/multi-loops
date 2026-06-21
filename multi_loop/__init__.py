"""Core primitives for multi-loop mission orchestration."""

from .capabilities import CapabilityRegistry, default_capabilities
from .models import (
    Artifact,
    Budget,
    CandidateLoop,
    CandidateState,
    Capability,
    CapabilityRef,
    Event,
    FitnessScore,
    Generation,
    LedgerEntry,
    Mission,
    MissionSchedule,
    PolicyGate,
    ScheduleState,
    SideEffectClass,
)
from .orchestrator import GenerationRunResult, MissionOrchestrator, ScheduleNotConfigured
from .planning import FitnessReviewer, HeuristicPortfolioPlanner, PortfolioPlan, prepare_candidate
from .scheduler import MissionScheduler, SchedulerTickReport, TickResult
from .onboarding import (
    CapabilityRecommendation,
    OnboardingEngine,
    OnboardingPlan,
    OnboardingQuestion,
    collect_answers,
    format_capability_brief,
)
from .runners import (
    AgentCommandRunner,
    MockRunner,
    RunRequest,
    RunResult,
    RunnerRegistry,
    ShellRunner,
    default_runner_registry,
)
from .schedule_util import compute_next_run, parse_schedule
from .storage import MissionStore
from .verification import VerificationReport, VerificationResult, run_verification

__all__ = [
    "Artifact",
    "Budget",
    "CandidateLoop",
    "CandidateState",
    "Capability",
    "CapabilityRef",
    "CapabilityRegistry",
    "Event",
    "FitnessScore",
    "FitnessReviewer",
    "Generation",
    "GenerationRunResult",
    "HeuristicPortfolioPlanner",
    "MissionScheduler",
    "PortfolioPlan",
    "LedgerEntry",
    "Mission",
    "MissionOrchestrator",
    "MissionSchedule",
    "MissionStore",
    "ScheduleNotConfigured",
    "ScheduleState",
    "SchedulerTickReport",
    "TickResult",
    "prepare_candidate",
    "CapabilityRecommendation",
    "OnboardingEngine",
    "OnboardingPlan",
    "OnboardingQuestion",
    "PolicyGate",
    "AgentCommandRunner",
    "MockRunner",
    "RunRequest",
    "RunResult",
    "RunnerRegistry",
    "ShellRunner",
    "SideEffectClass",
    "VerificationReport",
    "VerificationResult",
    "collect_answers",
    "compute_next_run",
    "default_capabilities",
    "default_runner_registry",
    "format_capability_brief",
    "parse_schedule",
    "run_verification",
]
