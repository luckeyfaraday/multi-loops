# Executive Director Agent Project Direction

Draft date: July 1, 2026

Revised: July 1, 2026 — second review. Scope cut to demo-first: question inventory archived to `direction-question-archive.md`, reference demo set to the GitHub stars mission, Stage 1 adapter contract narrowed, milestones reordered.

## One-Sentence Direction

Build an autonomous mission operating system, internally shaped by the Executive Director Agent pattern: the user talks to one orchestrator, explains their goals and context, and the orchestrator investigates, defines the mission, configures tools, creates loops, spawns sub-agents, schedules ongoing work, executes through the loop system, reviews evidence, and keeps operating until the mission changes, succeeds, or needs the user's judgment.

## What I Understand

The desired application is not just a task runner, chat agent, workflow builder, company runner, or scaffold generator. It is a CEO-like operating layer for autonomous agent work.

The user should be able to say something broad, such as:

```text
Run a company.
```

That is only one example. The product should be able to orchestrate whatever mission the user and orchestrator agree to pursue: run a GitHub growth campaign, run marketing, operate a business function, manage an open-source ecosystem, build software, monetize a project, research a market, create a content engine, or run parts of the user's life.

The system should then behave like an executive director:

- Interpret the mission.
- Ask only the questions that materially affect the mission.
- Build an operating structure from the mission.
- Create specialized departments or loops such as Finance, Development, Marketing, Operations, Research, Legal, Sales, and Support.
- Assign sub-agents with focused roles such as CFO, accountant, engineer, writer, reviewer, image generator, posting agent, researcher, or analyst.
- Discover and configure required tools such as Stripe, GitHub, browser automation, image generation, X API, email, payment APIs, databases, or MCP servers.
- Run work in bounded loops.
- Review progress and evidence.
- Synthesize decisions across loops.
- Keep an audit trail of what happened and why.
- Keep operating through the loop/sub-agent system instead of stopping at a plan.
- Escalate to the user only for mission-level direction, negotiated permission changes, budget decisions, ambiguous judgment calls, or checkpoints.

The core idea is: the user directs the mission, not the operations.

## Locked Direction From Review

These decisions supersede the first draft assumptions:

- **"Executive Director Agent" is internal language.** It describes the architecture and behavior, not necessarily the product name.
- **The product is mission-general and operating-oriented.** "Run a company" is one example, not the canonical scope.
- **The product must execute, not only negotiate or scaffold.** The orchestrator talks with the user, then drives the loop and sub-agent system to do the work.
- **The first target user can be a vibecoder, but the product category is broader.** It should feel like an autonomous operator for GitHub, marketing, business, life systems, or any long-running mission.
- **The interface is undecided.** A TUI may be the right near-term surface; CLI, MCP, and web dashboard remain open options.
- **Tools are chosen per mission.** The user and lead agent decide which APIs, MCP servers, CLIs, browser tools, local commands, and services are needed.
- **Permissions are negotiated per mission.** Full permissions must be possible because the agent needs authority to modify configuration and execute work, but that authority should be explicit, inspectable, and revocable.
- **The organization shape is dynamic.** Departments, loops, and agents may be chosen by the orchestrator and user rather than hardcoded as a universal model.
- **Hermes is the key substrate candidate.** Hermes already has much of the agent automation layer: model providers, tools, TUI, gateways, cron, skills, memory, sub-agents, and integrations.
- **The agent layer is a major open decision.** The product may sit above Hermes, fork Hermes, or extract a reusable agent layer from Hermes; MCP is not assumed to be the center.
- **The current implementation is far from the desired feeling.** The next work should focus on architecture and product shape before claiming the experience is solved.
- **"Run a company" is a north-star illustration, not the reference demo.** The reference demo is the GitHub stars mission: measurable, evidence-rich, indefinite, and real-tool.
- **Departments are a presentation layer.** The persisted model is loops; departments, teams, and org charts are projections for the user, not a second data model.
- **A visible permission ledger is decided.** Every grant, use, and revocation of authority is recorded and inspectable. This is what keeps "hands-off" from becoming "hidden."

