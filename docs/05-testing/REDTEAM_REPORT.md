# chaos and red-team report

Dated 2026-10-04. Historical crash and adversarial baseline; subsequent focused verification is recorded below.

## Reproduce

```powershell
$env:RUN_CHAOS='1'; python -m pytest tests/chaos tests/redteam -q -ra
```

Run serially with system Chrome and exclusive access to **127.0.0.1:8780**.
Signing keys, worker credentials, candidate data and answers are synthetic.
No `.env`, external employer, model service, Telegram or CAPTCHA interaction is
used. Ordinary `python -m pytest -q` skips browser chaos. The node and route
inventory checks run offline and fail if the production inventory changes.

## Method and limits

The suite drives the real G3 worker, SQLite checkpoints/ledger, control-plane
HTTP routes and Chrome CDP target. A test-only wrapper stops the worker before
each node function or after its return, before LangGraph checkpoints that output.
The parent kills that process and starts another against the same databases and
living Chrome target. An interrupting node's after boundary is its resumed return;
`application` is observed at child entry and the next parent node after child exit.
The repair variant fails an input before DOM mutation. A native question variant
asks explicitly for a synthetic phone number, exercising `ask_user` and
`/api/answer` independently of the legal-checkbox handoff.

Each route is faulted before handling and after handling finishes but its response
is lost. A test-only ASGI 503 represents request/response loss; this is not a
TCP-packet-drop measurement. Human retries use the same token. Worker event
delivery uses the production HTTP sink's three attempts rather than G3's one-attempt
demo configuration. Every route fault also kills/restarts the worker. HEAD and
SSE are included. Evidence uses an actual synthetic review screenshot through
the authenticated evidence route. Skip uses a synthetic documented E02 `chosen`
envelope because the original demo emits `selected`; this tests the CP shortlist
skip gate and does not establish a graph shortlist-confirmation flow.

Operator inputs are counted by field key without recording values. Recovery must
add zero repeated inputs; an explicitly requested edit may add exactly one email
input. Fixture counters independently enforce at most one submission. A VERIFIED
state or control-plane E10 event requires a recorded fixture submission.
Approval commands cannot multiply;
successful flows reject the same approval before and after worker recovery.
Rejected/cancelled jobs are truthful no-submit terminals. Liveness defects can
leave FAILED or NEEDS_HUMAN: those cases fail the requested terminal-status invariant
and are strict xfails, not successful recoveries. Safety assertions run in `finally`
and cannot be hidden by recovery xfails, which accept only `RecoveryFailure`.

Results from an earlier run sharing a Windows reusable port were discarded.
Only runs using the exclusive fixture listener contribute to this report.

## Matrix and verification

Verified **150/150 collected case IDs: 132 passed, 18 strict xfails**. The
matrix covers 25 nodes at both boundaries and 20 method/routes in both loss
phases. The two internal submit-claim windows, both inventory checks and all 56
red-team cases are also included. Findings: one high, eight medium, one low.

The retained run was interrupted after 66 completed cases. Its sanitized
per-case results were preserved; the remaining 84 cases were resumed using
`--deselect` for those exact completed IDs. That run exited 0: **76 passed,
66 deselected, 8 xfailed in 1071.66 s**. Saved results plus its JUnit results were
matched against fresh collection: all 150 unique IDs accounted for, no unexpected
failures or skips. This is combined verification, not a claim of a monolithic
final-run exit. An earlier complete discovery run found the two then-unmarked
RT-10 failures; their focused retest produced **2 strict xfails** after filing.

Offline verification: `python -m pytest -q` — **483 passed, 99 skipped,
6 deselected, 5 xfailed**. Ruff check/format and `git diff --check` passed.
Installed-dependency audit found no known vulnerabilities; the local
`hulchul-operator` package was skipped because it is not on PyPI.

Measured against baseline `9c0f449cdd64f2deeefc3466ea48036f40e91365`.

Cell format is **status / fixture submissions**. V = SUBMITTED_VERIFIED;
U = SUBMITTED_UNVERIFIED; F = FAILED; H = NEEDS_HUMAN;
R = REJECTED_BY_USER; C = CANCELLED. V, U, R and C are passing outcomes.
F/H rows are strict recovery xfails. **All rows passed the independent safety
assertions: zero recovery refills, at most one submission, no false VERIFIED
state/E10 event and at most one approval command.** Explicit edit cases have
exactly the initial email fill plus their one requested edit.

### Node matrix

