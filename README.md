# Multi-Loop Orchestration

`multi-loop` is a standalone mission orchestration harness inspired by
single-goal loop systems.

The basic execution pattern it generalizes is a bounded goal loop:

```text
goal -> decompose -> execute workers -> aggregate -> review -> refine -> done
```

Multi-loop orchestration treats that whole loop as one executable unit inside a
larger meta-loop. Instead of asking one loop to solve one bounded goal, the
system launches, compares, mutates, and coordinates many loops against a broad
mission.

```text
mission
  -> intake and scope
  -> generate loop population
  -> run many candidate loops
  -> compare fitness
  -> preserve the best outputs
  -> mutate / cross over strategies
  -> launch the next generation
  -> converge on an integrated result
```

## Progression

The reference model has three levels:

1. Linear prompting: user gives a task, agent completes it, user gives the next
   task.
2. Goal loop: user gives a goal, the harness decomposes, executes, reviews, and
   iterates until success.
3. Multi-loop orchestration: user gives a mission, the meta-harness runs many
   goal loops inside a broader orchestration loop.

## Design Principle

A multi-loop is a deterministic meta-harness, not a giant prompt.

The outer harness owns control flow, state, budgets, lineage, selection,
convergence, and user checkpoints. Models provide judgement inside well-scoped
roles: intake, strategy generation, loop planning, review, fitness scoring, and
mutation.

## Core Concept

A single-goal run answers: "Can this loop complete this goal?"

A `multi-loop` run answers: "Which collection of loops, strategies, and
generations can make progress on this larger mission?"

Any mission that is too broad, uncertain, or high-variance for one loop can be
turned into a portfolio of loop-driven experiments.

For broad missions like "start a company," no single loop should try to solve the
whole mission directly. The multi-loop orchestrator should create specialized
loops such as market research, customer discovery, product thesis, ad campaign
experiments, brand, technical prototype, legal checklist, financial model, and
launch plan. Each loop has its own success criteria, reviewer, budget, artifacts,
and continuation state.

## Experiments As First-Class Loops

Autoresearch is useful because it treats progress as experiments: mutate a
candidate, run it under a fixed budget, score it, keep or discard it, and repeat.
Multi-loop should generalize that pattern beyond code and model training.

An experiment can be any loop with:

- A hypothesis.
- A controlled scope.
- A budget.
- A runnable plan.
- An artifact.
- A fitness signal.
- A keep, discard, mutate, or merge decision.

For a company mission, an ad campaign can be handled as an experiment portfolio:

- Generate several campaign hypotheses for different audiences or promises.
- Produce creative variants, landing page copy, targeting assumptions, and budget
  plans.
- Score each campaign by expected conversion, clarity, differentiation, risk,
  cost, and evidence quality.
- Mutate weak campaigns by narrowing the audience, changing the hook, changing
  the channel, or simplifying the offer.
- Cross over strong pieces, such as combining the best audience insight with the
  best creative angle.

The same experiment model applies to product design, engineering architecture,
sales scripts, research approaches, content strategies, and hiring plans.

## Autonomy And Cost

Multi-loop is expected to consume many model calls and many tokens. That is not a
bug; it is the point of moving from single-loop execution to long-running mission
orchestration.

The harness should manage autonomy with explicit controls instead of pretending
the work is cheap:

- Mission-level budgets.
- Per-loop budgets.
- Generation limits.
- Fitness thresholds.
- User checkpoint policies.
- Durable event logs.
- Resume support.

The default mode should be autonomous enough to run for a long time, but visible
enough that the user can inspect status, stop it, redirect it, or approve a major
branch in the mission.

## Operational Control Plane

Multi-loop needs a small runtime control plane around the model loops. The
orchestrator should not expose every possible tool and policy in one giant prompt.
It should maintain explicit registries for capabilities, schedules, policy gates,
and child-loop delegation.

### Capability Registry

Capabilities should be described as searchable cards, not hardcoded prompt text.
Each card should include:

- Name and description.
- Toolset or backend required.
- Inputs, outputs, and artifact types.
- Availability check.
- Cost and latency class.
- Side-effect class: read-only, local write, external write, public publish,
  spend money, or message a person.
- Verification method.

The planner can search or filter capabilities when building a portfolio. Core
capabilities stay directly visible, while rare or expensive capabilities can be
discovered on demand by a search/describe/call bridge. This keeps prompt size
bounded while still letting missions discover browser, media generation,
messaging, social, remote execution, and future MCP-style tools.

### Policy Gates

Any candidate loop that can affect the outside world should pass through a policy
gate before execution. Examples that should require explicit approval or a stored
policy rule include:

- Publishing, posting, uploading, or sending messages.
- Spending money or launching paid ads.
- Mutating remote services.
- Deleting or replacing external assets.
- Using credentials beyond read-only access.

