# Test plan

## Layers
| Layer | What | Tool | Runs |
|---|---|---|---|
| Unit | policy tiers, allowlist, token signing/expiry/replay, ledger idempotency, normaliser, injection patterns, schema validation, state machine transitions | pytest | every commit |
| Integration | extractor+executor on fixtures; Drive/local adapters; control-plane routes; LangGraph gates with in-memory checkpointer | pytest + Playwright | per PR |
| E2E | full graph on fixtures: happy path, edit→re-approve, pause, crash-resume ×3, login handoff, CAPTCHA-stub handoff, duplicate, injection, expired approval | pytest + real Chrome headless | before demo |
| Evals | goal variants × data variants → expected shortlist/answers/outcome table | `evals/run_evals.py` → `evals/RESULTS.md` | before demo |
| Unseen-form benchmark | S9 forms; no submit | script + manual scoring sheet | gate G1 |
| Demo rehearsal | the exact video script, twice | manual | H10 |

## Must-have tests (map to requirements)
| ID | Test | Req |
|---|---|---|
| TP-01 | Submit without approval token → rejected; with token for old snapshot hash → rejected; reused token → rejected | R5 |
| TP-02 | GET on any link never changes ledger/state (property test over all routes) | R5 |
| TP-03 | Kill worker after fill, before gate → resume → 0 refills | R3 |
| TP-04 | Kill worker between approval and click → exactly 1 submit | R3 |
| TP-05 | Kill worker after click, before verify → no 2nd click; status verified or UNVERIFIED | R3, R4 |
| TP-06 | Edit one field → only that field action recorded; new hash; old approval dead | R5 |
| TP-07 | Pause mid-fill → no action executes until resume; browser untouched | R5 |
| TP-08 | CAPTCHA fixture → `NEEDS_HUMAN`; operator makes zero attempts at the challenge; after "done" resumes without refill | R3 |
| TP-09 | Login fixture → handoff; no credentials typed by operator | R3 |
| TP-10 | Duplicate job → `SKIPPED_DUPLICATE` | R3 |
| TP-11 | Hostile job post → `QUARANTINED`; planner prompt contains no excerpt as instruction; forced malicious plan blocked by `policy_check` | R4/safety |
| TP-12 | Drive rule change → different shortlist with identical code (R2 eval) | R2 |
| TP-13 | EEO/legal fields never auto-filled | safety |
| TP-14 | Final status truthful: injected failure produces PARTIAL/BLOCKED with reasons, not COMPLETED | R4 |
| TP-15 | Replay after reload uses 0 LLM calls | R2/R3 |

## Acceptance gates
G1 extractor benchmark · G2 crash-resume · G3 click-to-submit on fixture · G4 hostile board + variation · G5 demo rehearsal.

## Evidence the tests must emit
Per test: pass/fail, ledger dump, screenshots, timing. The eval table and a 1-page "what's proven vs. not proven" go into the engineering note. **Never claim a result that is not in `SPIKE_REPORT.md` or `evals/RESULTS.md`.**