## Directional Shift

The current project already has the right foundation: principal, operator, harness, mission drafts, capability setup, readiness checks, schedules, policy gates, candidate loops, runners, lineage, and ledgers.

The next product direction is to make the operator feel like an Executive Director Agent rather than a lower-level mission runtime.

That means shifting emphasis:

- From "run a generation" to "operate a mission."
- From "candidate loops" to "specialized departments and agents."
- From "capability registry" to "tool and backend onboarding."
- From "planner output" to "executive operating plan."
- From "logs" to "board-level progress reports with evidence."
- From "ask the user for setup details" to "the agent discovers what is missing, proposes setup, and asks only for approval or credentials when unavoidable."
- From "one mission execution" to "a durable autonomous organization that can resume, learn, schedule work, monitor the world, and keep operating."

One structural question is now first-class instead of buried: the generation/fitness/candidate-mutation machinery was built for evolutionary search over candidate loops, but an indefinite operating mission mostly wants stable scheduled loops plus review-driven replanning, not per-generation mutation. The working hypothesis is that generations and fitness stay useful for bounded experiments inside a mission, while the mission itself runs as scheduled work with periodic review. The reference demo must confirm or kill this hypothesis before the loop model is extended further.

## Product Promise

The product should make this promise:

> Talk to the orchestrator once. It will learn the mission, assemble the agents and tools, run the work, monitor progress over time, and come back only when your judgment changes the outcome.

## Non-Negotiable Principles

1. The user owns the mission.
2. The Executive Director Agent owns preparation, configuration, execution, and supervision.
3. The harness owns durability, budgets, leases, policy, verification, scheduling, lineage, and audit.
4. Permission scope is negotiated between the user and lead agent; broad or full permissions are allowed when explicitly granted and recorded.
5. Worker self-reports are not enough; important results need evidence.
6. Tool setup is part of the mission prep phase, not a user chore.
7. Loops should be specialized and bounded.
8. Long-running work should resume through scheduled bounded ticks, not one endless process.
9. The product should optimize for hands-off operation, but not hidden operation.
10. The user should always be able to inspect status, stop work, redirect work, change permissions, or approve broader autonomy.

## Core Roles

### Principal

The user. Owns:

- Mission statement.
- Mission-changing preferences.
- Permission grants and permission boundaries.
- Budget and spend decisions.
- Final checkpoint calls when the system cannot responsibly decide alone.

The principal should not be responsible for manually decomposing the work, finding tools, wiring APIs, assigning sub-agents, or tracking progress.

### Executive Director Agent

The operator. Owns:

- Interviewing the user.
- Creating the mission draft.
- Mapping the mission to capabilities.
- Finding setup gaps.
- Asking for approvals.
- Building the loop portfolio.
- Assigning sub-agents.
- Supervising execution.
- Reviewing progress.
- Updating schedules, budgets, runners, and capabilities.
- Producing executive reports.

### Harness

The deterministic runtime. Owns:

- Mission state.
- Main-loop sessions.
- Draft validation.
- Capability registry.
- Readiness checks.
- Policy gates.
- Runner execution.
- Verification.
- Schedules.
- Leases.
- Events and ledgers.
- Search and lineage.
- Cross-mission lessons.

### Loop

A bounded unit of work with a role, goal, success criteria, budget, dependencies, required capabilities, artifacts, and fitness signal.

For the product experience, loops should often be presented as departments, teams, workstreams, or specialist roles.

### Sub-Agent

A worker assigned to a loop. It receives a focused prompt, limited tools, a clear definition of done, and a requirement to return artifacts or evidence.

Sub-agents should not independently change the user's mission, approve side effects, recursively spawn unmanaged agents, or write durable memory unless explicitly allowed.

### Capability

A tool, backend, API, command, MCP server, local script, browser, remote service, or human approval path that a loop can use.

Capabilities should be searchable, describable, setup-aware, approval-aware, and evidence-aware.