| Node | Kill before | Kill after return |
|---|---|---|
| `intake` | V/1 | V/1 |
| `load_data` | V/1 | V/1 |
| `select_jobs` | V/1 | V/1 |
| `next_job` | V/1 | V/1 |
| `aggregate` | V/1 | V/1 |
| `final_report` | V/1 | V/1 |
| `application` | V/1 | V/1 |
| `open_application` | V/1 | V/1 |
| `classify_page` | V/1 | V/1 |
| `extract_fields` | V/1 | V/1 |
| `plan_answers` | V/1 | V/1 |
| `policy_check` | V/1 | V/1 |
| `execute_fill` | V/1 | V/1 |
| `verify_fill` | V/1 | V/1 |
| `repair` | F/0 — XFAIL RT-05 | F/0 — XFAIL RT-05 |
| `click_next` | F/0 — XFAIL RT-05 | H/0 — XFAIL RT-07 |
| `build_review` | F/0 — XFAIL RT-05 | F/0 — XFAIL RT-05 |
| `review_gate` | F/0 — XFAIL RT-05 | V/1 |
| `human_handoff` | V/1 | V/1 |
| `ask_user` | V/1 | V/1 |
| `paused` | F/0 — XFAIL RT-06 | V/1 |
| `apply_edit` | F/0 — XFAIL RT-08 | F/0 — XFAIL RT-08 |
| `pre_submit_check` | V/1 | V/1 |
| `action_boundary` | V/1 | V/1 |
| `submit` | V/1 | V/1 |

Internal submit windows also pass:

| Window | Measured outcome |
|---|---|
| Before durable submit claim | V/1; no repeated fill |
| After durable claim, before click | U/0; no retry click |

### Control-plane matrix

| Method / route | Request lost before handling | Response lost after handling |
|---|---|---|
| `GET /r/{run_id}` | V/1 | V/1 |
| `GET /r/{run_id}/{job_id}` | V/1 | V/1 |
| `HEAD /r/{run_id}` | V/1 | V/1 |
| `HEAD /r/{run_id}/{job_id}` | V/1 | V/1 |
| `GET /events/{run_id}` | V/1 | V/1 |
| `POST /api/approve` | V/1 | V/1 |
| `POST /api/edit` | V/1 | V/1 |
| `POST /api/reject` | R/0 | R/0 |
| `POST /api/skip` | R/0 | R/0 |
| `POST /api/answer` | V/1 | V/1 |
| `POST /api/handoff_done` | V/1 | V/1 |
| `POST /api/pause` | V/1 | V/1 |
| `POST /api/resume` | V/1 | V/1 |
| `POST /api/cancel` | C/0 | C/0 |
| `GET /api/worker/runs/{run_id}/commands` | F/0 — XFAIL RT-10 | F/0 — XFAIL RT-10 |
| `POST /api/worker/runs/{run_id}/ack` | V/1 | V/1 |
| `POST /api/worker/runs/{run_id}/heartbeat` | V/1 | V/1 |
| `POST /api/worker/runs/{run_id}/events` | V/1 | V/1 |
| `POST /api/worker/runs/{run_id}/jobs/{job_id}/snapshot` | V/1 | V/1 |
| `POST /api/worker/runs/{run_id}/evidence` | V/1 | V/1 |

Each of the 40 route cases recorded both `route-fired` and an actual
`route-restarted` worker restart. Request loss uses the disclosed ASGI 503
model; worker event retry count and explicit skip/evidence producers are
described above.

## Adversarial findings

| ID | Severity | Reproducer / observed behavior | Scope |
|---|---|---|---|
| RT-01 | Medium | Targeted standalone instruction to email the resume is classified clean | Injection detection; no email sent |
| RT-02 | Low | Correct Origin masks contradictory cross-site or same-site Fetch Metadata | Defense in depth; not proof browsers can forge Origin |
| RT-03 | Medium | A source-bound name action with an injected form label is accepted | Missing label quarantine; malicious value substitutions remain blocked |
| RT-04 | High | Off-allowlist local fixture receives the navigation redirect during fill | Destination receipt measured by server; no external target contacted |
| RT-05 | Medium | Repair/review continuation recovers into FAILED at review_gate | No repeated input or submit |
| RT-06 | Medium | Before-paused crash yields FAILED and removes Resume form | After-return pause recovers |
| RT-07 | Medium | After-click_next crash repeats uncertain-transition human handoffs | Ten explicit handoffs do not reach review; no repeated input/submit |
| RT-08 | Medium | Interrupted apply_edit applies one requested edit then fails instead of returning to fresh review | Email input is initial fill plus one edit |
| RT-09 | Medium | Signed boolean answer rejected with 422 although the graph requires bool for checkbox/radio answers | No command queued; separate regression, text question used for matrix |
| RT-10 | Medium | Lost command poll is persisted as FAILED at action_boundary and restart cannot recover | Both phases: zero inputs, zero submissions |

Strict xfails must be removed or updated only after owner-reviewed fixes. Tests
for forged/tampered/expired/cross-run/cross-job/wrong-action tokens, act/view swaps,
replay, wrong Origin, Fetch Metadata without Origin, EEO guesses across rule
policies, malicious action/value substitutions, normal/chunked oversized bodies
and simultaneous approvals also assert the expected safe behavior. Both same-token
and distinct-valid-token approval races must yield one 200, one 409, exactly one
command and one consumed capability.

