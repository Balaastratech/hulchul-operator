# Roadmap — 12 focused hours (deadline 4 Oct 2026, 11:59 PM IST)

"Hours" = focused engineering hours across all agents plus the user's review time. Four agents work in parallel, so wall-clock is shorter, but **the user's review/approval and the video are serial**. Aim to finish build by H9, leaving H9–H12 for tests, docs, video, submission.

| Hours | Block | Output | Gate |
|---|---|---|---|
| H0–1 | Foundations: `git init`, branches, contracts (T-001), ledger (T-002), LLM port (T-003), local data adapter. In parallel run spikes **S4 S5 S6** | repo skeleton, frozen contracts | Spikes recorded |
| H1–3 | Browser core v2: extractor v2, executor, fuzzy verifier, page-state classifier, evidence. Spikes **S7 S9 S10 S11** | `browser/` green on fixtures + 6 unseen forms | **G1**: ≥90 % fields correct-or-escalated, 0 invented facts |
| H3–5 | Graph + policy: nodes, interrupts, authority tiers, idempotency, resume. Spikes **S12 S16** | graph runs on fixture end to end | **G2**: crash-resume ×3 with 0 duplicate effects |
| H5–7 | Control plane + Telegram + signed links + review page + live progress. Fixtures: two ATS layouts, confirmation page | click link → review → POST approve → submit on fixture | **G3** |
| H7–8.5 | Hostile job board, injection layer, login wall + CAPTCHA-stub fixtures, Drive-driven variation | quarantine works; Drive edit changes behaviour | **G4** |
| H8.5–9.5 | Deploy control plane (Docker + tunnel/VM), security pass, WhatsApp spike **S15 only if everything above is green** | deployed review link | — |
| H9.5–11 | Evals table, README/setup, engineering note, AI-use disclosure, demo rehearsal ×2 | docs + `evals/RESULTS.md` | **G5** |
| H11–12 | Record video (≤5 min), upload, final run on a clean checkout, send reply to Hulchul | submission | — |

## Cut list (apply in this order if a gate fails; never cut R1–R5 proofs)
1. WhatsApp (S15) → documented stub.
2. Tunnel/VM deployment → control plane on localhost + Tailscale; deployment described in FUTURE_SCOPE.
3. Drive write-back, answer-capture back to Drive.
4. Second ATS fixture layout → one layout + the real unseen forms.
5. Eval table → 5 cases.
6. Live trace viewer → static HTML report with screenshots.
**Never cut:** approval gate, idempotent submit, crash-resume, CAPTCHA/login handoff, unseen-form proof, hostile-post quarantine, honest limitations.

## Fallback ladder for the demo video
Live run fails → use recorded segment from the last green rehearsal (state this on screen). Real unseen forms never submitted. Failure scenarios are shown live on fixtures.

## Daily checkpoints (3–4 Oct)
- Tonight (3 Oct): docs reviewed by user, git init, assignments, spikes S4–S6 and contracts frozen.
- 4 Oct morning: G1–G3. Afternoon: G4–G5. Evening: video + submit; **buffer ≥ 90 min before 11:59 PM**.
