# LangGraph agents, state and edges

Principle (D-004): each node is either **LLM** (proposes, returns structured output validated by Pydantic) or **DET** (deterministic code). Gates are `interrupt()` nodes. Every node declares inputs, outputs, side effects, idempotency, and failure routes.

## 1. Top-level graph (one run = one goal)
```
START → intake[LLM] → load_data[DET] → select_jobs[LLM+DET] → for each job: ┐
                                                                              │ application subgraph (§2)
        ┌─────────────────────────────────────────────────────────────────────┘
        └→ aggregate[DET] → final_report[DET] → END
```
| Node | Type | Input → Output | Side effects | Fails to |
|---|---|---|---|---|
| `intake` | LLM | goal text → `Goal{max_apply, filters, mode(dry_run/normal), notes}` | none | ask user via `ask_user` gate if ambiguous |
| `load_data` | DET | Drive/local → validated `Profile, Rules, AnswerLibrary, Resume, JobQueue` + snapshot hash | reads Drive | `BLOCKED(data_invalid)` with exact schema errors |
| `select_jobs` | LLM+DET | jobs × rules → ranked `Shortlist` with reasons; DET filters: blocked companies, duplicates (ledger), allowlist | none | empty shortlist → report |
| `aggregate`/`final_report` | DET | per-job results → COMPLETED / PARTIAL / BLOCKED / CANCELLED + evidence links | writes report | — |

## 2. Application subgraph (per job, multi-step form)
```
open_application → classify_page ─┬─ FORM ───────────────► extract_fields → plan_answers → policy_check
                                  ├─ LOGIN / CAPTCHA ────► human_handoff (interrupt) ─► classify_page
                                  ├─ CLOSED/UNSUPPORTED ─► mark_not_supported → END(job)
                                  └─ INJECTION_FLAG ─────► quarantine → END(job)
policy_check ─► execute_fill ─► verify_fill ─┬─ mismatch → repair (≤2) → execute_fill
                                              └─ ok → more_steps?
more_steps? ── yes → click_next (T2 reversible) → classify_page
            └─ no (submit button reachable) → build_review → review_gate (interrupt)
review_gate ─ approve → pre_submit_check → submit → verify_submission → record → END(job)
            ─ edit(field,new) → apply_edit → verify_fill → build_review → review_gate   (re-approval)
            ─ reject / skip → END(job, REJECTED_BY_USER)
            ─ pause → paused (interrupt) → resume where it was
            ─ expire → END(job, APPROVAL_EXPIRED)
```
| Node | Type | Responsibility | Idempotency / safety |
|---|---|---|---|
| `open_application` | DET | navigate to posting's apply URL; domain allowlist check; save CDP endpoint+target in state | skip if already at URL |
| `classify_page` | DET→LLM fallback | one of `FORM, LOGIN, CAPTCHA, CLOSED, UNSUPPORTED, CONFIRMATION, UNKNOWN`. Signals: password input, visible challenge iframe, "no longer accepting", submit button text, confirmation text | UNKNOWN → human_handoff (not guess) |
| `quarantine` | DET | job text matched injection scan → store, never feed to planner as instructions, notify | fully isolated |
| `extract_fields` | DET (JS) + LLM for ambiguous labels | build `FieldSpec[]` (id, key, label, group, type, options, required, current value); handles inputs, selects, comboboxes, radio/checkbox groups, button-based yes/no, file inputs | stable `key = label|type|group|n` |
| `plan_answers` | LLM | map each field to: `fill(value)`, `select`, `check`, `upload_resume`, `ask_user(question)`, `skip` using ONLY Profile + AnswerLibrary + Rules; free text allowed only for essay fields, marked `generated=true` | never invents facts; sensitive/EEO/legal → `ask_user` |
| `policy_check` | DET | enforce authority tiers (POLICY_AND_SAFETY), allowlist, forbidden fields; strips actions above T2 | cannot be overridden by the model |
| `execute_fill` | DET | perform actions through the Browser port; per-action ledger record (key = run+job+action+field) | `SUCCESS` skip on re-entry |
| `verify_fill` | DET + LLM judge | read back every field, compare to intended value with normalisation; LLM judge only for semantic matches ("Ahmedabad, India" ≈ "Ahmedabad, Gujarat, India") | produces `FillReport` |
| `repair` | DET | retry failed fields ≤2 with alternative strategy (click label, keyboard, scroll), then escalate to `ask_user` | bounded |
| `click_next` | DET | click Continue/Next (reversible) only if label matches the next-step vocabulary and not a submit vocabulary | never clicks submit-class buttons |
| `build_review` | DET | `ReviewSnapshot{fields as read back, uploads, generated texts flagged, unanswered, screenshots, hash}` | hash binds approval |
| `review_gate` | interrupt | emits E07; waits for `Command` from control plane | survives crash (S3) |
| `apply_edit` | DET | change only the edited field via stable key; invalidates previous approval | zero other field actions |
| `pre_submit_check` | DET | token valid & unused; snapshot hash equals current live read-back hash; ledger has no SUCCESS for this job | stale form → back to review |
| `submit` | DET | write `SUBMITTING` to ledger, then click | never re-click |
| `verify_submission` | DET + LLM judge | success evidence: confirmation text/URL pattern, application id, page-state `CONFIRMATION`; otherwise `SUBMITTED_UNVERIFIED` (visible, not hidden) | status truthfully reported |
| `record` | DET | ledger update, evidence links, log row | — |
| `human_handoff` | interrupt | emits E04/E05 with instructions + "I'm done" button; resumes at `classify_page` | never solves CAPTCHA |

## 3. State (checkpointed; serialisable only)
```
RunState {
  run_id, goal, goal_parsed, data_snapshot_hash, mode, status,
  shortlist[], jobs{job_id: JobState},
  cdp_endpoint, active_job_id,
  usage{llm_calls, tokens, cost_inr}, events_cursor
}
JobState { job_id, url, status(§4), page_state, fields[FieldSpec], actions[FillAction], fill_report,
           review_snapshot_hash, approval{token_hash, used_at}, evidence[], blockers[], notes[] }
```
Browser pages, file handles and secrets are NOT in state.

## 4. Job status machine
`QUEUED → OPENED → FILLING → (NEEDS_HUMAN | NEEDS_ANSWER) ⇄ FILLING → READY_FOR_REVIEW → APPROVED → SUBMITTING → SUBMITTED_VERIFIED`
Terminal alternatives: `SUBMITTED_UNVERIFIED, FAILED, SKIPPED_DUPLICATE, QUARANTINED, NOT_SUPPORTED, REJECTED_BY_USER, APPROVAL_EXPIRED, CANCELLED`. `PAUSED` is an overlay flag.

## 5. Run result rule
COMPLETED = every selected job is `SUBMITTED_VERIFIED` (or user-rejected by choice). PARTIAL = at least one success and at least one non-success. BLOCKED = zero successes with a concrete blocker. CANCELLED = user stop. Incomplete work is always listed with the reason and the next action.

## 6. Why LangGraph here (for the engineering note)
Checkpointed state, native `interrupt`/resume (proved in S3), subgraphs for the per-job loop, and explicit edges that make the control flow reviewable. We do **not** let the model choose arbitrary next nodes: edges are code; the LLM only fills typed outputs.
