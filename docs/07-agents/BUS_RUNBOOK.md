# Bala Agent Bus runbook for this project

Source: `C:\Balaastra\automation-agent\agent bus\docs\USAGE.md` + `busctl --help`, verified 2026-10-03. The bus gives tasks (Beads), advisory file leases, shared memory (Claude-Mem), Agent Mail messaging, and real quota limits. **Git and the filesystem remain the source of truth; leases are advisory** — worktrees + ownership map are the real collision protection.

## 0. One-time setup (user, ~15 min)
```bash
cd "C:/Balaastra/hulchul-operator"      # D-027
git init -b main
git add . && git commit -m "docs: planning set, spikes, decisions"
# create PRIVATE GitHub repo (OQ-06, D-022), then:
git remote add origin <repo-url> && git push -u origin main
busctl boot            # starts Gateway, MCP Agent Mail (was OFFLINE), Claude-Mem
busctl doctor          # expect no WARN for Agent Mail
busctl install         # only if hooks/MCP are not installed for an agent (check doctor output)
```
Why: task creation failed earlier ("Failed to create Beads task") because the folder was not a git repo. Re-test after `git init`: `busctl create "bus smoke test" --claim claude --paths "docs/**"` then `busctl finish <id> --test "echo ok"`.

## 1. Worktrees (one per agent; no shared working directory)
```bash
git worktree add ../wt-claude       -b agent/claude/T-001-contracts
git worktree add ../wt-codex        -b agent/codex/T-011-browser-core
git worktree add ../wt-kiro         -b agent/kiro/T-021-control-plane
git worktree add ../wt-antigravity  -b agent/antigravity/T-010-fixtures
```
Open each agent in its own worktree folder. The bus derives project identity from the git remote, so all worktrees map to the same bus project.

## 2. Seed bus tasks from the TASK_BOARD (Lead runs once)
One bus task per `T-xxx`, with the T-ID first in the title so board and bus can be cross-referenced. Unclaimed at creation:
```bash
busctl create "T-001 contracts v0.1" -d "docs/07-agents/TASK_BOARD.md#T-001" -p 0
busctl create "T-002 ledger" -d "..." -p 1
# ...repeat for T-003..T-027
```

## 3. Per-agent loop
| Moment | Command | Notes |
|---|---|---|
| Session start | `busctl context --agent <id>` (or MCP `bus_context`) | hooks usually inject it; shows task, peers, **your limits** |
| Pick work | `busctl route "<task>"` | ranks agents by quota headroom + fit; Lead may override |
| Claim | `busctl claim <bus-id> --agent <id> --paths "<owned globs from OWNERSHIP_MAP>"` | conflict = another agent holds those paths → pick other work or wait; never override |
| While working | commit on your `agent/...` branch; message peers with MCP `bus_message_send` (Agent Mail) for blocking questions | files remain the durable record |
| Finish | `busctl finish <bus-id> --test "<pytest command>" --reason "<decision + what changed>"` | the reason is searchable team memory: state the *decision* |
| Low quota (≤15 %) | `busctl handoff <bus-id> --from <id> --to <id2> --reason "..." --summary "..." --paths "..."` | commit WIP first |
| Status | `busctl status`, `busctl summary`, `busctl timeline` | Lead checks hourly |

## 4. Bus ↔ docs mapping
| Concept | Bus | Docs |
|---|---|---|
| Task | Beads task `T-xxx …` | `TASK_BOARD.md` row (status mirrors bus) |
| Ownership | file lease (advisory) | `OWNERSHIP_MAP.md` |
| Decision rationale | `--reason` memory | `DECISION_LOG.md` / `PROPOSALS/` (docs win on conflict) |
| Quotas | `busctl usage` | `AGENT_REGISTRY.md` notes |

## 5. Known issues (do not rediscover)
- Agent Mail offline → `busctl boot`.
- Kiro login token expired → open Kiro IDE once.
- Claude CLI token expired (usage reading only) → run any `claude` command.
- Antigravity quota reads need its IDE running.
- Tasks labelled `needs-approval` are untrusted proposals (e.g. from ChatGPT); agents never start/approve them.
- Never print secrets from tool output; never run `busctl install|remote|approve|reject` as an agent (user only).
- If the bus is down, continue with files + worktrees; never block the build on it.
