# Direction Question Archive

Archived July 1, 2026, during the second review of
`executive-director-agent-direction.md`. Most of these questions are answerable
by defaults or will be answered by building the reference demo (grow GitHub
stars to 1,000); keeping the full inventory in the direction document preserved
optionality instead of reducing it. They are kept here for traceability.

The four questions that actually block the demo live in the direction document
under "Blocking Questions". Some questions below have since been answered — see
"Decisions Made" in the direction document (notably: departments are a
presentation layer, the permission ledger is in scope, the stars mission is the
reference demo).

## First-Round Questions To Pinpoint The Product

### Product Identity

1. Should the product be called "Executive Director Agent," "Operator," "Multi-Loop," or something else?
2. Is "Executive Director Agent" the user-facing name or only the internal architecture metaphor?
3. Should the product feel like a CEO, chief of staff, project manager, automation platform, autonomous company, or agent OS?
4. What is the strongest one-line promise you want users to remember?
5. Should the product sell "hands-off execution," "agent orchestration," "autonomous organizations," or "missions that run themselves"?

### Target User

6. Who is the first user: you, founders, developers, creators, marketers, agencies, operators, or enterprises?
7. Is the first user technical enough to use CLI/MCP tools?
8. Should the first version assume the user has local agent tools like Codex, Claude Code, or OpenCode installed?
9. Should the product eventually be usable by a nontechnical person in a web app?
10. Does the user want detailed control, or should the product hide most operational details until asked?

### Mission Scope

11. What kinds of missions should be first-class: run a company, launch a SaaS, run marketing, conduct research, build software, manage social content, or something else?
12. Is "Run a company" meant as a real operating mission or a demo metaphor?
13. Should the first demo create a business plan, a working product, a marketing campaign, or a whole operating scaffold?
14. What does success mean for a broad mission: artifact delivered, revenue generated, user-approved plan, deployed system, or ongoing operations?
15. How long should a mission be expected to run: minutes, hours, days, weeks, or indefinitely?

### User Involvement

16. What should the system always ask the user before doing?
17. What should the system never ask the user unless blocked?
18. How many questions is acceptable during intake before the user feels it is no longer hands-off?
19. Should the agent batch questions, or ask progressively only when needed?
20. Should the user approve the full operating plan before execution every time?

### Autonomy And Safety

21. What autonomy levels should exist?
22. Should the default be read-only, local-write, or approved external-write?
23. Should the system be allowed to create files locally without asking?
24. Should it be allowed to install packages or configure local commands?
25. Should it be allowed to browse the web automatically?
26. Should it be allowed to spend money only per action, per budget, or never in the MVP?
27. Should it be allowed to publish posts only after per-post approval, campaign approval, or account-level approval?
28. Should approvals expire?
29. Should approvals be scoped by mission, capability, dollar amount, account, time window, or artifact?
30. What is the highest-risk side effect the MVP must handle safely?

### Organization Model

31. Should loops be presented to users as departments, workstreams, teams, experiments, or agents?
32. Should a department contain many loops, or is a department just a named loop?
33. Should there be manager agents above specialist agents?
34. Should each department have a reviewer by default?
35. Should the Executive Director Agent be allowed to create new departments during execution?
36. Should the user be able to edit the org chart directly?
37. Should loops have persistent memory across generations?
38. Should failed loops be retired, retried, mutated, or converted into setup tasks?

### Tools And Capabilities

39. Which tools must be in the first real demo?
40. Do you want Stripe API, X API, GPT image generation, GitHub, browser automation, email, databases, or local shell first?
41. Should capabilities be configured through MCP tools, CLI commands, environment variables, or a UI?
42. Should the agent be allowed to create custom capabilities from user-approved shell commands?
43. Should capabilities be grouped into bundles such as company, marketing, development, finance, and research?
44. How should the system handle a required tool that is unavailable?
45. Should tool setup be interactive, or should the agent generate a checklist?

### Execution Model

46. Should the first implementation run candidate loops sequentially or prioritize parallel execution?
47. Should each sub-agent run in an isolated workspace or shared mission workspace?
48. Should software-building loops use separate git worktrees?
49. Should selected artifacts be merged into a canonical mission workspace?
50. Should the Executive Director Agent directly run tools, or should all tool use happen through sub-agents?
51. Should reviewers be separate agents or deterministic checks where possible?
52. How should the system decide when a mission has converged?
53. Should the user be able to force another generation?

### Reporting And UX

54. What should the main screen show?
55. Do you want a CLI-first product, MCP-first product, web dashboard, or all three?
56. Should reports look like board reports, project updates, task lists, or research memos?
57. How much raw detail should be hidden by default?
58. Should every artifact have a visible owner, loop, score, and evidence link?
59. Should the user see an org chart of agents and tools?
60. Should the product show live activity, or only periodic summaries?

