# Submission checklist (deadline 4 Oct 2026, 11:59 PM IST — target done by 10:00 PM)

## Deliverables
- [ ] **Repo link** reviewers can open (OQ-06). No secrets, no real personal data; `.env.example` only. Clean-checkout run verified.
- [ ] **README**: 5-minute setup (Python ≥3.11, Chrome, `pip install -e .`, `GEMINI_API_KEY`, optional Telegram + Drive), one-command demo on fixtures, how to run tests/evals, model and account requirements stated.
- [ ] **Demo video ≤ 5:00**, viewable link (YouTube unlisted / Drive link). Test the link in a private window.
- [ ] **Engineering note** (1–2 pages): see outline below.
- [ ] **AI-assistance disclosure** in the note: which tools (Claude Code, Codex, Kiro, Antigravity, Gemini) did what; what the user wrote/decided/reviewed; confirm the user can explain and modify.
- [ ] **Reply in Internshala chat**: repo link + video link + **current start date (04-10-2026, decided)** + **availability for a 6-month internship, ≥30 focused hrs/week** + any timing adjustment (must be raised BEFORE the deadline).

## Video script (≤ 5:00)
| Time | Segment | Shows | Req |
|---|---|---|---|
| 0:00–0:30 | Problem + architecture in one slide: model proposes, code disposes | LangGraph diagram, control-plane/worker split | — |
| 0:30–2:10 | **Task**: goal sentence → Drive data loaded → shortlist with reasons → real unseen form being filled in the visible browser → review summary lands in Telegram → click link → review page with read-back values → **Approve & Submit** (on fixture) → confirmation verified → ledger row | actual interactions, evidence | R1 R4 R5 |
| 2:10–3:10 | **Variation**: edit the Drive `rules`/`answers` (and goal sentence) with no code change → different shortlist and answers; also show the hostile job post being quarantined | R2 + safety | R2 |
| 3:10–4:25 | **Failure**: (a) kill the worker mid-form → restart → resumes with zero refills; (b) CAPTCHA-stub/login fixture → "outside my authority" → user completes → resume; (c) duplicate application blocked | R3 | R3 |
| 4:25–5:00 | Pause + edit-one-field + re-approve in 10 s; honest limitations; what's next (FUTURE_SCOPE) | R5 | R5 |

Rules: real unseen employer forms are filled but **never submitted** (state it on screen). If a live segment fails, use a rehearsal recording and say so.

## Engineering note outline
1. Problem and why this workflow (Raj's candidate side; unseen forms are the real difficulty).
2. Architecture: LangGraph agents, control plane vs worker, ports/adapters.
3. **What the model decides vs what code decides** (central section).
4. Reliability: checkpoints, CDP reattach, idempotency keys, `SUBMITTING` semantics, replay with 0 LLM calls.
5. Human control: tiers, signed single-use approval bound to snapshot hash, POST-only submit, edits, pause.
6. Safety: no CAPTCHA solving (why), injection quarantine, domain allowlist, EEO/legal rules.
7. Measured results: link to `SPIKE_REPORT.md` and `evals/RESULTS.md` (numbers, not adjectives).
8. Limitations (be specific): supported ATS list as measured, no account creation/OTP, local browser, single user, Drive-public data path, etc.
9. AI assistance and personal contribution.
10. Next steps → `FUTURE_SCOPE.md` (inbox/OTP, adapters, answer-library UI, remote browser, WhatsApp).

## Final pre-submit gate
- [ ] Confirm `private/` is not in the repo or any archive (company research, brief PDF)
- [ ] Fresh clone → setup → fixture demo passes on the user's PC
- [ ] `pytest` and `evals/run_evals.py` green; results committed
- [ ] grep repo for keys/tokens/emails/phones that aren't synthetic
- [ ] Control-plane link from phone works (or documented as local-only)
- [ ] Video under 5:00 and plays logged-out
- [ ] Message to Hulchul drafted and approved by the user before sending