## Canonical Mission Flow

```text
user states mission
  -> Executive Director Agent opens or resumes a main-loop session
  -> agent clarifies only mission-changing unknowns
  -> agent creates a structured mission draft
  -> agent searches and selects required capabilities
  -> agent builds a setup and approval plan
  -> agent runs readiness checks until blockers are clear
  -> agent shows the operating plan for confirmation
  -> user confirms mission and required approvals
  -> harness creates durable mission state
  -> agent creates the first loop portfolio
  -> sub-agents execute bounded loops
  -> artifacts and evidence are written
  -> reviewers score work against mission criteria
  -> agent reviews loops and replans: continue, retire, retry, adjust, or spawn
  -> agent synthesizes an executive report
  -> agent either continues autonomously or asks for a checkpoint decision
```

## North-Star Illustration: Run A Company

The drawing's company example is the north-star illustration of the pattern's ceiling, not the reference demo. A credible company simulation is the mission hardest to prove and easiest to fake: CFO and accountant agents producing plausible-looking artifacts is theater until the underlying operating loop is trusted. The reference demo is the GitHub stars mission in the next section.

User says:

```text
Run a company.
```

The Executive Director Agent should transform that into an operating structure like:

```text
Mission: Run a company

Executive Director Agent
  Finance loop
    CFO agent
    Accountant agent
    Stripe API capability
    Reporting artifacts

  Development loop
    Engineering manager agent
    Engineer agent
    GitHub capability
    Local shell / CI capability
    Product artifacts

  Marketing loop
    Marketing strategist agent
    Copywriting loop
      Writer agent
      Reviewer agent
    Image generation loop
      Image generator agent
      Reviewer agent
      GPT image generation capability
    Posting loop
      Social posting agent
      X API capability

  Operations loop
    Project manager agent
    Scheduler capability
    Status report artifacts
```

This illustration shows what the pattern must eventually support:

- Turn one vague mission into a structured organization.
- Find the questions that matter.
- Identify which tools are required.
- Separate read-only research from side-effecting actions.
- Ask for approvals before publishing, messaging, spending, or mutating external systems.
- Coordinate loops over multiple generations.
- Produce concrete artifacts and status.

## Reference Demo: Grow GitHub Stars To 1,000

The reference demo is an indefinite, measurable, real-tool mission:

```text
Demo: User talks to orchestrator; orchestrator operates an indefinite mission

Input:
  "I am Alan / Lucky Faraday. I work on Lucky Systems and Athena-related open-source projects.
   My mission is to grow cumulative GitHub stars to 1,000."

Output:
  Mission investigation
  Operating plan
  Loop / team / agent portfolio
  Capability and integration readiness report
  Scheduled monitors and recurring work
  First execution generation
  Evidence-backed artifacts
  Executive report

Constraints:
  Permissions negotiated explicitly at mission start
  Full permissions possible when granted
  Hermes reuse/fork/extraction decision made explicitly
  All meaningful config and permission changes recorded
```

This mission is the demo because it is measurable (star count), evidence-rich (issues, PRs, releases, posts, analytics), genuinely indefinite (scheduled monitoring plus recurring content and maintenance work), and runs against real tools with real permission boundaries: nothing publishes, messages, or spends without an explicit recorded grant.

## Target User Experience

The ideal user experience should feel like this:

1. The user gives a mission in plain language.
2. The agent responds with understanding and a short set of high-impact questions.
3. The agent produces an operating plan: departments, loops, agents, tools, budgets, schedule, and expected artifacts.
4. The agent identifies setup gaps: missing credentials, unavailable tools, unapproved side effects, missing runner, missing workspace, or unknown capability.
5. The user approves setup or provides credentials outside the stored mission state.
6. The agent confirms readiness.
7. The user approves the mission.
8. The agent runs work, updates the ledger, and reports only meaningful progress.
9. The user can inspect the dashboard, pause, resume, redirect, approve, or stop.
10. The agent continues through bounded scheduled runs until the mission converges or reaches a checkpoint.

