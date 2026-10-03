# Agent protocol — how four agents share one repo without colliding

Applies equally to Claude Code, Codex, Kiro, Antigravity. Entry: `AGENTS.md`. Assignments are decided by the user AFTER reviewing these docs (D-011); until then every owner cell is `UNASSIGNED`.

## 1. Self-awareness checklist (each agent, each session)
1. Who am I? Set `AGENT_ID`. Confirm my row in `AGENT_REGISTRY.md`.
2. What is my task? Read my row in `TASK_BOARD.md` (class B0–B3, dependencies, owned paths).
3. What may I write? Only paths in `OWNERSHIP_MAP.md` for my task. Everything else is read-only to me.
4. What can break others if I change it? Check "Consumers" in OWNERSHIP_MAP; if non-empty and my change alters an interface → B2: stop, file a PROPOSAL, wait.
5. What is my remaining capacity? Note quota/context limits in the registry row; hand off at ≤15 % rather than leaving half-written code (use the bus `handoff` if available).
6. What do I NOT know? List unknowns in task notes and ask the user; never guess a locked decision.

## 2. Blast-radius classes and parallelism
| Class | Meaning | Examples | Parallel with |
|---|---|---|---|
| B0 | isolated, nobody imports it | fixtures, tests, evals, docs, assets | everything |
| B1 | module-internal, public interface unchanged | extractor internals, Telegram adapter, a prompt | other B0/B1 in different modules |
| B2 | changes an interface others use | contract field, route shape, function signature, event schema | nothing in its consumers; announce, then serial |
| B3 | core behaviour | graph topology, `RunState`, ledger schema, policy rules | nothing; one agent, user-approved |

**Matrix** (rows = running task, cols = new task; ✓ parallel OK, ✗ serial):
| | B0 | B1 same module | B1 other module | B2 | B3 |
|---|---|---|---|---|---|
| B0 | ✓ | ✓ | ✓ | ✓ | ✓ |
| B1 same module | ✓ | ✗ | ✓ | ✗ | ✗ |
| B1 other module | ✓ | ✓ | ✓ | ✗ if consumer | ✗ |
| B2 | ✓ | ✗ if consumer | ✗ if consumer | ✗ | ✗ |
| B3 | ✓ | ✗ | ✗ | ✗ | ✗ |

## 3. Git workflow
- User runs `git init`, sets remote, creates `main` with this docs tree committed.
- Branch: `agent/<AGENT_ID>/<TASK_ID>-<slug>`; worktree per agent: `git worktree add ../wt-<AGENT_ID> -b <branch>`.
- Never commit to `main`; never force-push shared branches; never rebase another agent's branch.
- PR (or "branch ready" message) includes: task id, class, files touched, tests run, spike evidence, AI-assistance note.
- Integrator (user or one designated agent) merges in dependency order and resolves cross-module conflicts. Merge order follows `TASK_BOARD.md` dependencies.
- Contracts (`src/operator/contracts/**`) merge first, always.

## 4. Contract lease
Only one agent holds the contract lease at a time (write the holder in `OWNERSHIP_MAP.md` row `contracts`). Others pin to the frozen version via type imports; a change requires PROPOSAL → user ack → lease → merge → everyone rebases.

## 5. Handoff
When pausing or quota is low: update the task row (done / not done / next step / branch / failing test), commit WIP on your branch, push. The receiving agent must read the task row and the branch diff before touching anything.

## 6. Communication between agents
Through files only: `TASK_BOARD.md` notes, `PROPOSALS/`, `OPEN_QUESTIONS.md`. If the Bala Agent Bus is healthy, also use `busctl claim/finish/handoff` and file leases; leases are advisory. (Note: bus task creation failed on 2026-10-03 — beads backend error; do not block on it.)

## 7. Review rules
No agent merges its own B2/B3 change. B1/B0 may be self-merged after tests pass if no consumer exists. Every PR gets one cross-agent review pass on safety rules (AGENTS.md §5).
