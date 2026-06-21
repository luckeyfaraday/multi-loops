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
from .leases import MissionBusy, MissionLease, acquire_mission_lease
from .orchestrator import GenerationRunResult, MissionOrchestrator, ScheduleNotConfigured
from .planning import FitnessReviewer, HeuristicPortfolioPlanner, PortfolioPlan
from .policy import PathEscape, prepare_candidate, resolve_within
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
    "MissionBusy",
    "MissionLease",
    "MissionOrchestrator",
    "MissionSchedule",
    "MissionStore",
    "PathEscape",
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
    "acquire_mission_lease",
    "collect_answers",
    "compute_next_run",
    "default_capabilities",
    "default_runner_registry",
    "format_capability_brief",
    "parse_schedule",
    "resolve_within",
    "run_verification",
]