Worker self-reports are not sufficient for side effects. Successful external
actions should return verifiable handles such as URLs, object IDs, status codes,
receipts, or absolute artifact paths, and the parent loop should verify them
before reporting success.

### Scheduled Missions

Long-running missions should be resumed by bounded scheduled jobs, not by a
single never-ending agent process. A schedule tick should:

- Load mission state and the latest ledger entries.
- Run one bounded generation or maintenance step.
- Save outputs and decisions.
- Advance the next run time.
- Deliver a status report only when there is something useful to report.

Scheduled mission jobs should be self-contained and non-interactive. They should
not recursively create more schedules, ask clarifying questions, or send messages
directly; delivery is handled by the scheduler. Script-only jobs are useful for
cheap checks that decide whether an agent run is needed.

### Delegation Semantics

Candidate loops can delegate internally, but delegation needs explicit limits.
Default child loops should be leaves: isolated context, restricted tools, no user
interaction, no memory writes, and no recursive spawning. Nested orchestration
should be an opt-in role with a depth limit and a concurrency budget.

Use delegation for independent reasoning-heavy workstreams, not for single tool
calls or mechanical steps. Durable work that must outlive the current turn should
be scheduled instead of delegated synchronously.

## Genetic Execution Model

The genetic layer gives the system a way to explore multiple possible paths
instead of committing to the first plan.

- Genome: a candidate strategy, plan, artifact set, or business thesis.
- Population: multiple loop runs exploring different genomes in parallel.
- Fitness: measurable score from reviewers, tests, market evidence, user
  preferences, cost, risk, and completeness.
- Selection: preserve the strongest candidates and discard weak or redundant
  ones.
- Mutation: change one candidate by adding constraints, trying a different
  audience, simplifying scope, changing implementation approach, or targeting a
  different distribution channel.
- Crossover: combine useful parts from two candidates into a new candidate.
- Generation: one outer cycle of run -> score -> select -> mutate -> rerun.

## Outer Loop Phases

```text
intake
  -> mission framing
  -> portfolio planning
  -> loop spawning
  -> loop monitoring
  -> artifact aggregation
  -> fitness review
  -> selection and mutation
  -> synthesis
  -> user checkpoint or next generation
```

### 1. Intake

Clarify only mission-changing unknowns. For "start a company," the system might
ask about industry, budget, time horizon, risk tolerance, geography, skills,
preferred business model, and whether the output should be a plan, prototype, or
operating company scaffold.

### 2. Portfolio Planning

Create a portfolio of loops, not just a task list. Each loop should have a role,
goal, success criteria, dependencies, budget, verification method, and expected
artifact.

### 3. Loop Spawning

Run many candidate experiments as executable units. A candidate can be an agent
run, MCP call, shell command, mock runner, manual task, or future custom backend.
Some can be parallel, some gated by dependencies, and some intentionally
redundant to compare alternate strategies.

### 4. Fitness Review

Score candidate outputs against mission-level criteria. Fitness can include
quality, evidence, novelty, risk, cost, speed, user fit, test results, and
compatibility with other artifacts.

### 5. Selection And Mutation

Keep the best candidates, merge compatible outputs, and create new loop goals
from gaps or promising variants.

### 6. Synthesis

Produce an integrated mission artifact: an implementation, plan, company brief,
research dossier, product prototype, content package, or operating roadmap.

## Examples

### Start A Company

Mission: "Start a company."

Possible candidate loops:

- Clarify founder constraints and target domains.
- Research 5 market opportunities.
- Generate 3 business theses.
- Validate customer pain and willingness to pay.
- Run ad campaign experiments.
- Design an MVP scope.
- Prototype the product.
- Build a brand and positioning system.
- Produce a 30-day launch plan.
- Build a basic financial model.
- Review legal, compliance, and operational risks.

Genetic behavior:

- Run multiple business theses in parallel.
- Score them by feasibility, demand, founder fit, speed to revenue, and risk.
- Mutate weak but promising theses into narrower niches.
- Cross over the strongest market insight with the strongest product idea.
- Continue until one integrated company plan is strong enough to execute.

### Run An Ad Campaign Portfolio

Mission: "Find and refine the best ad campaign for a product."

Possible candidate loops:

- Define audiences and campaign hypotheses.
- Generate hooks, offers, and creative directions.
- Draft landing page variants.
- Produce channel-specific copy for search, social, email, or creator outreach.
- Score variants against user fit, differentiation, clarity, risk, and estimated
  cost.
- Synthesize the winning campaign plan.

Genetic behavior:

- Treat each campaign direction as a genome.
- Mutate targeting, hook, offer, proof, format, or channel.
- Cross over the strongest audience with the strongest message.
- Preserve a lineage of discarded and winning variants so the user can see why a
  campaign was selected.

