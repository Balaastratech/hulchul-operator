# Hulchul Job-Apply Operator (planning stage)

LangGraph-based computer operator: plain-English goal → reads mutable data from Google Drive → fills unseen job-application forms in a real browser → read-back verification → **pauses in place** → sends a review summary with a clickable approve link → submits only after approval → verifies the result. Never solves CAPTCHAs; hands over to the human and resumes.

**Status (2026-10-03):** documentation and feasibility spikes complete; build not started. Start at [`docs/07-agents/START_HERE.md`](docs/07-agents/START_HERE.md) (user) or [`AGENTS.md`](AGENTS.md) (agents) then [`docs/00-INDEX.md`](docs/00-INDEX.md).

Setup instructions will land here once the build exists (see `docs/08-submission/SUBMISSION_CHECKLIST.md`). Model/account requirements: Python ≥3.11, Google Chrome, a Gemini API key (or Vertex access); optional Telegram bot token.
