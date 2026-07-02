"""Bounded native tool loop for the standalone CLI main-loop agent."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .main_agent import MainLoopService
from .models import to_dict
from .orchestrator import MissionOrchestrator
from .providers import ProviderClient, ProviderReply, ProviderToolCall
from .reports import render_mission_report


# Minimum length for a user confirmation quote. A substring match against the
# latest user message is lenient, so requiring a substantive span prevents a
# coincidental short token from authorizing an external action.
_MIN_CONFIRMATION_QUOTE_LEN = 8


@dataclass(slots=True)
class AgentTurnResult:
    session_id: str
    content: str
    tool_iterations: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


class MainLoopAgent:
    """Run one durable user turn with bounded native function calling."""

    def __init__(
        self,
        root: str | Path,
        client: ProviderClient,
        *,
        max_tool_iterations: int = 12,
        context_limit: int = 40,
        compaction_threshold: int = 80,
    ) -> None:
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be positive.")
        self.root = Path(root)
        self.client = client
        self.max_tool_iterations = max_tool_iterations
        self.context_limit = context_limit
        self.compaction_threshold = compaction_threshold
        self.service = MainLoopService(self.root)
        self._refresh_orchestrator()

    def turn(self, session_id: str, user_message: str) -> AgentTurnResult:
        if not user_message.strip():
            raise ValueError("User message cannot be empty.")
        current = self.service.sessions.load(session_id)
        if current.status.value != "active" or current.phase.value == "paused":
            raise RuntimeError("Main-loop session is paused or closed; resume it before chatting.")
        # Hermes' useful durability invariant: persist inbound input before any
        # provider call, so a timeout/crash can resume without losing the turn.
        self.service.sessions.append_message(session_id, "user", user_message.strip())
        session = self.service.sessions.load(session_id)
        budget = session.draft.budget
        if budget.max_cost_usd is not None:
            message = (
                "Session cost budgets require provider pricing, which is not configured; "
                "use a token budget instead."
            )
            self.service.sessions.append_entry(session_id, "loop_stopped", {"reason": message})
            raise RuntimeError(message)
        iteration_limit = self.max_tool_iterations
        if budget.max_iterations is not None:
            iteration_limit = min(iteration_limit, budget.max_iterations)
        existing_tokens = session.prompt_tokens + session.completion_tokens
        if budget.max_tokens is not None and existing_tokens >= budget.max_tokens:
            message = f"Session token budget is exhausted ({budget.max_tokens})."
            self.service.sessions.append_entry(session_id, "loop_stopped", {"reason": message})
            raise RuntimeError(message)
        messages = self._provider_messages(session_id)
        total_prompt = 0
        total_completion = 0
        started = time.monotonic()

        base_timeout = getattr(self.client, "timeout", None)

        for iteration in range(iteration_limit + 1):
            if budget.max_seconds is not None and time.monotonic() - started >= budget.max_seconds:
                message = f"Agent turn exceeded the time budget of {budget.max_seconds} seconds."
                self.service.sessions.append_entry(session_id, "loop_stopped", {"reason": message})
                self._record_usage(session_id, total_prompt, total_completion)
                raise RuntimeError(message)
            # Clamp the transport timeout to the time remaining in the budget so a
            # single hung provider call cannot overshoot max_seconds.
            if budget.max_seconds is not None and base_timeout is not None:
                remaining = budget.max_seconds - (time.monotonic() - started)
                self.client.timeout = max(0.1, min(base_timeout, remaining))
            reply = self.client.complete(messages, TOOL_SCHEMAS)
            total_prompt += reply.prompt_tokens
            total_completion += reply.completion_tokens
            self._record_assistant_reply(session_id, reply)
            messages.append(_assistant_message(reply))

            if not reply.tool_calls:
                compact_prompt, compact_completion = self._maybe_compact(session_id)
                total_prompt += compact_prompt
                total_completion += compact_completion
                self._record_usage(session_id, total_prompt, total_completion)
                return AgentTurnResult(
                    session_id=session_id,
                    content=reply.content,
                    tool_iterations=iteration,
                    prompt_tokens=total_prompt,
                    completion_tokens=total_completion,
                )
            if budget.max_tokens is not None and (
                existing_tokens + total_prompt + total_completion >= budget.max_tokens
            ):
                message = f"Session token budget reached ({budget.max_tokens}) before tool execution."
                self.service.sessions.append_entry(session_id, "loop_stopped", {"reason": message})
                self._record_usage(session_id, total_prompt, total_completion)
                raise RuntimeError(message)
            if iteration >= iteration_limit:
                message = f"Tool loop exceeded the limit of {iteration_limit} iterations."
                self.service.sessions.append_entry(
                    session_id,
                    "loop_stopped",
                    {"reason": message},
                )
                self._record_usage(session_id, total_prompt, total_completion)
                raise RuntimeError(message)

            for call in reply.tool_calls:
                try:
                    result = self._dispatch_tool(session_id, call)
                except Exception as exc:  # Tool errors return to the model for recovery.
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                self.service.sessions.append_entry(
                    session_id,
                    "tool_result",
                    {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": result,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        raise AssertionError("unreachable")

    def _maybe_compact(self, session_id: str) -> tuple[int, int]:
        """Summarize a long suffix while retaining every append-only entry."""
        entries = self.service.sessions.read_entries(session_id)
        last_compaction = -1
        for index, entry in enumerate(entries):
            if entry.entry_type == "compaction":
                last_compaction = index
        suffix = entries[last_compaction + 1 :]
        if self.compaction_threshold <= 0 or len(suffix) < self.compaction_threshold:
            return 0, 0
        transcript: list[str] = []
        for entry in suffix[-self.compaction_threshold :]:
            if entry.entry_type == "message":
                transcript.append(
                    f"{entry.data.get('role', 'unknown')}: {entry.data.get('content', '')}"
                )
            elif entry.entry_type in {"draft_updated", "mission_confirmed", "tool_result"}:
                transcript.append(f"{entry.entry_type}: {json.dumps(entry.data, ensure_ascii=False)}")
        reply = self.client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize the conversation for future continuation. Preserve decisions, "
                        "constraints, unresolved questions, and user preferences. Do not invent facts. "
                        "Canonical mission state is stored separately and overrides this summary."
                    ),
                },
                {"role": "user", "content": "\n".join(transcript)},
            ],
            [],
        )
        if reply.content.strip():
            self.service.compact(session_id, summary=reply.content.strip())
        return reply.prompt_tokens, reply.completion_tokens

    def _provider_messages(self, session_id: str) -> list[dict[str, Any]]:
        context = self.service.context(session_id, recent_limit=self.context_limit)
        session = context["session"]
        messages: list[dict[str, Any]] = [{"role": "system", "content": context["system_prompt"]}]
        state = {
            "phase": session["phase"],
            "active_mission_id": session["active_mission_id"],
            "draft": session["draft"],
            "validation": context["validation"],
            "working_summary": context["working_summary"],
        }
        messages.append(
            {
                "role": "system",
                "content": "Canonical durable main-loop state:\n" + json.dumps(state, ensure_ascii=False),
            }
        )
        for entry in context["recent_entries"]:
            if entry["entry_type"] == "message":
                data = entry["data"]
                role = data.get("role")
                if role in {"user", "assistant"}:
                    message: dict[str, Any] = {"role": role, "content": data.get("content", "")}
                    calls = (data.get("metadata") or {}).get("tool_calls")
                    if role == "assistant" and calls:
                        message["tool_calls"] = calls
                    messages.append(message)
            elif entry["entry_type"] == "tool_result":
                data = entry["data"]
                call_id = data.get("tool_call_id", "unknown")
                pending = {
                    call.get("id")
                    for message in messages
                    for call in message.get("tool_calls", [])
                    if message.get("role") == "assistant"
                }
                if call_id not in pending:
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(data.get("result"), ensure_ascii=False),
                    }
                )
        return messages

    def _record_assistant_reply(self, session_id: str, reply: ProviderReply) -> None:
        calls = [_tool_call_payload(call) for call in reply.tool_calls]
        self.service.sessions.append_message(
            session_id,
            "assistant",
            reply.content,
            metadata={"tool_calls": calls, "finish_reason": reply.raw_finish_reason},
        )

    def _record_usage(self, session_id: str, prompt: int, completion: int) -> None:
        def mutate(session) -> None:
            session.prompt_tokens += prompt
            session.completion_tokens += completion

        self.service.sessions.mutate(
            session_id,
            mutate,
            entry_type="usage",
            data={"prompt_tokens": prompt, "completion_tokens": completion},
        )

    def _dispatch_tool(self, session_id: str, call: ProviderToolCall) -> dict[str, Any]:
        args = call.arguments
        if call.name == "update_mission_draft":
            return _tool_view(self.service.update_draft(session_id, args.get("patch") or {}))
        if call.name == "validate_mission_draft":
            return self.service.validate(session_id)
        if call.name == "confirm_mission":
            quote = str(args.get("confirmation_quote") or "").strip()
            if not quote:
                raise ValueError("An explicit user confirmation quote is required.")
            self._require_user_quote(session_id, quote, after_entry_type="draft_updated")
            self.service.sessions.append_entry(
                session_id,
                "user_confirmation",
                {"quote": quote},
            )
            confirmed = self.service.confirm(session_id, confirmed_by="cli_user")
            return {
                "created": confirmed["created"],
                "mission": confirmed["mission"],
                "session_id": session_id,
            }
        if call.name == "checkpoint":
            return _tool_view(
                self.service.checkpoint(
                    session_id,
                    summary=str(args.get("summary") or ""),
                    decisions=args.get("decisions") or [],
                    open_questions=args.get("open_questions") or [],
                )
            )
        if call.name == "capability_search":
            query = str(args.get("query") or "")
            return {
                "results": self.service.capabilities.search_cards(
                    query,
                    limit=int(args.get("limit") or 5),
                    include_unavailable=True,
                )
            }
        if call.name == "capability_setup_plan":
            return self.service.capability_setup_plan(
                session_id, args.get("capability_names") or []
            )
        if call.name == "capability_setup_apply":
            quote = str(args.get("confirmation_quote") or "").strip()
            self._require_user_quote(session_id, quote)
            result = _tool_view(
                self.service.capability_setup_apply(
                    session_id,
                    args.get("capability_names") or [],
                    confirmation_quote=quote,
                    approved_by="cli_user",
                )
            )
            self._refresh_orchestrator()
            return result
        if call.name == "capability_add_command":
            quote = str(args.get("confirmation_quote") or "").strip()
            self._require_user_quote(session_id, quote)
            result = self.service.add_command_capability(
                session_id,
                name=str(args["name"]),
                description=str(args["description"]),
                command=str(args["command"]),
                side_effect_class=str(args["side_effect_class"]),
                confirmation_quote=quote,
                runner=str(args.get("runner") or "agent_command"),
                approved_by="cli_user",
            )
            self._refresh_orchestrator()
            return result
        if call.name == "mission_capability_setup_plan":
            return self.service.mission_capability_setup_plan(
                str(args["mission_id"]), args.get("capability_names") or []
            )
        if call.name == "mission_capability_setup_apply":
            quote = str(args.get("confirmation_quote") or "").strip()
            self._require_user_quote(session_id, quote)
            result = self.service.mission_capability_setup_apply(
                str(args["mission_id"]),
                args.get("capability_names") or [],
                confirmation_quote=quote,
                approved_by="cli_user",
            )
            self._refresh_orchestrator()
            return result
        if call.name == "mission_status":
            mission = self.service.missions.load_mission(str(args["mission_id"]))
            return {"mission": to_dict(mission)}
        if call.name == "mission_readiness":
            mission_id = str(args.get("mission_id") or "").strip()
            if mission_id:
                return self.service.mission_readiness(mission_id)
            return self.service.readiness(session_id)
        if call.name == "mission_configure":
            quote = str(args.get("confirmation_quote") or "").strip()
            self._require_user_quote(session_id, quote)
            mission = self.orchestrator.configure_mission(
                str(args["mission_id"]),
                args.get("patch") or {},
                changed_by="cli_user",
            )
            return {"mission": to_dict(mission)}
        if call.name == "mission_pause":
            mission = self.orchestrator.pause_schedule(
                str(args["mission_id"]),
                reason=str(args.get("reason") or "").strip() or None,
            )
            return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
        if call.name == "mission_resume":
            mission = self.orchestrator.resume_schedule(str(args["mission_id"]))
            return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
        if call.name == "mission_trigger":
            mission = self.orchestrator.trigger_schedule(str(args["mission_id"]))
            return {"mission_id": mission.id, "schedule": to_dict(mission.schedule)}
        if call.name == "approve_capability":
            quote = str(args.get("confirmation_quote") or "").strip()
            if not quote:
                raise ValueError("An explicit user approval quote is required.")
            self._require_user_quote(session_id, quote)
            self.service.sessions.append_entry(
                session_id,
                "capability_approval_requested",
                {
                    "mission_id": str(args["mission_id"]),
                    "capability": str(args["capability"]),
                    "quote": quote,
                },
            )
            mission = self.orchestrator.approve_capability(
                str(args["mission_id"]),
                str(args["capability"]),
                approved_by="cli_user",
            )
            return {"mission_id": mission.id, "approvals": mission.approvals}
        if call.name == "generation_run":
            result = self.orchestrator.run_generation(str(args["mission_id"]))
            return {"result": to_dict(result)}
        if call.name == "mission_report":
            mission = self.service.missions.load_mission(str(args["mission_id"]))
            permissions = self.service.missions.read_permissions(mission.id)
            return {"report": render_mission_report(mission, permissions)}
        if call.name == "generation_prepare":
            generation = self.orchestrator.prepare_generation(str(args["mission_id"]))
            return {"generation": to_dict(generation)}
        if call.name == "candidate_claim":
            claim = self.orchestrator.claim_candidate(
                str(args["mission_id"]),
                int(args["generation_index"]),
                str(args["candidate_id"]),
                claimant_id=f"cli_session:{session_id}",
                claim_token=args.get("claim_token"),
            )
            return {"claim": to_dict(claim)}
        if call.name == "candidate_submit_result":
            candidate = self.orchestrator.submit_candidate_result(
                str(args["mission_id"]),
                int(args["generation_index"]),
                str(args["candidate_id"]),
                success=bool(args["success"]),
                summary=str(args["summary"]),
                output=str(args.get("output") or ""),
                artifacts=args.get("artifacts") or [],
                metadata={"controller": "cli_agent"},
                submission_id=call.id,
                claim_token=str(args["claim_token"]),
            )
            return {"candidate": to_dict(candidate)}
        if call.name == "candidate_artifact_write":
            artifact = self.orchestrator.write_candidate_artifact(
                str(args["mission_id"]),
                int(args["generation_index"]),
                str(args["candidate_id"]),
                claim_token=str(args["claim_token"]),
                filename=str(args["filename"]),
                content=str(args["content"]),
                kind=str(args.get("kind") or "text"),
                description=str(args.get("description") or "CLI main-loop artifact"),
            )
            return {"artifact": to_dict(artifact)}
        if call.name == "generation_finalize":
            result = self.orchestrator.finalize_generation(
                str(args["mission_id"]), int(args["generation_index"])
            )
            return {"result": to_dict(result)}
        raise ValueError(f"Unknown main-loop tool: {call.name}")

    def _refresh_orchestrator(self) -> None:
        self.orchestrator = MissionOrchestrator(
            store=self.service.missions,
            capabilities=self.service.capabilities,
        )

    def _require_user_quote(
        self,
        session_id: str,
        quote: str,
        *,
        after_entry_type: str | None = None,
    ) -> None:
        # A substring match is lenient, so require a substantive span of the
        # user's own words rather than a coincidental token like "yes".
        if len(quote.strip()) < _MIN_CONFIRMATION_QUOTE_LEN:
            raise ValueError(
                "Confirmation quote must reproduce a substantive span of the user's "
                f"approval (at least {_MIN_CONFIRMATION_QUOTE_LEN} characters)."
            )
        entries = self.service.sessions.read_entries(session_id)
        boundary = -1
        if after_entry_type:
            for index, entry in enumerate(entries):
                if entry.entry_type == after_entry_type:
                    boundary = index
        for entry in reversed(entries[boundary + 1 :]):
            if entry.entry_type != "message" or entry.data.get("role") != "user":
                continue
            content = str(entry.data.get("content") or "")
            if quote not in content:
                raise ValueError("The confirmation quote is not present in the latest user message.")
            return
        if after_entry_type:
            raise ValueError("User confirmation must arrive after the latest draft update.")
        raise ValueError("No user message contains the requested approval quote.")


def _tool_view(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "session": context["session"],
        "validation": context["validation"],
        "next_actions": context["next_actions"],
    }


def _tool_call_payload(call: ProviderToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }


def _assistant_message(reply: ProviderReply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [_tool_call_payload(call) for call in reply.tool_calls]
    return message


def _function(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _function(
        "update_mission_draft",
        "Persist user-confirmed mission fields. Do not infer consequential preferences.",
        {"patch": {"type": "object"}},
        ["patch"],
    ),
    _function("validate_mission_draft", "Check deterministic readiness and missing fields.", {}),
    _function(
        "confirm_mission",
        "Create the mission only after the user explicitly confirms the shown draft.",
        {"confirmation_quote": {"type": "string"}},
        ["confirmation_quote"],
    ),
    _function(
        "checkpoint",
        "Persist a resume summary, decisions, and open questions.",
        {
            "summary": {"type": "string"},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    ),
    _function(
        "capability_search",
        "Search capability cards before recommending tools.",
        {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
        ["query"],
    ),
    _function(
        "capability_setup_plan",
        "Plan capability additions, approvals, and runner config without changing state.",
        {"capability_names": {"type": "array", "items": {"type": "string"}}},
        ["capability_names"],
    ),
    _function(
        "capability_setup_apply",
        "Apply a capability setup only after the user explicitly approves the shown plan.",
        {
            "capability_names": {"type": "array", "items": {"type": "string"}},
            "confirmation_quote": {"type": "string"},
        },
        ["capability_names", "confirmation_quote"],
    ),
    _function(
        "capability_add_command",
        "Persist a user-approved command as a new tool. Never embed credentials.",
        {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "command": {"type": "string"},
            "side_effect_class": {
                "type": "string",
                "enum": [
                    "read_only",
                    "local_write",
                    "external_write",
                    "public_publish",
                    "spend_money",
                    "message_person",
                ],
            },
            "runner": {"type": "string", "enum": ["agent_command", "shell"]},
            "confirmation_quote": {"type": "string"},
        },
        ["name", "description", "command", "side_effect_class", "confirmation_quote"],
    ),
    _function(
        "mission_capability_setup_plan",
        "Plan tool, approval, and runner changes for an existing mission.",
        {
            "mission_id": {"type": "string"},
            "capability_names": {"type": "array", "items": {"type": "string"}},
        },
        ["mission_id", "capability_names"],
    ),
    _function(
        "mission_capability_setup_apply",
        "Apply shown capability changes to an existing mission after user confirmation.",
        {
            "mission_id": {"type": "string"},
            "capability_names": {"type": "array", "items": {"type": "string"}},
            "confirmation_quote": {"type": "string"},
        },
        ["mission_id", "capability_names", "confirmation_quote"],
    ),
    _function("mission_status", "Read canonical mission state.", {"mission_id": {"type": "string"}}, ["mission_id"]),
    _function(
        "mission_readiness",
        "Report capability gaps, blockers, and next actions before running work. "
        "Omit mission_id to check the current draft.",
        {"mission_id": {"type": "string"}},
    ),
    _function(
        "mission_configure",
        "Reconfigure a mission (success_criteria, clarifications, budget, schedule, "
        "execution_profile, selected_capabilities) after the user approves the change. "
        "The mission statement and approvals cannot be changed.",
        {
            "mission_id": {"type": "string"},
            "patch": {"type": "object"},
            "confirmation_quote": {"type": "string"},
        },
        ["mission_id", "patch", "confirmation_quote"],
    ),
    _function(
        "mission_pause",
        "Pause a mission's schedule so ticks skip it until resumed.",
        {"mission_id": {"type": "string"}, "reason": {"type": "string"}},
        ["mission_id"],
    ),
    _function(
        "mission_resume",
        "Resume a paused mission schedule and recompute its next run.",
        {"mission_id": {"type": "string"}},
        ["mission_id"],
    ),
    _function(
        "mission_trigger",
        "Mark a scheduled mission due now so the next tick runs a generation.",
        {"mission_id": {"type": "string"}},
        ["mission_id"],
    ),
    _function(
        "approve_capability",
        "Record capability-scoped approval only after the user explicitly approves the external action class.",
        {
            "mission_id": {"type": "string"},
            "capability": {"type": "string"},
            "confirmation_quote": {"type": "string"},
        },
        ["mission_id", "capability", "confirmation_quote"],
    ),
    _function(
        "generation_run",
        "Run one full generation now: plan candidates, execute them through the "
        "mission's configured runner, score, and synthesize. Blocks until done.",
        {"mission_id": {"type": "string"}},
        ["mission_id"],
    ),
    _function(
        "mission_report",
        "Render the user-facing executive report (progress, evidence, authority, "
        "attention items, next steps). Prefer this over raw status when updating the user.",
        {"mission_id": {"type": "string"}},
        ["mission_id"],
    ),
    _function("generation_prepare", "Plan a generation without executing it.", {"mission_id": {"type": "string"}}, ["mission_id"]),
    _function(
        "candidate_claim",
        "Policy-check and claim one candidate before working on it.",
        {
            "mission_id": {"type": "string"},
            "generation_index": {"type": "integer"},
            "candidate_id": {"type": "string"},
            "claim_token": {"type": "string"},
        },
        ["mission_id", "generation_index", "candidate_id"],
    ),
    _function(
        "candidate_artifact_write",
        "Store durable candidate evidence and receive the artifact reference for result submission.",
        {
            "mission_id": {"type": "string"},
            "generation_index": {"type": "integer"},
            "candidate_id": {"type": "string"},
            "claim_token": {"type": "string"},
            "filename": {"type": "string"},
            "content": {"type": "string"},
            "kind": {"type": "string"},
            "description": {"type": "string"},
        },
        [
            "mission_id",
            "generation_index",
            "candidate_id",
            "claim_token",
            "filename",
            "content",
        ],
    ),
    _function(
        "candidate_submit_result",
        "Submit a claimed candidate's structured result and evidence.",
        {
            "mission_id": {"type": "string"},
            "generation_index": {"type": "integer"},
            "candidate_id": {"type": "string"},
            "claim_token": {"type": "string"},
            "success": {"type": "boolean"},
            "summary": {"type": "string"},
            "output": {"type": "string"},
            "artifacts": {"type": "array", "items": {"type": "object"}},
        },
        ["mission_id", "generation_index", "candidate_id", "claim_token", "success", "summary"],
    ),
    _function(
        "generation_finalize",
        "Finalize only after every candidate is completed, failed, or policy-discarded.",
        {"mission_id": {"type": "string"}, "generation_index": {"type": "integer"}},
        ["mission_id", "generation_index"],
    ),
]