### Build A Large Application

Mission: "Build a project management SaaS."

Possible candidate loops:

- Requirements and user stories.
- Data model and architecture.
- Authentication and billing.
- Core task board UI.
- Collaboration and notifications.
- Test strategy and Playwright coverage.
- Deployment and observability.
- Security and abuse review.

Genetic behavior:

- Compare multiple architecture candidates.
- Run competing UI directions.
- Select the simplest implementation that passes verification.
- Mutate failing components into smaller scoped loops.

### Produce A Documentary Or Video Essay

Mission: "Make a YouTube documentary about a topic."

Possible candidate loops:

- Research the story and timeline.
- Find source clips and transcript evidence.
- Generate competing narrative structures.
- Draft narration.
- Build an edit decision list.
- Create title, thumbnail, and packaging options.
- Review pacing, claims, and source support.

Genetic behavior:

- Generate several narrative angles.
- Score them by clarity, originality, evidence, and audience pull.
- Cross over the strongest hook with the strongest evidence structure.

### Scientific Or Technical Research

Mission: "Find the best approach to solve a hard technical problem."

Possible candidate loops:

- Literature and prior-art review.
- Prototype approach A.
- Prototype approach B.
- Benchmark and evaluate tradeoffs.
- Failure-mode analysis.
- Final recommendation and implementation plan.

Genetic behavior:

- Treat each approach as a genome.
- Use benchmarks and reviewer judgement as fitness.
- Mutate the highest-potential approaches based on observed failures.

## Initial Data Model Sketch

```text
Mission
  id
  statement
  success_criteria
  clarifications
  budget
  schedule
  generations[]
  ledger[]

Generation
  index
  candidate_loops[]
  fitness_scores[]
  selected_lineage[]
  mutations[]
  synthesis

CandidateLoop
  id
  parent_ids[]
  goal
  success_criteria
  role
  dependencies[]
  budget
  required_capabilities[]
  policy_gates[]
  verification
  result
  artifacts[]
  fitness

Capability
  name
  description
  toolset_or_backend
  availability_check
  side_effect_class
  cost_class
  verification

Job
  id
  mission_id
  schedule
  next_run_at
  state
  max_generation_steps
  enabled_capabilities[]
  disabled_capabilities[]

LedgerEntry
  id
  mission_id
  generation_index
  candidate_loop_id
  event_type
  summary
  artifacts[]
  created_at
```

## Standalone Implementation

`agentloop` is a separate project and should be treated only as inspiration.
`multi-loop` should define its own runtime model, storage model, runner
abstraction, and orchestration flow.

The likely implementation path is:

1. Define mission, generation, experiment, candidate run, artifact, fitness, and
   ledger data types.
2. Add capability and policy-gate data types.
3. Add a backend-neutral runner interface for agents, MCP calls, shell commands,
   manual tasks, scheduled jobs, and mock runs.
4. Persist candidate lineage and artifacts under a mission run directory.
5. Add a fitness reviewer role that scores candidate outputs.
6. Add selection, mutation, and crossover policies.
7. Add user checkpoint/resume behavior for broad missions.
8. Add scheduled mission ticks for recurring long-running work.

## MVP Runtime

The current MVP has a deterministic one-generation runtime:

- `MissionOrchestrator.create_mission(...)` creates a persisted mission.
- `MissionOrchestrator.run_generation(...)` plans three candidate loops, runs them,
  scores fitness, selects lineage, writes synthesis, and appends event/ledger data.
- `MockRunner` produces deterministic local artifacts for tests and demos.
- `ShellRunner` runs a configured shell command.
- `AgentCommandRunner` runs an external agent CLI command with the candidate prompt
  on stdin.
- Verification commands can run after a candidate and affect its success score.

Note: in the current MVP, candidate loops within a generation run sequentially, not
in parallel. The parallel-population behavior described above is the design target,
not yet the runtime behavior.

CLI examples:

```bash
python3 -m multi_loop onboard --mission "Start a company"
python3 -m multi_loop create "Start a company" --success-criteria "Produce a launch plan"
python3 -m multi_loop run <mission-id>
python3 -m multi_loop status <mission-id>
python3 -m multi_loop list
```

### Scheduling

Missions can carry a schedule that the `tick` command advances one bounded
generation at a time. Supported expressions:

- One-shot: `30m`, `2h`, `1d`, or an ISO timestamp like `2026-07-01T09:00:00`.
- Recurring interval: `every 30m`, `every 2h`, `every 1d`.
- Cron: `0 9 * * *` (requires the optional `croniter` package; other kinds stay
  dependency-free).