## Product Surfaces Needed

### 1. Mission Intake

The system needs a polished intake flow that accepts a vague mission and turns it into a mission draft.

Required fields:

- Mission statement.
- Success criteria.
- Constraints.
- Time horizon.
- Budget.
- Autonomy level.
- Approval policy.
- Workspace.
- Schedule.
- Expected final artifacts.
- Requested or inferred capabilities.

### 2. Executive Operating Plan

Before execution, the agent should show a plan that a human can understand quickly:

- Mission summary.
- Proposed departments or loops.
- Assigned agent roles.
- Required capabilities.
- Setup gaps.
- Approval needs.
- Budget and schedule.
- Expected artifacts.
- Verification strategy.
- First checkpoint.

### 3. Capability Setup

The agent should handle capability discovery and setup planning.

It should be able to say:

- "This mission needs image generation."
- "This mission needs posting to X, which is a public publishing capability."
- "X posting is not approved."
- "The required command or environment variable is missing."
- "Here is the exact setup I need permission to apply."
- "This action requires an approval quote from you."

### 4. Readiness Gate

No real mission should run until readiness is checked.

Readiness should answer:

- Which capabilities are ready?
- Which need setup?
- Which need approval?
- Which are unknown?
- Is the runner real enough for unattended execution?
- Is the schedule valid?
- Are verification commands configured?
- Are there blockers?
- What should the operator do next?

### 5. Mission Dashboard

The user needs an inspectable view of:

- Active missions.
- Current phase.
- Departments / loops.
- Agent assignments.
- Candidate state.
- Capability status.
- Approvals.
- Budget used and remaining.
- Schedule.
- Latest artifacts.
- Latest executive report.
- Blockers and next decisions.

This can start as CLI or Markdown output, but the product direction points toward a visual dashboard.

### 6. Reports And Checkpoints

Reports should be written for the principal, not for the implementation.

Reports should include:

- What changed.
- What was learned.
- Which loops succeeded.
- Which loops failed.
- Why failures happened.
- Evidence links or artifact paths.
- Recommended next action.
- Decisions required from the user, if any.

### 7. Program Files

The project should move toward editable mission operating files:

- `program.md`: operating procedure for the mission.
- `mission.md`: mission, constraints, success criteria.
- `portfolio.md`: active departments, loops, and agents.
- `fitness.md`: scoring rubrics.
- `capabilities/`: capability cards and policy metadata.
- `ledger.jsonl`: durable event history.

These files are "organization code": the user and agents can improve how the autonomous organization behaves, while the Python harness enforces state, safety, and execution.

## Technical Direction

The current architecture should be extended rather than replaced.

Current foundations to preserve:

- `MainLoopService` as the durable session and draft service.
- `MissionDraft` as the conversational mission-scoping object.
- `MissionOrchestrator` as the generation runtime.
- `CapabilityRegistry` and configured capabilities as the tool layer.
- `mission_readiness` as the prep gate.
- `mission_configure` as the operator's audited reconfiguration path.
- `PolicyGate` and permission records for side effects and full-authority grants.
- `MissionSchedule` and scheduler ticks for long-running missions.
- `Outcome`, failure classes, and remedy hints for learning.
- `ledger.jsonl` and `events.jsonl` for accountability.

## Hermes Assessment

Hermes appears to already contain much of the lower-level agent substrate this project needs:

- `run_agent.AIAgent` provides the core model/tool conversation loop, provider routing, tool execution, session persistence, callbacks, context compression, memory integration, and fallback behavior.
- `tools/delegate_tool.py` provides sub-agent execution with isolated context, restricted toolsets, batch/parallel delegation, background delegation, child session IDs, active sub-agent tracking, pause/interrupt hooks, and an optional `role="orchestrator"` mode for bounded nested delegation.
- `toolsets.py` and `model_tools.py` provide a mature tool registry and toolset system covering terminal, files, web, browser, image generation, vision, memory, skills, session search, cron, delegation, and platform-specific bundles.
- `cron/scheduler.py` and `tools/cronjob_tools.py` provide scheduled unattended work, file locking, job-specific toolset resolution, prompt scanning, output persistence, and delivery/silence behavior.
- Hermes already has a TUI, gateway channels, webhook routines, skills, persistent memory, session search, remote terminal backends, MCP support, and broad model-provider support.

