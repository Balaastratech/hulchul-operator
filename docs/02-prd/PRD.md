# PRD — Job-Apply Operator

## 1. Problem
Applying to jobs means repeating the same 20–60 form fields across ATS systems with different layouts, while obeying personal rules (target roles, salary floor, blocked companies, never say X). Existing auto-apply tools either click blindly or submit without a human check. We want an operator that handles **unseen forms**, **tells the user exactly what it entered**, **waits in place for approval**, and **never double-submits**.

## 2. Users
Primary: a job-seeker who delegates applications but keeps final authority (the user, and later Raj-style candidates).
Secondary: the Hulchul reviewers evaluating reliability, safety and explainability.

## 3. Goal sentence (the input)
Plain English, e.g. *"Apply to the 3 best-fit roles under my rules."* Variations: *"Only remote roles, skip fintech, apply to 2."* / *"Dry run: fill but don't ask me to submit."*

## 4. User stories
| ID | Story | Acceptance |
|---|---|---|
| U1 | As a user I give a goal and the operator reads my rules, profile, answers and resume **from Google Drive** | Changing a Drive file changes behaviour on the next run with **no code change** (R2) |
| U2 | The operator shortlists jobs under my rules and tells me why each was chosen/skipped | Shortlist with reasons delivered before filling starts |
| U3 | It fills an **unseen** multi-step form in a real browser, answering from my data, asking me only for what it cannot know | ≥90 % of fields on the unseen-form benchmark filled correctly or escalated; 0 invented facts |
| U4 | It shows me **everything it entered** and every field it left, plus a screenshot, before I decide | Review page + Telegram summary; values come from a read-back of the live form |
| U5 | I get a **clickable link**; one click opens the review, one click submits | Link works from phone/laptop; GET never submits; token single-use |
| U6 | I can edit any field; only that field changes; I must approve again | Edit does not re-run other fields; new snapshot hash; old approval invalidated |
| U7 | I can pause at any time and the browser stays exactly as it is | Pause → no further actions until Resume; state survives a worker crash |
| U8 | Login walls and CAPTCHAs are handed to me; the operator resumes without redoing work | After my action: 0 refills, 0 duplicate accounts, submit once |
| U9 | A job post that tries to instruct the AI is quarantined and flagged | Injected instruction never executed; appears in report |
| U10 | Re-applying to the same job is blocked | Duplicate → `SKIPPED_DUPLICATE`, reason shown |
| U11 | I get a final report: COMPLETED / PARTIAL / BLOCKED / CANCELLED with evidence and what remains | Report generated for every run, incl. failures |

## 5. In scope (12-hour build)
LangGraph agents and gates · Playwright/CDP operator with extractor, executor, page-state classifier, fuzzy verifier · Drive-backed data (read) · policy engine · idempotent ledger · FastAPI control plane (review page, signed tokens, pause/approve/edit) · Telegram channel · fixtures (mock ATS ×2 layouts, hostile board, login wall, CAPTCHA-stub page, confirmation page) · tests + small eval table · README, video, engineering note.

## 6. Out of scope (documented in FUTURE_SCOPE.md, not built)
Dedicated per-user inbox and OTP/email verification · automatic employer-account creation · ATS-specific adapters · answer-library database and editing UI · fully deployed remote browser · assessments · multi-user · WhatsApp (unless the late-stage spike succeeds) · any CAPTCHA solving · auto-submitting without approval.

## 7. Non-functional requirements
| Area | Target |
|---|---|
| Safety | No Submit without a valid single-use approval for the exact snapshot; no off-allowlist navigation; secrets only via env |
| Reliability | Crash at any node → resume with zero duplicate side effects; every side effect has an idempotency key |
| Verifiability | Every filled field is read back; every run ends in an explicit status with evidence |
| Cost | ≤ ₹10 of model spend per application on Vertex; per-run token and cost counter shown |
| Latency | Fill phase ≤ 90 s for a 40-field form (measured 17–34 s single-page) |
| Explainability | Run ledger + trace viewer: one screenshot and one reason per step |
| Portability | Reviewers need only Python 3.11+, Chrome, and a `GEMINI_API_KEY` |

## 8. Success metrics (reported in the engineering note)
Field-fill accuracy on N unseen forms · escalation rate for unknown fields · injection catch rate · crash-resume duplicates (target 0) · LLM calls per replayed run (target 0) · time and cost per application.

## 9. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Custom widgets (yes/no buttons, hidden checkboxes, comboboxes) | Extractor v2 + S9 benchmark gate G1 |
| Verification false negatives (normalisation) | Fuzzy/LLM-judged verifier (S10) |
| CAPTCHA/login coverage unproven | Deterministic detector + fixtures; claim only what is measured |
| Telegram/mail prefetch of links | POST-only submit, token bound to snapshot (S5) |
| Scope creep from AIApply-style features | FUTURE_SCOPE only; cut list in ROADMAP |
| Merge conflicts across 4 agents | Ownership map + worktrees + blast-radius rules |
