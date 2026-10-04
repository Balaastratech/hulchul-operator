# Engineering note

## Problem and design

Job applications combine mutable candidate facts with unfamiliar forms, sensitive questions and irreversible submission. The operator accepts a plain-English goal, selects roles, fills and verifies inputs in real Chrome, then pauses for a human to review, edit or stop. Real employer sites are fill-only; approved submission is demonstrated on self-hosted fixtures.

Candidate data comes from Google Drive or a local folder and is snapshotted with a hash (D-009, D-021). Gemini proposes shortlist and grounded answers; deterministic code controls permissions, verification and approval (D-004, D-033). LangGraph saves workflow checkpoints in SQLite (D-001). Chrome runs separately and the worker reconnects through its debugging connection (D-002). A FastAPI control plane receives events and snapshots; Telegram delivers review links (D-006, D-007). The worker makes outbound calls so its computer needs no inbound service.

The review link is read-only. Approval is an expiring, signed, single-use POST bound to the exact snapshot; edits invalidate old approval (D-008, D-030). The ledger records `SUBMITTING` before the click, and ambiguous recovery only verifies rather than clicking again (D-015). This sacrifices completion in one crash window to avoid duplicate submission. CAPTCHA and login are handed to the human, never solved (D-005); submissions remain restricted to fixtures (D-010, D-014). Untrusted job instructions are quarantined (D-017), and sensitive/legal facts cannot be invented.

Decision IDs refer to the [decision log](../04-decisions/DECISION_LOG.md).

## What was measured

- The [independent audit](../05-testing/AUDIT_SUMMARY.md) reproduced **28 findings: 2 critical, 14 high, 11 medium, 1 low** on an earlier revision. The critical findings were a Continue-labelled submit button clicked during navigation and an Enter fallback submitting a combobox's form. These were fixed and covered by regressions; the audit is a historical baseline, not a statement that every finding is still open.
- The [historical crash/adversarial matrix](../05-testing/REDTEAM_REPORT.md) reconciled **150 unique cases: 132 passed, 18 strict expected failures**, including 50 node-boundary and 40 route-loss cases. Twelve recovery liveness failures were reported. It combined preserved and resumed results; it was not one uninterrupted final run. Later focused fixes do not constitute a complete post-fix matrix rerun.
- The [phone proof](../05-testing/evidence/phone-proof-2026-10-04.json) and [timeline](../05-testing/evidence/phone-proof-2026-10-04-timeline.jsonl) used real Drive, Vertex Gemini, headed Chrome, Telegram and human approval. **One human approval, one verified fixture submission, replay refused with `409 token_replayed`**. Seven model calls reported about **INR 3.65**; other selected jobs were deliberately rejected, leaving `PARTIAL`.
- The [public rehearsal](../05-testing/REAL_REHEARSAL.md) was a guarded pre-hardening baseline: **Greenhouse 7 verified of 34 controls, 22 escalated, 4 skipped, 1 unverified; Lever 4 of 62, 53 escalated, 5 skipped**. Ashby had no available form; seven sites were not run. Zero employer submissions. Clicks, uploads and custom widgets were withheld, so these are not application-completion rates.
- [Historical S9 evidence](../../evidence/s9/s9_benchmark_summary.json) reconciles **53 verified + 63 escalated + 56 skipped + 1 unverified = 173 controls**. [S10](../05-testing/SPIKE_REPORT.md) measured **38/40 comparisons**. Original estimator costs are not billing evidence.
- The final cleanup run on 4 Oct 2026 passed: **710 passed, 104 skipped, 8 deselected, 2 xfailed in 64.60 seconds** (exit 0). The full offline suite ran once.

## Failures found and fixed

Beyond the pre-approval submit paths, fixes included fail-closed/full-text injection checks, stricter read-back, atomic candidate refresh, expired approval renewal, scoped screenshot delivery and replay handling. The phone rehearsal exposed a review POST Origin problem, missing screenshot upload, Windows state-file replacement races and multi-step handoff skipping later pages. Focused regressions and the successful phone proof support those corrections; some remaining fields still require human answers.

Crash recovery can end `SUBMITTED_UNVERIFIED` with zero submissions after intent was recorded before the click. An unobserved Next transition requires manual inspection instead of a repeated click. These are deliberate uncertainty boundaries. The current demo launcher parses `--crash after_claim` but runs `before_claim`; the separate opt-in integration test proves the after-claim behavior. Cleanup documents that existing issue without changing runtime behavior.

## Not claimed and next

No real employer submission, CAPTCHA solving, universal ATS coverage, unattended production readiness, full post-fix chaos rerun or working WhatsApp integration is claimed. Windows is the measured platform; macOS/Linux and live Gemini API-key parity remain unproven. Future work: inbox and OTP handling, account creation, website adapters, an answer-library editor and remote browser operation. These match the [video limits](VIDEO_SCRIPT.md) and [future scope](../06-roadmap/FUTURE_SCOPE.md).

## AI tools and owner contribution

Claude, Codex, Antigravity, Kiro and Gemini were used for planning, coding, tests, review and documentation. The user directed scope and decisions, approved merges, verified outcomes and performed the phone approval. Model-generated proposals never replace deterministic safety checks.

**TODO(user): Add your own 3–5 sentences stating your design choices, implementation contribution and personal verification.**