### Memory And Learning

61. Should lessons learned in one mission automatically influence future missions?
62. Should the user be able to approve or delete learned lessons?
63. Should the system remember user preferences globally?
64. Should mission templates evolve from successful missions?
65. Should failures become reusable pitfall warnings?

### Business And Packaging

66. Is this primarily a local developer tool, an MCP server, a hosted app, or a framework?
67. Should the repo focus on library quality, product demo quality, or both?
68. What is the first "wow" demo that would prove this direction?
69. Who would pay for this, and what outcome would they pay for?
70. What should be intentionally out of scope for the next 30 days?

## Retained First-Draft Questions

1. What is the first canonical mission: "Run a company," "Launch a SaaS," or something narrower?
2. Who is the first user persona?
3. What should the first demo produce as its final artifact?
4. Which real tools must be included in the first demo?
5. Should the first status surface be CLI/Markdown or a web dashboard?
6. What autonomy level should be the default?
7. What side effects must be blocked until explicit approval?
8. Should departments be a real data model now, or just a presentation layer over candidate loops?
9. Should sub-agents execute through MCP host tools, CLI agent commands, or both?
10. What would make you say: "Yes, this is the Executive Director Agent I meant"?

## Round 2 Questions

These were the next questions to answer before implementation planning. The
blocking subset (interface default, permission default, generations/fitness,
stopping rule) was promoted into the direction document; the first-mission
question was answered by choosing the stars demo.

### A. First Indefinite Mission

1. What is the best first indefinite mission: grow Lucky Systems to 1,000 GitHub stars, turn `multi-loop` into a monetizable product, run GitHub maintenance, run marketing, or something else?
2. What should the orchestrator be expected to do in week one of that mission?
3. Which work should run once, which should run on a schedule, and which should run in response to events?
4. What artifacts prove the mission is operating: reports, issues, PRs, posts, dashboards, analytics, or changed repository state?
5. What does "mission progress" mean for the first demo: stars gained, content shipped, issues triaged, PRs reviewed, website published, leads generated, or another metric?

### B. TUI Direction

6. What should the TUI primarily be: mission cockpit, chat console, org chart, task runner, logs viewer, or all-in-one workspace?
7. Should the TUI be the main product surface, or just a power-user monitor for missions running elsewhere?
8. What needs to be visible at all times: mission state, active agents, permissions, tools, artifacts, logs, budget, or next decisions?
9. Should the TUI allow direct control actions like pause, resume, approve permission, edit plan, rerun loop, and kill agent?
10. Should the TUI feel like a developer tool, an operations dashboard, or an agent command center?

### C. Hermes And Native Agent Layer

11. Should multi-loop sit above Hermes and call Hermes as the execution substrate?
12. Should we fork Hermes and evolve it into the mission product?
13. Should we extract Hermes' agent/runtime pieces into a reusable library and keep multi-loop as the mission brain?
14. Which Hermes pieces are must-reuse: provider routing, tools, TUI, gateway channels, cron, skills, memory, sub-agents, terminal/browser backends, or all of them?
15. What should remain deterministic in multi-loop even if Hermes provides the agent execution layer?

### D. Permission Contract

16. When the user grants "full permissions," what exact things are included: filesystem, shell, package install, network, browser, credentials, posting, spending, messaging, deploys?
17. Should full permissions be scoped to one mission, one workspace, one time window, or globally?
18. Should the agent be allowed to change configuration without asking if it already has full permission?
19. Which actions still require an interrupt even in full-permission mode?
20. Should the product keep a visible permission ledger so the user can see what authority was granted and used?

### E. Orchestration Model

21. Should departments/loops/agents be persisted as structured data, or generated as a view from the current mission plan?
22. Should the orchestrator create the org shape before execution, or evolve it continuously as work reveals new needs?
23. Should loops be genetic experiments, project tasks, durable departments, scheduled monitors, event responders, or a hybrid?
24. Should the lead agent be allowed to spawn new sub-agents without another user checkpoint?
25. What is the stopping rule for indefinite missions: target metric reached, user stops it, budget expires, mission changes, or the agent recommends completion?

### F. Product Boundary

26. Is this a standalone product, a local daemon plus TUI, a Python framework, a Codex/Claude companion, or eventually a hosted app?
27. Should this repo remain the product, or should `multi-loop` become the backend for a new app layer?
28. Should the first milestone prioritize architecture correctness or a compelling demo?
29. What should be removed or de-emphasized from the current repo if MCP is likely not the long-term center?
30. What would be a credible two-week milestone from the current state?
