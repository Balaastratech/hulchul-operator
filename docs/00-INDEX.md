# Documentation index

Status legend used everywhere: **LOCKED-TESTED** (measured), **LOCKED-CHOICE** (user decided, not yet measured), **LOCKED-POLICY** (rule), **OPEN** (needs user), **PENDING-SPIKE** (will be tested before build).

| Folder / file | What it answers | Writers |
|---|---|---|
| `../AGENTS.md` | How any agent behaves here | user/integrator |
| `01-brief/ASSIGNMENT_BRIEF.md` | The Hulchul brief as numbered requirements (R1–R5, D1–D3) | user |
| `01-brief/COMPANY_CONTEXT.md` | Pointer + key takeaways about Hulchul / Raj | user |
| `02-prd/PRD.md` | What we build, for whom, scope, non-goals, acceptance criteria | integrator |
| `03-architecture/ARCHITECTURE.md` | Components, ports/adapters, repo layout, deployment split | integrator |
| `03-architecture/AGENT_GRAPH.md` | The LangGraph agents/nodes, state, edges, contracts | integrator |
| `03-architecture/COMMUNICATION_MATRIX.md` | Every message the system sends the human: event → channel → link → response | integrator |
| `03-architecture/POLICY_AND_SAFETY.md` | Authority tiers, injection quarantine, idempotency, forbidden actions | integrator |
| `03-architecture/DATA_SOURCES.md` | Google Drive as mutable source of truth: files, schemas, refresh rules | integrator |
| `04-decisions/DECISION_LOG.md` | Every decision, status, evidence | user/integrator only |
| `04-decisions/OPEN_QUESTIONS.md` | Questions for the user, with options | anyone appends |
| `04-decisions/PROPOSALS/` | Proposed changes to locked decisions | any agent |
| `05-testing/SPIKE_REPORT.md` | Measured facts so far (append-only) | any agent |
| `05-testing/SPIKE_BACKLOG.md` | Tests that must run BEFORE building on an assumption | any agent |
| `05-testing/TEST_PLAN.md` | Unit / integration / eval / demo-rehearsal plan, with pass thresholds | any agent |
| `06-roadmap/ROADMAP.md` | 12-hour plan with go/no-go gates and a cut list | integrator |
| `06-roadmap/FUTURE_SCOPE.md` | What a production version adds (shown to the employer, NOT built in 12h) | user |
| `07-agents/AGENT_PROTOCOL.md` | Git, ownership, blast radius, parallelism rules, handoff | integrator |
| `07-agents/AGENT_REGISTRY.md` | Who each agent is, capabilities, quota notes | agents append |
| `07-agents/OWNERSHIP_MAP.md` | Module → path → blast radius → owner | user |
| `07-agents/BUS_TASK_MAP.md` | Board task ID -> Bala Agent Bus ID (for `busctl claim`) | Claude |
| `07-agents/START_HERE.md` | The user's ordered start steps and the exact message to paste into each agent | user |
| `07-agents/ASSIGNMENT_PROPOSAL.md` | Proposed hierarchy, lanes, waves, parallel vs serial (PROPOSED, user decides) | user |
| `07-agents/BUS_RUNBOOK.md` | Bala Agent Bus setup and per-agent loop for this repo | integrator |
| `07-agents/KICKOFF_PROMPTS.md` | Paste-ready first prompt per agent | integrator |
| `07-agents/TASK_BOARD.md` | Task list, dependencies, owner, status | agents update own rows |
| `08-submission/SUBMISSION_CHECKLIST.md` | Repo, setup, video script (≤5 min), engineering note outline, reply to Hulchul | user/integrator |

Existing context file (kept in place): `../private/HULCHUL_COMPANY_RESEARCH_AND_ASSIGNMENT_ALIGNMENT.md` (local-only, git-ignored). Brief PDF: `../private/Hulchul-AI-Engineering-Assignment.pdf` (local-only).
Spike code and raw results: `../research/spikes/`.
