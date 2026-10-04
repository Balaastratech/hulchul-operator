# Agent registry (append-only; agents add their own row)

| AGENT_ID | Tool | Config file it reads | Known strengths (fill from experience, do not assume) | Quota / context notes | Role (D-025) |
|---|---|---|---|---|---|
| claude | Claude Code (Sonnet 5.5), MANAGER ONLY | `CLAUDE.md` → `AGENTS.md` | wrote these docs and the 3 spikes; has browser/Drive/Gmail MCP tools in the user's setup | weekly ~33 % left (user); guardrails in ASSIGNMENT_PROPOSAL §8 | Lead/manager (no feature code) |
| codex | Codex CLI | `AGENTS.md` | — | bus snapshot 2026-10-03: 5-hour 0 % used, weekly 0 % used | Builder: contracts + core (ledger, policy, graph, submit, worker) |
| kiro | Kiro | `.kiro/steering/00-read-agents-md.md` → `AGENTS.md` | — | bus snapshot: credits 0 % used | Builder: fixtures, control plane, channels, deploy, README |
| antigravity | Antigravity (Gemini / Claude models) | `GEMINI.md` → `AGENTS.md` | — | bus snapshot: Gemini 3.8 Flash and Claude Sonnet 4.6 100 % left | Builder: browser operator, Google side (Drive data, Gemini/Vertex LLM), injection, evals |

Template for a new row: `| id | tool | config file | strengths observed | quota notes | assignment |`
| codex-b | Codex CLI (second session, worktree C:\Balaastra\wt-codex-b) | `AGENTS.md` | Same tool as `codex`; takes T-033 (phone proof, config hardening, Dockerfile) | shares the Codex quota with `codex` | Builder: control_plane/**, channels, deploy for T-033 only |

| codex-d | Codex session 4 | `AGENTS.md` | Fixture-only worker crash and adversarial request testing (T-035) | shares the Codex account quota | Builder: tests/chaos, tests/redteam and REDTEAM_REPORT; Claude reviews/merges |