This changes the likely architecture. Multi-loop should probably not rebuild every tool and agent primitive from scratch. The better question is how to compose with Hermes:

1. **Use Hermes as the execution substrate.** Multi-loop remains the mission brain and calls Hermes agents/sub-agents/cron/tools underneath.
2. **Fork Hermes.** The mission product evolves from Hermes directly, adding the higher-level orchestrator, mission ledger, loop model, and dashboard.
3. **Extract a shared agent layer.** Hermes keeps its product surfaces, while the reusable agent runtime becomes a library consumed by multi-loop.

Current bias: start by treating Hermes as the execution substrate and multi-loop as the mission orchestration brain. Forking may be justified later if integration friction is higher than expected, but the first design pass should avoid duplicating Hermes' already-working providers, tools, cron, TUI, memory, skills, gateway, and sub-agent machinery.

## How Multi-Loop Connects To Hermes

The connection should be through a `HermesRuntimeAdapter` owned by multi-loop.

Hermes should not be the user-facing mission brain. The user speaks to the multi-loop orchestrator. The orchestrator owns the mission, loops, permission contract, ledger, schedules, reports, and strategic decisions. Hermes sits behind it as the execution engine.

```text
User channel
  TUI / Hermes chat / web / Telegram later
        |
        v
multi-loop Orchestrator
  owns mission, loops, permissions, ledger, schedules, reports
        |
        v
HermesRuntimeAdapter
  translates loop work into Hermes agent runs
        |
        v
Hermes
  AIAgent, tools, sub-agents, cron, browser, GitHub, terminal, memory, skills
```

MCP should not be the main bridge. Hermes' MCP surface is useful for some tools, but core Hermes capabilities like `delegate_task` need a running `AIAgent` context. The primary bridge should be Hermes' own agent/runtime APIs and commands.

### Stage 1: Subprocess Bridge

Start with Hermes one-shot mode for isolated worker runs:

```text
multi-loop -> hermes --oneshot "<worker prompt>" --toolsets coding,web,browser
```

Hermes one-shot runs one prompt with the user's configured model, provider, tools, memory, context files, and skill system, then returns the final response on stdout. Multi-loop captures the output, exit status, artifacts, and evidence into the mission ledger.

This is the fastest safe integration because Hermes remains process-isolated and multi-loop does not need to import Hermes internals on day one.

### Stage 2: Python Runtime Adapter

After the subprocess bridge proves the mission loop, add a direct Python adapter for richer control:

```python
from run_agent import AIAgent

agent = AIAgent(
    model=model,
    provider=provider,
    enabled_toolsets=["terminal", "file", "web", "browser", "delegation"],
    session_id=session_id,
)

result = agent.run_conversation(prompt)
```

This lets multi-loop observe callbacks, session IDs, tool progress, sub-agent events, token/cost usage, and richer run status.

### Stage 3: Schedule Bridge

For always-on missions, multi-loop should create or control Hermes scheduled jobs where appropriate:

```text
multi-loop mission schedule
  -> Hermes cron job
  -> Hermes agent run
  -> result back into multi-loop ledger/report
```

Example recurring mission work:

```text
Every morning:
  inspect GitHub stars, issues, PRs, releases, and repo health
  identify growth opportunities
  create or update work loops
  report only meaningful changes
```

Hermes cron is a strong substrate for scheduled checks, but multi-loop should still own the mission-level decision about why a job exists, what success means, how the output is scored, and whether the mission should continue, mutate, or pause.

### Stage 4: Deeper Integration Decision

After a real always-on mission runs through Hermes, decide from evidence:

