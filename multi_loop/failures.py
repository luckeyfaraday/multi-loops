"""Failure classification for candidate loops.

The orchestrator records a structured :class:`~multi_loop.models.Outcome` for
every candidate so later generations can learn from what went wrong. This module
turns the signals already present on a finished run — exit codes, timeouts,
policy blocks, verification results, output shape — into a closed
:class:`~multi_loop.models.FailureClass` plus a short, agent-readable
``remedy_hint``.

The default classifier is purely rule-based: deterministic, free, and testable,
mirroring ``FitnessReviewer``. A fuzzier ``LlmClassifier`` can be swapped in via
the :class:`FailureClassifier` protocol without touching the orchestrator.
"""

from __future__ import annotations

from typing import Protocol

from .models import CandidateLoop, FailureClass, Outcome
from .runners import RunResult

# Below this summary length a successful-looking result is treated as too thin to
# be useful evidence (matches the specificity floor in ``FitnessReviewer``).
_MIN_USEFUL_SUMMARY = 20

_REMEDIES: dict[FailureClass, str] = {
    FailureClass.POLICY_BLOCKED: (
        "A required action was blocked pending approval; secure scoped approval "
        "before attempting the side effect again."
    ),
    FailureClass.TOOL_UNAVAILABLE: (
        "A required tool/capability was unavailable; do not rely on it until it "
        "is configured, and plan around it in the meantime."
    ),
    FailureClass.RESOURCE_EXHAUSTED: (
        "A prior attempt ran out of time or budget; reduce scope, split the work "
        "into smaller steps, or raise the budget."
    ),
    FailureClass.EXECUTION_ERROR: (
        "A prior attempt errored out during execution; inspect the failure detail "
        "and fix the command or inputs before retrying."
    ),
    FailureClass.VERIFICATION_FAILED: (
        "A prior attempt could not be verified; produce concrete, checkable "
        "evidence and re-run the verification steps."
    ),
    FailureClass.BAD_OUTPUT: (
        "A prior attempt returned a thin or empty result; be specific and produce "
        "durable artifacts that satisfy the success criteria."
    ),
    FailureClass.STRATEGY_ERROR: (
        "A prior attempt completed but in the wrong direction; reconsider the "
        "approach rather than repeating it."
    ),
    FailureClass.UNKNOWN: (
        "A prior attempt failed for an unclassified reason; narrow the scope and "
        "capture more diagnostic detail."
    ),
}


class FailureClassifier(Protocol):
    """Turn a finished candidate run into a structured outcome."""

    def classify(self, candidate: CandidateLoop, result: RunResult) -> Outcome:
        """Return the outcome for one finished candidate run."""


class RuleBasedClassifier:
    """Deterministic classifier driven by signals already on the run result."""

    def classify(self, candidate: CandidateLoop, result: RunResult) -> Outcome:
        if result.success:
            return Outcome(
                candidate_loop_id=candidate.id,
                success=True,
                confidence=1.0,
            )

        failure_class, subreason, signals = _classify_failure(result)
        severity = (
            "blocking"
            if failure_class in {FailureClass.POLICY_BLOCKED, FailureClass.TOOL_UNAVAILABLE}
            else "normal"
        )
        return Outcome(
            candidate_loop_id=candidate.id,
            success=False,
            failure_class=failure_class,
            failure_subreason=subreason,
            severity=severity,
            remedy_hint=_REMEDIES[failure_class],
            confidence=0.5 if failure_class is FailureClass.UNKNOWN else 0.8,
            signals=signals,
        )


def _classify_failure(result: RunResult) -> tuple[FailureClass, str, dict[str, object]]:
    metadata = result.metadata
    signals: dict[str, object] = {}

    if metadata.get("blocked_by_policy"):
        # ``prepare_candidate`` reports both unavailable capabilities and unmet
        # approval gates through the same block path; the summary distinguishes
        # them, and the distinction drives different recovery in the planner.
        summary = (result.summary or "").lower()
        if "unavailable" in summary or "not registered" in summary:
            return FailureClass.TOOL_UNAVAILABLE, "capability_unavailable", signals
        return FailureClass.POLICY_BLOCKED, "approval_required", signals

    if metadata.get("timed_out"):
        return FailureClass.RESOURCE_EXHAUSTED, "timeout", signals

    error = metadata.get("error")
    if error:
        signals["error"] = error
        return FailureClass.EXECUTION_ERROR, str(error), signals

    exit_code = metadata.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        signals["exit_code"] = exit_code
        return FailureClass.EXECUTION_ERROR, f"exit_{exit_code}", signals

    if result.verification and not all(item.success for item in result.verification):
        failed = [item.command for item in result.verification if not item.success]
        signals["failed_verification"] = failed
        return FailureClass.VERIFICATION_FAILED, "verification", signals

    if not result.artifacts or len((result.summary or "").strip()) < _MIN_USEFUL_SUMMARY:
        return FailureClass.BAD_OUTPUT, "thin_result", signals

    return FailureClass.UNKNOWN, "", signals