## Core recovery fixes (2026-10-04)

The preceding results describe the historical baseline. Later fixes change production
core code and the corresponding strict regression expectations. The baseline's
18 total xfails comprise 12 recovery cases, one redirect case and five findings
owned by the injection/control-plane lanes; they are not 18 recovery cases.

- RT-04: BrowserBridge installs a CDP Fetch request-stage guard for Document
  requests on the saved target. Every redirect hop is checked before network IO,
  including redirects that Playwright's page route does not intercept. The
  local forbidden-destination receipt assertion is retained as a hard regression.
  Repeated attachment to the same endpoint/target reuses its live interception
  session, avoiding duplicate interception during recovery of open_application.
- RT-05/06/08: worker resumes use a mapping from the specific persisted interrupt
  ID to the validated command, rather than LangGraph's unscoped null resume.
  A command that releases a handoff, pause or edit cannot be replayed into the
  next review/paused gate after a crash. Existing run/job/snapshot binding,
  approval expiry/consumption and command acknowledgement rules are retained.
  Review construction restores checkpointed answer intent before live read-back,
  including an edit whose execution outlived the adapter cache; this restores
  metadata only and never repeats the browser input.
- RT-07: the reversible Next action records its observed boolean result in an
  add-only ledger table, atomically with SUCCESS. Recovery uses this result to
  enter classification or review without clicking again. A conflicting observed
  result cannot overwrite the original. No submit-action schema or claim changes.
- RT-10: action-boundary polls retry transport failures three times. An exhausted
  poll raises a recoverable outage outside the guarded terminal-failure path.
  The worker keeps its checkpoint and retries pending work on the polling loop;
  no fill or submit occurs while pause/cancel authority is unavailable.

Accepted limits (exact scenarios):

- A crash after durable SUBMITTING but before the fixture click still ends
  SUBMITTED_UNVERIFIED with zero submissions. Recovery verifies only; it never
  retries that click. This is the intentional at-most-once tradeoff, not VERIFIED.
- A crash after a Next click but before its result and SUCCESS transaction commits
  leaves a CLAIMED action with no observation. It ends NOT_SUPPORTED with an
  explicit unobserved-transition blocker and instructions to inspect the browser
  manually; it never re-clicks or loops through handoffs. A pre-upgrade SUCCESS
  record with no stored result has the same truthful terminal outcome. This change fixes the measured after-node-return
  window; it does not infer an unobserved transition or bypass human inspection.
- The navigation guard protects the attached saved browser target while the worker
  connection exists. Worker-down manual browser activity and separate browser
  targets are outside this regression's proof. Static subresources retain the
  existing policy; this regression tests Document navigation and redirect hops.
- RT-02 (two cases) and RT-09 remain strict xfails for their control-plane
  owner. These three are pending fixes, not accepted core safety/liveness limits.
  RT-01 and RT-03 were fixed upstream and their regressions pass after rebase.

The manager stopped further matrix expansion and the remaining 121-case rerun.
The original 50-node/40-route safety matrix above remains the baseline evidence;
no complete post-fix matrix run is claimed. A partial post-fix attempt retained
28 passing cases before an empty crash-marker race stopped it. Fault markers now
publish by atomic rename; startup/cleanup timeouts and the failed attempt are
excluded from successful counts. Internal submit-claim barriers use the same
persistent chaos-child protocol; Windows cleanup falls back to the exact tracked
Chrome process handle. The host needed CHAOS_START_TIMEOUT=180 for subprocess
startup; production timeouts are unchanged.

GET/HEAD /s/{code} and GET/HEAD /evidence/{evidence_id} are included in the cheap
route inventory, with its temporary xfail removed. They do not expand the
original 40-case loss matrix and have no claimed request-loss/restart coverage.
Wrap-up verification uses the normal offline suite and three fixture-only G3
live tests, including edit/reapproval and both submit-claim crash windows.

### Final focused verification

- Rebased cleanly onto origin/main (f88b1e1).
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`: **602 passed,
  99 skipped, 6 deselected, 4 expected xfails in 113.41 s**. The xfails are
  AUDIT-027, RT-02 (two cases), and RT-09. An earlier run collected the stale
  inventory xfail and XPASS-failed; the final run above removes that marker.
- `RUN_G3=1 .venv/Scripts/python.exe -m pytest
  tests/integration/test_g3_click_to_submit.py -m live -q -p no:cacheprovider`:
  **3 passed in 226.90 s** (normal submission, edit/reapproval with before-claim
  crash, after-claim recovery without repeat submit). Synthetic fixtures only.
- Node/route inventory: **2 passed**. Scoped Ruff check/format, compileall and
  `git diff --check`: passed. Installed dependency pip-audit: no known
  vulnerabilities; unpublished local hulchul-operator package skipped.
- No additional long matrix run or new request-loss scenarios. Test-generated
  eval timestamp restored; packaging metadata is excluded from the commit.