1. Keep Hermes wrapped as an execution substrate.
2. Fork Hermes and evolve it into the mission product.
3. Extract a reusable Hermes agent-runtime library and consume it from multi-loop.

Do not fork or extract before the adapter reveals the real seams.

### Adapter Contract

The Stage 1 adapter exposes a deliberately tiny contract:

```python
class HermesRuntimeAdapter:
    def run_agent(self, prompt, toolsets, workspace, permissions): ...
    def collect_artifacts(self, run_id): ...
```

That is the entire Stage 1 contract. `run_loop`, `spawn_subagents`, `schedule_job`, `list_toolsets`, `inspect_sessions`, and `interrupt` are candidates that get added only when the stage that needs them arrives. Designing the full interface before the subprocess bridge has run anything is the same premature-commitment mistake as forking Hermes early, one layer up.

The user should never need to think about Hermes directly. The orchestrator says, for example, "I need a GitHub review loop, a marketing loop, and a daily monitor," then uses Hermes behind the scenes to execute those agents and tools.

Likely additions:

- A mission organization projection that can show departments, loops, teams, or agent roles depending on the mission.
- A role taxonomy for agents, such as executive, manager, specialist, reviewer, tool-runner, and reporter.
- A mission template system for common scaffolds, without making templates mandatory.
- A richer operating-plan artifact.
- A dashboard/status projection optimized for vibecoders, with TUI as a serious near-term candidate.
- A stronger capability setup wizard.
- A report generator that converts low-level state into executive summaries.
- Better loop concurrency once leases and workspace isolation are ready.
- Workspace strategy for selected artifacts, worktrees, and generated assets.
- An explicit agent-runtime decision: continue MCP-hosted execution, build a native agent layer, or support both behind a common interface.

## MVP Direction

The next MVP should be:

```text
Autonomous mission operator that can run an indefinite user mission through loops and sub-agents.
```

The MVP does not need to solve every mission type. It needs to convincingly demonstrate that a broad user request can become an operating mission that continues over time:

- The user gives the mission.
- The orchestrator investigates the user's context, goals, assets, accounts, tools, and constraints.
- The orchestrator asks high-impact questions.
- The orchestrator negotiates scope, permissions, and tool authority.
- The orchestrator creates an operating plan.
- The orchestrator creates the needed loops, teams, or specialist agents.
- The system maps each loop to capabilities.
- The system identifies missing setup, configuration, tools, and permission gaps.
- The system runs a first generation through the selected agent/tool backend.
- The system writes artifacts.
- The system reviews and synthesizes progress.
- The system schedules or resumes ongoing work when the mission is indefinite.
- The system produces useful artifacts, operational changes, and executive reports.

### MVP Included

- Mission intake for broad user missions.
- Operating plan output.
- Loop, team, department, or agent portfolio generation.
- Capability mapping.
- Readiness report.
- Permission-aware execution.
- First-generation run.
- Scheduled/always-on continuation path.
- Artifact writing.
- Executive report.
- CLI, Markdown, or TUI-first status view.

### MVP Excluded

- Silent spending without a negotiated permission grant.
- Silent public publishing without a negotiated permission grant.
- Silent messaging to real people without a negotiated permission grant.
- Fully automated legal, accounting, or financial authority.
- Unlimited recursive sub-agent spawning.
- A production-grade GUI, unless the immediate goal changes.

## Milestones

### Milestone 1: Direction Lock

Output:

- This direction document reviewed.
- The four blocking questions answered.
- Canonical terminology chosen.
- MVP mission confirmed: the GitHub stars reference demo.

### Milestone 2: Real-Tool Indefinite Mission Demo

The demo moves ahead of plan artifacts, reports, and dashboards: one real end-to-end run answers more open questions than deliberation will, and every later surface is better for having real output to render.

Output:

- Stage 1 Hermes subprocess bridge working.
- The stars mission running its first generation with at least one real capability (GitHub).
- Side effects blocked until approved; approvals recorded in the permission ledger.
- Evidence-backed artifacts verified to exist.
- A scheduled continuation path for the mission.
- Default operating-plan shape, agent roles, capability needs, readiness checks, and success criteria discovered from the run rather than designed up front.