A schedule tracks operational state (`scheduled`, `paused`, `completed`,
`error`) plus the last run's outcome (`last_status`, `last_error`). Recurring
runs are pre-advanced before execution (at-most-once on crash), missed runs past
their catch-up grace window are fast-forwarded instead of firing a stale burst,
and a recurring schedule that can no longer compute its next run is surfaced as
`error` rather than silently disabled.

```bash
python3 -m multi_loop create "Monitor competitors" --schedule "every 1d"
python3 -m multi_loop pause <mission-id> --reason "holding for review"
python3 -m multi_loop resume <mission-id>
python3 -m multi_loop trigger <mission-id>   # mark due now
python3 -m multi_loop tick                    # run all missions that are due
```

The intended flow is onboarding first:

1. The user states the mission.
2. The orchestrator explains relevant configured capabilities and capabilities
   that need setup.
3. The user answers mission-critical questions: success criteria, time horizon,
   constraints, available resources, autonomy level, approval policy, schedule,
   and preferred tools.
4. The orchestrator creates the mission with those clarifications saved.
5. The first generation runs as a dry/local pass unless the user approves broader
   tools or external side effects.

Use this for a non-interactive dry setup:

```bash
python3 -m multi_loop onboard --mission "Run a company" --defaults
```

Runtime state is stored under `.multi-loop/runs/<mission-id>/`:

- `mission.json`: mission state, generations, candidates, scores, selected lineage.
- `ledger.jsonl`: durable mission history.
- `events.jsonl`: event stream for monitoring/debugging.
- `artifacts/`: candidate outputs and generation synthesis.
- `results/`: structured candidate run results.
- `.run.lock`: exclusive run lease. A generation holds this lock for its whole
  duration, so a scheduled tick, a detached MCP run, and a manual CLI run can
  never produce a duplicate generation on the same mission; concurrent callers
  raise `MissionBusy` (the scheduler reports this as an `already_running` skip).
  The lock is process-held, so a crashed runner releases it automatically.

## MCP Server

`multi-loop` can also run as an MCP server. The MCP package is optional so the
core CLI and tests stay dependency-free:

```bash
pip install -e ".[mcp]"
python3 -m multi_loop.mcp_server
```

If installed as a package, the console script is available too:

```bash
multi-loop-mcp
```

The server exposes the mission runtime directly:

- `onboard` builds an onboarding plan and can create the mission.
- `create_mission`, `mission_status`, `list_missions`, and `approve_capability`
  manage persisted mission state.
- `run_generation` runs one generation. It detaches by default and returns a
  `run_id` immediately.
- `run_status`, `run_tail`, `run_result`, and `run_list` monitor detached runs.
- `tick` runs scheduled mission ticks that are currently due.
- `list_backends` and `doctor` report local runner/capability and storage health.
- `capability_search`, `capability_describe`, and `capability_list` are the
  on-demand discovery bridge: search returns matching capability cards, describe
  returns one full card (including `available`, `requires_env`, and `missing_env`),
  and list enumerates all cards. The same surface is available from the CLI via
  `multi-loop capabilities [--search Q | --describe NAME | --available]`.
- `toolset_list` and `toolset_resolve` work with named capability bundles.
  Toolsets compose via `includes` (e.g. `company` folds in `research`,
  `outreach`, and `media`), and resolution accepts a mix of toolset names,
  capability names, and `all`/`*`, returning a deduped capability list. The CLI
  mirrors this with `multi-loop toolsets [--resolve "company,agent_loop"]`.

Detached MCP run logs live under `.multi-loop/mcp-runs/<run-id>/` with
`events.jsonl`, `status.json`, and `result.json`. Mission state remains under
`.multi-loop/runs/<mission-id>/`.

## Program Files

The `program.md` idea from autoresearch maps well to multi-loop, but it should be
generalized into mission operating files instead of one hardcoded research prompt.

Possible files:

- `program.md`: the meta-loop operating procedure.
- `mission.md`: the user's mission, clarifications, constraints, and success
  criteria.
- `portfolio.md`: the current set of candidate loops and experiments.
- `fitness.md`: scoring rubrics and thresholds.
- `capabilities/`: searchable capability cards and policy metadata.
- `ledger.tsv` or `ledger.jsonl`: durable experiment history.

These files become editable "organization code." The user and agents improve how
the autonomous organization behaves by editing the program files, while the
Python harness enforces the loop mechanics.

## Open Design Questions

- Should candidate runs execute in separate worktrees by default, then merge
  selected artifacts back into a mission workspace?
- Should fitness be a single numeric score, a rubric object, or both?
- How much autonomy should mutation have before asking the user?
- Should the first version support true crossover, or start with selection plus
  mutation only?
- Should multi-loop be exposed as a separate MCP tool, or as an extension of the
  existing `orchestrate` server?
