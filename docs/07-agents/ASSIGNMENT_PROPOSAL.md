# Agent assignment v2 (roles and lane mapping DECIDED by user 2026-10-03)

## 1. Decided by the user
- **Claude = Lead/manager only.** Claude does not build features. Claude plans, assigns, answers questions, reviews every PR, runs gates, explains each change to the user in plain words, and merges **only after the user's explicit "yes" for that specific merge**.
- **Kiro, Antigravity, Codex = the builders.**
- Claude's weekly quota is limited (user: ~33 % left). Claude spends tokens on: contract/PR review from diffs, gate checks, decisions, merge briefs. Not on writing code, not on re-reading whole files. Reserve ≥15 % for G2–G5 and the final integration.
- Repo moves to `C:\Balaastra\hulchul-operator` (no space in the path).

## 2. Facts this rests on (measured 2026-10-03, `busctl usage` / `doctor`)
| Agent | Capacity | Caveat |
|---|---|---|
| Claude | 1.85 M tokens in 5 h, 24.5 M in 7 d; ~33 % weekly left (user) | manager role to conserve |
| Codex (Plus) | 5 h window 0 % used; weekly 0 % used; 4 reset credits | holds the core; watch the 5-hour window |
| Kiro Pro+ | 0 / 2000 credits | login token expired → open Kiro IDE once |
| Antigravity Pro | Gemini 3.x pools ~100 % left; Claude Opus 4.6 / Sonnet 4.6 / GPT-OSS pool ~100 % left | IDE must be running for quota reads |
| Bus | healthy; Agent Mail was offline (`busctl boot`); task creation needs a git repo | leases are advisory |
Strengths below are **reasoned from tooling and quota, not benchmarked**. The first-hour checks (§7) reveal early if a lane is going badly; reassign immediately if so.

## 3. Hierarchy
```
USER ── product owner; final say; explicit approval before every merge; owns locked decisions, the engineering note and the Hulchul reply
  └─ CLAUDE (Lead/manager) ── plans, assigns, reviews, gates, explains, merges-after-approval, keeps docs/board/bus consistent
       ├─ CODEX        — contracts, ledger, policy, LangGraph graph, submit semantics, worker   (the core)
       ├─ ANTIGRAVITY  — browser operator, Google side (Drive data port, Gemini/Vertex LLM port), injection layer, evals
       └─ KIRO         — fixtures, control plane, channels, deploy, README
```
Workers never edit each other's paths and never merge. Cross-lane needs go through Claude via `TASK_BOARD.md` notes or `PROPOSALS/`; urgent blockers via bus message.

## 4. Lane mapping (user-directed swap 2026-10-03: core -> Codex, browser/Google -> Antigravity)
| Worker | Lane | Why | Owns | Tasks |
|---|---|---|---|---|
| **Codex** | Contracts + Core | Biggest unused quota; free at H0 so contracts land in ~30 min; the deterministic core is long, test-driven backend work; 4 reset credits cover the 5-hour window | `src/operator/{contracts,ledger,graph}/**`, `src/operator/policy/{tiers,allowlist,authority}.py`, `worker/**` | T-001, T-002, T-015, T-017, T-018, T-019 (+ S12 S16) |
| **Antigravity** | Browser + Google | Built-in browser and Chrome tooling; Gemini/Vertex and Drive are Google-native; separate quota pool with Claude Opus 4.6 | `src/operator/{browser,llm,data}/**`, `src/operator/policy/injection.py`, `evals/**` | T-003, T-004, T-011, T-012, T-013, T-014, T-016, T-023 (+ S4 S7 S8 S9 S10 S11 S13) |
| **Kiro** | Periphery + control plane | 2000 credits; spec-driven generation suits well-specified services; clearest interface (COMMUNICATION_MATRIX); fixtures are needed early by Antigravity | `fixtures/**`, `sample_data/**`, `control_plane/**`, `src/operator/channels/**`, `deploy/**`, `Dockerfile`, `README.md`, `docs/08-submission/**` | T-010, T-020, T-021, T-022, T-024, T-025, T-026 (+ S5 S6 S15) |
| **Claude** | Manager | decided | `docs/04-decisions/**`, board/registry sync; no source code | review, gates, merges |
Risk call-outs: (1) **Codex holds the critical path** (contracts -> ledger -> policy -> graph -> submit -> worker). Mitigation: LLM port, data port, injection and evals deliberately sit with Antigravity; if Codex slips past H5, Antigravity (Opus pool) takes the worker and policy impl after G1. (2) Claude reviews Codex's graph/policy/ledger PRs line by line against POLICY_AND_SAFETY. (3) Kiro's fixtures are the critical path for Antigravity's G1 and go first.

