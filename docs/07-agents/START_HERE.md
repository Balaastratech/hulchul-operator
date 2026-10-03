# START HERE — what the user does, in order

Repo: `C:\Balaastra\hulchul-operator` (private GitHub repo, see README). Worktrees (one folder per agent):
| Agent | Open this folder in the agent's app | Branch | First task |
|---|---|---|---|
| Codex | `C:\Balaastra\wt-codex` | `agent/codex/T-001-contracts` | T-001 contracts (critical path, 30 min) |
| Antigravity | `C:\Balaastra\wt-antigravity` | `agent/antigravity/T-003-llm-port` | S4 Drive spike, then T-003 LLM port |
| Kiro | `C:\Balaastra\wt-kiro` | `agent/kiro/T-010-fixtures` | T-010 fixtures (Antigravity waits on these) |
| Claude (manager) | `C:\Balaastra\hulchul-operator` (main) | — | seeds tasks, reviews, merges after your yes |

## Your 5 manual steps (agents cannot do these)
1. **Telegram bot:** in Telegram talk to @BotFather → `/newbot` → copy the token. Put it in `C:\Balaastra\hulchul-operator\.env` as `TELEGRAM_BOT_TOKEN=...` (file is git-ignored; never paste the token into chat). Also send any message to your new bot and keep your chat id handy.
2. **Drive folder:** create a Drive folder `hulchul-operator-data`, share "anyone with the link: viewer". Tell Antigravity the folder link (it is synthetic data only). Kiro supplies the persona files in `sample_data/`; upload copies to the folder.
3. **Gemini API key (for reviewer-parity test S13):** make one at aistudio.google.com, put `GEMINI_API_KEY=...` in `.env`.
4. **Open each agent app once:** Kiro IDE (refresh login), Antigravity IDE (quotas readable), Codex (logged in).
5. **Reviewer access later:** Hulchul's GitHub handle if the repo stays private (or we make it public after the secret scan).

## What to say to each agent (paste exactly; open the agent in its worktree folder first)
**Codex**
> Read AGENTS.md first, then docs/07-agents/KICKOFF_PROMPTS.md section "CODEX" and follow it. You are AGENT_ID=codex. Start with T-001 contracts: publish contracts-v0.1a (FieldSpec, FillAction, FillReport, PageState and the five Ports) within 30 minutes, then T-001b, then T-002 ledger. Stay inside your owned paths. You never merge: open a PR or post "branch ready" in TASK_BOARD.md and tell me.

**Antigravity**
> Read AGENTS.md first, then docs/07-agents/KICKOFF_PROMPTS.md section "ANTIGRAVITY" and follow it. You are AGENT_ID=antigravity. Open the IDE so quotas are readable. First prove Chrome and network with `python research/spikes/probe.py`. Then S4 (Drive), then T-003 LLM port, then the browser lane once contracts-v0.1a lands. Stay inside your owned paths. You never merge.

**Kiro**
> Read AGENTS.md first, then docs/07-agents/KICKOFF_PROMPTS.md section "KIRO" and follow it. You are AGENT_ID=kiro. Start with T-010 fixtures (ATS layout A and B, confirmation page with a server-side submission counter, hostile job board, sample_data persona) because Antigravity is blocked on them. Then S5/S6, then the control plane. Stay inside your owned paths. You never merge.

**Claude (this session or a new one in the main folder)**
> Act as Lead/manager per AGENTS.md and docs/07-agents/ASSIGNMENT_PROPOSAL.md. Do not write feature code. Review PRs, write merge briefs, ask me before every merge.

## How you stay in control during the build
- Every ~45 minutes tell Claude "status": Claude runs `busctl status`, `busctl usage`, reads TASK_BOARD notes, and reports what is proven, what is blocked, and what needs your decision.
- Anything a worker is unsure about lands in `docs/04-decisions/OPEN_QUESTIONS.md`; Claude brings it to you as a short multiple-choice question.
- Merge flow: worker says "branch ready" → Claude reviews → Claude shows you a ≤10-line merge brief → you say yes/no → Claude merges.

## Timeline reminder
Start working for Hulchul: 04-10-2026. Submission deadline: 4 Oct 2026, 11:59 PM IST. Target: build done by H9, video + submit with ≥90 minutes buffer.
