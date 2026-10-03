# AGENTS.md — universal entry point (Claude Code, Codex, Kiro, Antigravity, any other agent)

You are one of several AI agents working on ONE repo at the same time (Claude = manager/reviewer; Codex, Antigravity, Kiro = builders). Read this file fully before doing anything.
`CLAUDE.md`, `GEMINI.md` and `.kiro/steering/` are only pointers to this file. This file is the single source of truth for how to behave.

## Project in one paragraph
**Hulchul Job-Apply Operator** — a LangGraph-based "computer operator" that takes a plain-English goal
("apply to the 3 best-fit roles under my rules"), reads mutable candidate data from Google Drive, operates a real
browser on application forms it has never seen, fills them, verifies what it wrote, then **pauses in the exact browser
state** and messages the user a review summary plus a clickable approve link. Only after approval does it submit,
and then it verifies the submission. It never solves CAPTCHAs; it hands over to the human and resumes.
Deadline: **4 Oct 2026, 11:59 PM IST**. Hard budget: **12 focused hours**. Graded on usefulness, execution,
reliability, engineering judgment, and ownership (see `docs/01-brief/ASSIGNMENT_BRIEF.md`).

## Step 0 — Identify yourself (self-awareness)
1. Determine which agent you are (Claude Code / Codex / Kiro / Antigravity / other). Your tool or launcher tells you.
2. Find your row in `docs/07-agents/AGENT_REGISTRY.md`. If it is missing, add it (append-only) before any other write.
3. Your identity string `AGENT_ID` (e.g. `claude`, `codex`, `kiro`, `antigravity`) is used in branch names, commit prefixes and task claims.
4. Check what you do NOT know: if the task needs a fact absent from `docs/`, ask the user; do not assume.

## Step 1 — Read order (always, in this order; stop when you have what you need)
1. `AGENTS.md` (this file)
2. `docs/00-INDEX.md` — map of everything
3. `docs/04-decisions/DECISION_LOG.md` — what is LOCKED. Do not re-litigate locked decisions.
4. `docs/07-agents/TASK_BOARD.md` — find your task, its dependencies, its "blast radius" class
5. `docs/07-agents/OWNERSHIP_MAP.md` — which paths you may write
6. The ONE design doc your task touches (PRD / ARCHITECTURE / AGENT_GRAPH / COMMUNICATION_MATRIX / POLICY_AND_SAFETY / DATA_SOURCES)
7. `docs/05-testing/SPIKE_REPORT.md` only if your task depends on a measured fact

## Step 2 — Write rules (what you may change)
| You may | Where |
|---|---|
| Edit code | Only inside the paths your task owns in `OWNERSHIP_MAP.md` |
| Append | `docs/07-agents/AGENT_REGISTRY.md`, `docs/05-testing/SPIKE_REPORT.md` (new dated section), your task's notes on `TASK_BOARD.md` |
| Propose a decision change | New file in `docs/04-decisions/PROPOSALS/NNN-title.md`. NEVER edit `DECISION_LOG.md` locked rows yourself — the integrator/user does. |
| Change a shared contract (`src/operator/contracts/**`) | Only if your task is class **B2/B3** and you hold the contract lease; needs a PROPOSAL first |
| Never touch | Another agent's owned paths, `.env*`, any credential, `docs/04-decisions/DECISION_LOG.md` locked rows |

## Step 3 — Git protocol (user will `git init`; remote on GitHub)
- `main` is protected by convention: **no direct commits by agents, and workers never merge.** Claude (Lead/manager) reviews, asks the user for explicit approval per merge, then merges (D-026).
- One branch per task: `agent/<AGENT_ID>/<TASK_ID>-<slug>` (e.g. `agent/codex/T-014-extractor-v2`).
- Prefer one **git worktree** per agent: `git worktree add ../wt-<AGENT_ID> -b agent/<AGENT_ID>/<TASK_ID>`. Never share a working directory with another agent.
- Commit prefix: `[<AGENT_ID>][<TASK_ID>] type: message`. Small commits. Do not rewrite shared history.
- Before starting: `git fetch && git rebase origin/main`. Before finishing: rebase again, run tests, open a PR (or tell the user the branch is ready).
- If you hit a merge conflict in a file you do not own: STOP and report; do not "resolve" another agent's work.

## Step 4 — Parallel-work safety (blast radius)
Each task has a class. Two tasks may run in parallel only if the matrix in `docs/07-agents/AGENT_PROTOCOL.md` allows it.
- **B0 isolated**: tests, fixtures, docs, assets — parallel with anything.
- **B1 module-internal**: change inside one module, public interface unchanged — parallel with other modules.
- **B2 interface/contract**: changes a function signature, Pydantic model, event, or HTTP route others use — serial; announce first.
- **B3 core**: graph topology, state schema, ledger schema, policy rules — one agent at a time, user-approved.

## Step 5 — Non-negotiables (violating these is a failed task)
1. **Never submit a real job application.** Real employer forms are filled up to the approval gate only. Submit runs only on our self-hosted fixtures. (D-010, D-014)
2. **Never solve, bypass or automate a CAPTCHA.** Detect → pause → hand to human. (D-005)
3. **No secrets in the repo.** Keys come from environment variables. `.env` is git-ignored. Synthetic data only. (D-009)
4. **The LLM proposes; deterministic code disposes.** Safety, approval, idempotency and verification are never delegated to a model. (D-004)
5. **GET requests never change state.** Approve/submit is a POST behind a signed single-use token. (D-008)
6. **Do not assume — test.** If a decision rests on an unmeasured claim, add a spike to `docs/05-testing/SPIKE_BACKLOG.md` and run it before building on it.
7. **Disclose AI assistance.** Log what you generated in your task notes; the engineering note needs it.

## Step 6 — Definition of done for any task
- [ ] Code inside owned paths only; types and docstrings on public functions
- [ ] A runnable check exists (pytest test or an `evals/` case) and passes
- [ ] No secret, no real personal data, no real employer submission
- [ ] `TASK_BOARD.md` notes updated: what changed, what was verified, what is left
- [ ] If you measured something new, appended to `SPIKE_REPORT.md`

## Quick facts (verified on this machine, 2026-10-03)
Windows 11, Python 3.13.15, Node 24.19, Playwright (Python) 1.63.0, LangGraph 1.2.12, langgraph-checkpoint-sqlite 3.1.1,
system Chrome at `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe` (use `channel="chrome"`; Playwright's bundled Chromium is NOT installed).
Gemini via Vertex project `ai-negotiation-copilot` (location `global`). No Anthropic API key on this machine.

## Config and secrets (single copy)
The only `.env` is `C:\Balaastra\hulchul-operator\.env` (git-ignored). Worktrees do NOT get a copy; load it with `python-dotenv` using the path in `ENV_FILE`, or `load_dotenv(r"C:\Balaastra\hulchul-operator\.env")`. Names only: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`, `LLM_PROVIDER=vertex`, `LLM_MODEL=gemini-2.5-flash`, `TELEGRAM_BOT_TOKEN`. Vertex auth = application-default credentials (works on this machine; no key file). Never print, log, copy or commit values from it.