## 5. What runs in parallel and what must wait
Ports are Protocols in `contracts` (`LedgerPort, LLMPort, DataSourcePort, BrowserPort, ChannelPort`), so each lane builds against fakes and integrates at gates.

| Wave | Window | Parallel work | Must wait for |
|---|---|---|---|
| 0 | before H0 (user + Claude) | `git init`, private repo, worktrees, bus seed, fresh Telegram bot, link-shared Drive folder, Chrome/network check, IDE logins | — |
| 1 | H0–1 | **Codex:** T-001a contracts (browser types + Ports) in 30 min -> T-001b -> start T-002 ledger. **Kiro:** T-010 fixtures first (+ `sample_data/`), then S5/S6. **Antigravity:** S4 Drive spike, T-003 LLM port, S7/S9 baseline from `research/spikes`; starts T-011 once `contracts-v0.1a` lands. **Claude:** reviews contracts, tags | contracts-v0.1a gates typed code in every lane |
| 2 | H1–3 | **Codex:** ledger, T-015 policy, graph skeleton on fakes. **Antigravity:** T-011 to T-013 browser (**G1**), T-004 data port. **Kiro:** token service, review page, Telegram adapter. **Claude:** PR reviews | G1 needs fixtures (Kiro) + LLM port (Antigravity) |
| 3 | H3–5 | **Codex:** T-017 graph wired to real ports, T-018 submit. **Antigravity:** T-014 multi-step, S9 benchmark. **Kiro:** routes (approve/edit/pause/answer/handoff_done), SSE, T-022 handoff fixtures | **G2** needs graph + ledger + browser |
| 4 | H5–7 | **Codex:** T-019 worker (reattach, poll), S12/S16. **Kiro:** control-plane <-> worker integration (**G3**). **Antigravity:** T-016 injection + S8, then T-023 evals | G3 needs T-017/18/19 + T-021 |
| 5 | H7–9 | **Kiro:** T-024 deploy, T-025 WhatsApp only on user's go. **Antigravity:** hostile-board runs, reliability fixes from evals (**G4**). **Codex:** bug-fix queue from gates | cut list if a gate failed |
| 6 | H9–12 | **Kiro:** README. **Antigravity:** eval table to `evals/RESULTS.md`. **Claude:** final review + merge briefs. **User:** engineering note, rehearsal x2, video, reply | G5 |

**Strictly serial:** contracts -> anything typed; ledger schema -> approvals/submit; policy rules -> graph `policy_check`; graph topology (B3); any route change the worker calls (B2, Kiro announces first).

## 6. Review and merge protocol (D-026)
1. Worker finishes on `agent/<id>/<task>`, runs its tests, posts "branch ready" with a note in `TASK_BOARD.md` (what changed, tests, spike evidence, AI-assistance note) and `busctl finish`.
2. **Claude reviews** the diff: safety rules (AGENTS.md §5), ownership (only owned paths), contracts unchanged unless approved, tests exist and were run, no secrets/PII, claims match `SPIKE_REPORT.md`.
3. Claude writes a **merge brief** for the user (plain language, ≤10 lines): what changes, why, risk, what was tested, what to double-check.
4. Claude **asks the user for explicit approval for that merge**. No yes → no merge.
5. On yes, Claude merges with `--no-ff` in dependency order, updates `TASK_BOARD.md`/bus, tells affected lanes to rebase.
B0 doc/test/fixture-only PRs still get the brief but may be batched into one approval.

## 7. First-hour health checks
| Check | Pass |
|---|---|
| Antigravity runs `python research/spikes/probe.py` (Chrome + network) | prints 3 sites |
| Kiro and Antigravity logged in; each reads AGENTS.md and posts its registry row | rows present |
| Every agent: `busctl context --agent <id>` works from its worktree | context printed |
| `contracts-v0.1a` PR ready within 45 min | tag |
| Kiro fixtures served locally with a submission counter | `curl` shows counter |
Fail → Claude reassigns that lane on the spot.

## 8. Claude quota guardrails
Reviews from `git diff` only; ask workers for a ≤15-line summary + test output instead of re-reading files; no code-writing; batch review rounds (one per ~45 min); check `busctl usage` hourly; at ≤15 % weekly → stop reviewing details, delegate review to a fresh Antigravity (Opus 4.6) session under the same checklist and keep only the merge-approval conversation.
