# The Operator Protocol

multi-loop is built around three roles:

| Role | Who | Owns |
| --- | --- | --- |
| **Principal** | The user | The mission statement, side-effect approvals, and final say at checkpoints. Otherwise hands-off. |
| **Operator** | The agent (an MCP host like Claude Code or Codex, or the built-in CLI agent) | Everything else: preparation, configuration, execution, supervision, and reporting. The executive director of the mission. |
| **Harness** | multi-loop itself | Deterministic control flow: durable state, leases, budgets, policy gates, verification, scheduling, lineage, and the audit ledger. |

The principal states a mission and goes back to their life. The operator
interviews them once, wires up what the mission needs, runs generations,
reconfigures the mission as reality changes, and comes back only with results
or decisions that genuinely belong to the principal. The harness makes every
operator action durable, auditable, and bounded.

## The operator loop

```text
open session (main_loop_open)
  -> interview: turn confirmed intent into the mission draft (mission_draft_update)
  -> prep: map the mission to capabilities (capability_search / capability_setup_plan)
  -> close gaps: setup, custom commands, approvals (capability_setup_apply,
     capability_add_command, approve_capability)
  -> readiness gate: mission_readiness until no blockers remain
  -> confirm: mission_confirm after the principal explicitly approves the draft
  -> generations: generation_prepare -> candidate_claim -> execute with your own
     tools -> candidate_artifact_write -> candidate_submit_result -> generation_finalize
     (or run_generation for runner-driven execution)
  -> supervise: mission_configure / mission_pause / mission_resume / mission_trigger
     as circumstances change; tick advances scheduled missions
  -> report: checkpoint durable decisions; deliver results, not process
```

## Readiness: prep is a first-class phase

`mission_readiness` is the operator's prep instrument. It deterministically
answers: *if a generation ran right now, what would fail or be blocked?*

- Pass `session_id` while onboarding to check the draft, or `mission_id`
  after creation.
- Every capability the mission relies on is classified:
  - `ready` — available, and approved if it has side effects.
  - `needs_setup` — backend missing or unconfigured; the item carries
    `missing_env`, the availability check, and a concrete `fix`.
  - `needs_approval` — available, but its side-effect class requires a
    recorded approval from the principal.
  - `unknown` — not a registered capability; search for an equivalent or
    persist it with `capability_add_command`.
- Scheduled missions are additionally checked for a real unattended runner
  (an `agent_command`/`shell` runner with an executable command).
- `blockers` gate readiness; `notices` (like a paused schedule) inform
  without blocking; `next_actions` tell the operator what to do about each gap.

Run it before confirming the mission, before the first generation, and after
any capability, approval, or environment change. Work through the gaps
conversationally — this is exactly the preparation the principal should never
have to do themselves.

## Configuration authority

`mission_configure` gives the operator authority over every mutable mission
setting after creation:

| Patch key | Meaning |
| --- | --- |
| `success_criteria` | Replace the success criteria (never empty). |
| `clarifications` | Merge keys; an empty value deletes a key. |
| `budget` | `max_iterations`, `max_seconds`, `max_tokens` (positive; `max_cost_usd` is rejected without provider pricing). |
| `schedule` | A new expression (`every 1d`, `30m`, cron, ISO timestamp); `null` clears the schedule. |
| `execution_profile` | `runner`, `runner_command`, `verification`, `workspace`, `autonomy_level`. |
| `selected_capabilities` | Replace the mission's capability list (names must be registered). |

Two things are deliberately **not** configurable by the operator:

- **The mission statement.** It is the principal's word; a different mission
  is a new mission.
- **Side-effect approvals.** Publishing, spending, messaging people, and
  mutating remote services always go through `approve_capability`, backed by
  the principal's explicit consent. Adding a side-effecting capability to the
  mission does not approve it; policy gates still block until approval exists.

Every applied patch is validated as a whole before anything persists (a bad
field leaves the mission untouched) and is recorded as a `mission_configured`
event plus a ledger entry naming the changed fields and the changer.

Schedule operations are first-class operator controls:

- `mission_pause` — ticks skip the mission until resumed (reason recorded).
- `mission_resume` — recompute the next future run and rejoin the schedule.
- `mission_trigger` — mark the mission due now; the next `tick` runs it.

## What the operator commands

"Everyone and everything" concretely means:

- **Candidate loops** — plan them (`generation_prepare`), claim and execute
  them with the host's own tools, or delegate to runners (`run_generation`
  with a `runner_command` such as `claude -p`). Verification commands, not
  worker self-reports, decide success.
- **Sub-agents** — the `agent_command` runner pipes each candidate's
  self-contained prompt to any agent CLI; spawned agents receive a safety
  directive that denies side effects unless their specific capability is
  approved.
- **Schedules** — long missions advance through bounded `tick` steps, not a
  never-ending process; the operator sets, pauses, resumes, and triggers them.
- **Capabilities** — searchable cards, persistent user-approved commands, and
  toolset bundles; the operator plans and applies setup with the principal's
  confirmation quote.
- **Memory of failure** — every finished candidate records a classified
  `Outcome`; pitfalls and cross-mission lessons are injected into future
  loops automatically.

## Audit and trust

The operator's authority is broad because the harness makes it accountable:

- Sessions are append-only and fsynced; summaries never replace canonical
  mission state.
- Consequential actions in the CLI agent require a quote from the principal's
  own words; MCP hosts govern consent in their own conversation, and the
  harness still records who changed what.
- Configuration changes, approvals, schedule operations, and generation
  results all land in `events.jsonl` and `ledger.jsonl` under the mission
  directory, queryable across missions via `search`.
- Generations run under an exclusive lease, so a scheduled tick, a detached
  run, and a manual run can never double-execute a mission.