### Milestone 3: Executive Report

Output:

- User-facing report generated after each generation, rendered from real run output.
- Report summarizes status, evidence, decisions, risks, and next actions.
- Report avoids raw implementation noise unless requested.

### Milestone 4: Operating Plan Artifact

Output:

- Durable operating plan model or Markdown artifact, formalizing what Milestone 2 improvised.
- Plan includes departments, loops, roles, tools, approvals, budgets, schedule, and expected artifacts.
- Plan is shown before mission confirmation.

### Milestone 5: Better Status Surface

Output:

- A mission status view that reads like an organization dashboard.
- Shows mission phase, departments, active loops, blockers, capabilities, approvals, artifacts, and schedule.

## Definition Of Done For The Direction

This direction is implemented well when:

- A nontechnical user can state a broad mission without decomposing it.
- The agent can explain the mission back clearly.
- The agent asks a small number of high-value questions instead of interrogating the user endlessly.
- The agent produces a credible operating plan.
- Tool setup gaps are explicit and actionable.
- Side effects are blocked until approved.
- The first generation can run without the user managing sub-tasks.
- Results include artifacts and evidence.
- The user can inspect progress without reading raw logs.
- The system can pause, resume, and continue through scheduled bounded runs.

## Blocking Questions

The full question inventory from earlier drafts (the 70 pinpointing questions, the 10 retained first-draft questions, and the 30 Round 2 questions) is archived in `direction-question-archive.md` for traceability. Most of it is answerable by defaults or by building the reference demo. Only these block the demo:

1. **Interface.** Is the first surface CLI/Markdown status output or a minimal TUI? Default if undecided: CLI/Markdown now, TUI once the demo works.
2. **Default permission scope.** What does the stars mission get without asking? Default: read-only GitHub/web plus local workspace writes; every external side effect (posting, messaging, spending) gated behind an explicit recorded grant.
3. **Generations and fitness.** Does the evolutionary candidate model survive the pivot, or does an indefinite mission run as stable scheduled loops plus review-driven replanning, with generations reserved for bounded experiments? Working hypothesis: the latter; the demo confirms or kills it.
4. **Stopping rule.** What ends or pauses an indefinite mission? Default: target metric reached, budget expired, or user stop; the agent recommends, the user decides.

## Decisions Made

Answered on July 1, 2026 (first review):

- "Executive Director Agent" is internal language.
- The product is mission-general; "Run a company" is one possible example.
- The first strong output should be a working scaffold.
- The first target user is the vibecoder.
- A TUI may be the right first interface, but this needs discussion.
- Tools, capabilities, departments, and permissions should be negotiated by the user and lead agent per mission.
- Full permissions must be possible when granted by the user.
- The product may move away from MCP and own the agent layer.

Answered on July 1, 2026 (second review):

- The reference demo is the GitHub stars mission; "Run a company" is demoted to a north-star illustration.
- Departments are a presentation layer over loops, not a persisted org model.
- A visible permission ledger is in scope from the start: every grant, use, and revocation of authority is recorded and inspectable.
- The Stage 1 Hermes adapter contract is `run_agent` plus `collect_artifacts` only; further methods are added when the stage that needs them arrives.
- The real-tool demo runs before the plan-artifact, report, and dashboard milestones.
- The full question inventory is archived in `direction-question-archive.md`; only the blocking questions remain in this document.

## Immediate Next Step

Answer the four blocking questions, then create a scoped implementation plan for the reference demo: Stage 1 subprocess bridge to Hermes, the stars mission's first generation against real GitHub state, side effects gated and recorded in the permission ledger, evidence-backed artifacts, one executive report, and a scheduled continuation path.

That plan can then be translated into concrete changes in the Python models, planner, main-loop prompt, capability bundles, agent-runtime layer, CLI status output, and tests.
