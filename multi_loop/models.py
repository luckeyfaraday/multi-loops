"""Serializable mission orchestration data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from types import UnionType
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import uuid4


def new_id(prefix: str) -> str:
    """Return a compact, readable identifier."""
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class SideEffectClass(str, Enum):
    """How far outside the local mission workspace a capability can reach."""

    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    PUBLIC_PUBLISH = "public_publish"
    SPEND_MONEY = "spend_money"
    MESSAGE_PERSON = "message_person"


class CandidateState(str, Enum):
    """Lifecycle state for a candidate loop."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISCARDED = "discarded"


@dataclass(slots=True)
class Budget:
    max_iterations: int | None = None
    max_seconds: float | None = None
    max_cost_usd: float | None = None
    max_tokens: int | None = None


@dataclass(slots=True)
class Artifact:
    path: str
    kind: str = "file"
    description: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class Capability:
    name: str
    description: str
    toolset_or_backend: str
    side_effect_class: SideEffectClass = SideEffectClass.READ_ONLY
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    availability_check: str | None = None
    cost_class: str = "unknown"
    latency_class: str = "unknown"
    verification: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CapabilityRef:
    name: str
    required: bool = True
    reason: str = ""


@dataclass(slots=True)
class PolicyGate:
    capability: str
    side_effect_class: SideEffectClass
    requires_approval: bool = True
    approved_by: str | None = None
    approved_at: str | None = None
    evidence: str | None = None


@dataclass(slots=True)
class FitnessScore:
    candidate_loop_id: str
    score: float
    rationale: str
    rubric: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class CandidateLoop:
    goal: str
    success_criteria: str
    id: str = field(default_factory=lambda: new_id("loop"))
    parent_ids: list[str] = field(default_factory=list)
    role: str = "worker"
    runner: str = "mock"
    runner_config: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    required_capabilities: list[CapabilityRef] = field(default_factory=list)
    policy_gates: list[PolicyGate] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    state: CandidateState = CandidateState.PLANNED
    result: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    fitness: FitnessScore | None = None


@dataclass(slots=True)
class Generation:
    index: int
    candidate_loops: list[CandidateLoop] = field(default_factory=list)
    fitness_scores: list[FitnessScore] = field(default_factory=list)
    selected_lineage: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)
    synthesis: str | None = None


class ScheduleState(str, Enum):
    """Lifecycle state for an unattended mission schedule."""

    SCHEDULED = "scheduled"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(slots=True)
class MissionSchedule:
    expression: str
    next_run_at: str | None = None
    max_generation_steps: int | None = None
    enabled: bool = True
    # Operational state borrowed from Hermes' unattended-job model so scheduled
    # missions can be paused, report their last outcome, and surface errors
    # instead of being silently disabled.
    kind: str | None = None  # "once" | "interval" | "cron"
    display: str = ""
    state: ScheduleState = ScheduleState.SCHEDULED
    paused_at: str | None = None
    paused_reason: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None  # "ok" | "error"
    last_error: str | None = None
    last_delivery_error: str | None = None


@dataclass(slots=True)
class Mission:
    statement: str
    success_criteria: str
    id: str = field(default_factory=lambda: new_id("mission"))
    clarifications: dict[str, str] = field(default_factory=dict)
    approvals: dict[str, str] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)
    schedule: MissionSchedule | None = None
    generations: list[Generation] = field(default_factory=list)
    ledger: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class LedgerEntry:
    mission_id: str
    event_type: str
    summary: str
    id: str = field(default_factory=lambda: new_id("entry"))
    generation_index: int | None = None
    candidate_loop_id: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class Event:
    mission_id: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("event"))
    generation_index: int | None = None
    candidate_loop_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)


T = TypeVar("T")


def to_dict(value: Any) -> Any:
    """Convert nested dataclasses and enums into JSON-safe values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_dict(val) for key, val in value.items()}
    return value


def from_dict(cls: type[T], data: dict[str, Any]) -> T:
    """Hydrate one of this module's dataclasses from JSON data."""
    kwargs: dict[str, Any] = {}
    type_hints = get_type_hints(cls)
    for model_field in fields(cls):
        if model_field.name not in data:
            continue
        annotation = type_hints.get(model_field.name, model_field.type)
        kwargs[model_field.name] = _coerce_value(annotation, data[model_field.name])
    return cls(**kwargs)


def _coerce_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in {Union, UnionType} and type(None) in args:
        non_none = [arg for arg in args if arg is not type(None)]
        return _coerce_value(non_none[0], value) if non_none else value

    if origin is list and args:
        return [_coerce_value(args[0], item) for item in value]

    if origin is dict:
        return dict(value)

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)

    if isinstance(annotation, type) and is_dataclass(annotation):
        return from_dict(annotation, value)

    return value
