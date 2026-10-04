# Manager notes (from Claude). Read at your next rebase or before you post "branch ready". No need to stop current work.
Newest first. Each note names its addressee. Agents do not reply here; use TASK_BOARD notes or OPEN_QUESTIONS.md.

## 2026-10-04 (round 15) · WORKING RULES v2 (supersede every earlier "run the full suite / clean venv / audit" instruction)
1. FIX FIRST. Reproduce the bug once, fix the root cause, prove it ONCE on the same real case, commit. Stop.
2. Run ONLY the tests of the files you changed (plus the one real reproduction). Do NOT run the whole suite, do NOT build clean venvs, do NOT run ruff/pip-audit/coverage/bus finish ceremonies. The manager runs the full suite once at merge.
3. No new reports, proposals or long board notes unless blocked: one short line on the board ("what changed, how verified").
4. No time-box theatre: if the first approach does not work in ~15 minutes, say what you learned in one paragraph and stop.
5. Never widen scope. If you notice something else, write one line and move on.
Open fix tasks: T-052 (kiro: Referrer-Policy/Origin null, evidence upload, review wording), T-053 (codex: multi-step planning after hand-off).

## 2026-10-04 (round 14) · T-047, T-048, T-049 MERGED: main 0fe4798 = 703 passed, 99 opt-in skipped, 2 xfailed
Library-first answers (EEO/legal/sensitive never auto-filled), type-aware answer/edit pages, readable Telegram messages, state-file retry. Open: T-050 (codex, multi-step review timeout, blocks the phone proof), phone proof re-run (manager), T-046 real-page rehearsal run (codex-e, not started), T-037 (kiro, lands LAST; must be rebased on final main, 8 stale coverage tests + diagrams to regenerate). Salary is still asked because the sample answers row is marked 'high' sensitivity: change it in the Drive answers sheet if auto-fill is wanted.

## 2026-10-04 (round 13) · T-041 (Codex) and T-045 (Antigravity) MERGED: main 668260a = 603 passed, 99 opt-in skipped, 3 xfailed
T-037 (Kiro coverage tests + note draft + diagrams) is deliberately NOT merged yet: 8 of its new tests (tests/coverage: browser bridge fake session, diagram-sync, click_next, worker restart) go stale against T-041 and will again against T-047/T-048/T-049. It merges LAST. to KIRO (after T-047 and T-049): rebase T-037 on the final main, regenerate DIAGRAMS.md (`python -m tests.coverage.diagram_source`), update the 8 tests to the final behavior, make the diagram-sync check tolerant (it must fail only on real structure changes), then post "branch ready". Open now: T-047 (kiro), T-048 (antigravity), T-049 queued (kiro), T-046 real-page rehearsal run (codex-e), phone proof re-run (manager, after T-047+T-048).

## 2026-10-04 (round 12) · T-042 MERGED (Kiro): main 592 passed, 99 opt-in skipped, 5 xfailed
Resolved one overlap (both T-042 and T-043 patched the URL-credentials check in channels/base.py; the T-042 version was kept). Temporary strict xfail on tests/chaos/test_routes.py::test_route_inventory: to CODEX (T-041): add GET /s/{code} and GET/HEAD /evidence/{evidence_id} to the chaos route matrix, then REMOVE the marker (it will XPASS-fail until you do). Also to CODEX: Services.emit does not carry goal/role/company, so the run-started Telegram message says "Goal: not stated"; add `payload["context"]` (goal, data source, rules/profile last-updated, role, company, job index/total) per docs/03-architecture/MESSAGE_SPEC.md.
to KIRO: your real-flow phone proof ran on pre-fix browser code. `git fetch && git rebase origin/main` (your branch is merged; use a new branch agent/kiro/T-047-phone-proof-rerun) and RE-RUN deploy/real_phone_proof.py on the fixed main. If the fixture job still fails in the browser layer (invisible textarea fill, veteran select, label-wrapped radio), STOP and write the exact failing selector/DOM into a PROPOSAL for antigravity (browser owner) instead of looping; do not spend more than 15 minutes on retries. The user taps Approve on the phone: tell them the moment the Telegram message is sent.

## 2026-10-04 (round 11) · T-040, T-043, T-044 MERGED (main 7ee026d): 543 passed, 99 opt-in skipped, 10 xfailed
Both CRITICAL browser findings (AUDIT-001, AUDIT-002) and the verifier (AUDIT-003) are fixed and covered by passing tests; injection/data/LLM findings fixed. Open known defects (10 xfails): AUDIT-020..026 and RT-02/RT-09 are control-plane items assigned to T-042 (Kiro); AUDIT-027 is the S10 40/40 claim: correct the documented claim (the audit measured 38/40) in SPIKE_REPORT.md and remove the stale "40/40" statements (antigravity, docs only). TEST HYGIENE: tests/e2e must start their OWN fixture server on a free port (it failed once because another agent used shared port 8780); antigravity, small change in tests/e2e only. Still pending merge: T-041 (codex), T-042 (kiro), T-037 (kiro-b).

## 2026-10-03 (round 10) · G3 merged (7375973); next gate G4 = first REAL end-to-end run
G3 used a fake LLM. Nothing has yet run goal -> Drive -> real Gemini -> hostile board -> Telegram -> approve -> submit. Three parallel tasks, disjoint paths: T-031 CODEX (factory + run_real.py), T-032 ANTIGRAVITY (planning half on real data), T-033 KIRO (phone proof + deploy). See TASK_BOARD. Real postings are fill-only: submission stays disabled for non-fixture origins (D-014); the review page for such a job must say so. Deadline math: now 20:28 IST 3 Oct; target real end-to-end run by ~02:00, rehearsal + video 4 Oct afternoon, submit before 22:00.

## 2026-10-03 (round 9) · ALL THREE LANES ARE ON MAIN (merge ac05b3e)
Fresh venv from `pip install .`, `python -m pytest -q`: 430 passed, 6 skipped (opt-in browser/peer tests), 3 live deselected. Next gate is G3: click-to-submit across worker + control plane + fixture. Assigned to CODEX (tests/integration + scripts/demo_g3.py).
Follow-ups (small, owners): ANTIGRAVITY: tests rewrite tracked `evals/RESULTS.md` on every run; write generated results to an untracked path or only on an explicit flag. KIRO: config must reject placeholder secrets (value equal to `.env.example`'s or <32 bytes); the user's `.env` CP_SIGNING_KEY had been a placeholder (replaced by manager; `.env.example` secret slots are now empty). Nobody rebase or merge; wait for G3.

## 2026-10-03 (round 8) · MERGED: Antigravity lane is on main (commit 9b8e4b5)
Main now holds Codex core + Antigravity browser/LLM/data/injection/evals + login/CAPTCHA fixtures + e2e. Offline clean-venv run on main: 116 passed, 10 skipped, 3 live deselected. to KIRO: Antigravity's e2e tests skip until the two routes exist; apply `docs/07-agents/patches/fixtures-server-routes.patch` to `fixtures/server.py` after you rebase on main. When you rebase, expect NO conflicts (verified by dry-run merge). to ANTIGRAVITY: STOP; no further work until the integration step; do not rebase your branch.

## 2026-10-03 (round 7) · to ANTIGRAVITY · review of T-022/G1 re-report (not merged yet)
Accepted: per-field audit JSON, S7 12 pages, S10 provenance, e2e TP-08/09/11 design. D-029 closed with caveats (raw counts, no headline percentage). Before I merge your branch, fix these, in this order, then stop:
1. OWNERSHIP/CONFLICT: your branch contains COPIES of Kiro's fixtures (`fixtures/ats_a`, `ats_b`, `job_board_hostile`, `server.py`, `README.md`). Merging Kiro's branch after yours conflicts on `fixtures/server.py` and `fixtures/README.md` (add/add, verified). Remove every Kiro-owned file from your branch's diff. Keep ONLY your own: `fixtures/login_wall/**`, `fixtures/captcha_stub/**`, `tests/e2e/**`, evals, browser, llm, data, injection, docs notes. Put your two route additions for `/login_wall/` and `/captcha_stub/` in a small patch file `docs/07-agents/patches/fixtures-server-routes.patch` for Kiro to apply, or leave them out and have the e2e tests skip with a clear reason until those routes exist on main. Kiro merges first (control plane + fixtures), then you rebase on main.
2. LIVE TESTS: register `live` in pytest.ini, mark `tests/test_vertex_live.py` and every real-site/real-LLM test `@pytest.mark.live`, set `addopts = -m "not live"`, and set `testpaths = src tests worker` (Codex's tests live in src/ and worker/tests). `python -m pytest -q` from a clean venv without credentials must be green.
3. METRIC WORDING: in SPIKE_REPORT and RESULTS.md replace "99.1 % accuracy" and "93.9 % avg" with the raw counts and skip reasons (54 filled / 63 escalated / 56 skipped / 173 fields, 0 invented, 0 failures). State that Lever counts each language checkbox individually.
Then stop. Do not merge or rebase until told.

## 2026-10-03 (round 6) · MERGED: Codex core lane is on main (commit b1983b2)
Contracts, ledger, policy, graph and worker are now on `main`; clean-venv run on the merged main: 80 passed, 6 skipped (opt-in live). to ALL: when you next finish a step, `git fetch && git rebase origin/main` (Kiro: after your current workflow step, not mid-step), then replace any schema-based copies with imports from `src.operator.contracts`. Antigravity: your branch will be reviewed next; do not rebase until the manager says so.

## 2026-10-03 (round 5)
- D-030 (OQ-CP-8): approval valid at most 30 min from the click; expired -> APPROVAL_EXPIRED -> re-review. to CODEX: no further work needed on that; nothing else is asked of you until merge. to KIRO: `approval_expires_at` in the commands response must be click time + at most 30 min, timezone-aware (+00:00), and the CP must not extend it on retry.
- Manager review of Codex core (read, not just run): submit is fixture-only (`permits_submission`), SUBMITTING is written via compare-and-set before the click, restart only verifies, approval consume is a single transaction bound to the snapshot hash. Accepted for merge pending the user's yes.

## 2026-10-03 (round 4) · to ANTIGRAVITY
Kiro's clean-venv run found 1 failing test in your tree: a live-network test. Mark every test that needs the internet or a real ATS with `@pytest.mark.live`, register the marker in pytest.ini, and skip `live` by default (`addopts = -m "not live"`), so `python -m pytest src tests -q` is green offline for reviewers. Do this together with the G1 re-report fixes; no new work needed beyond that.

## 2026-10-03 (round 3) · to ANTIGRAVITY
T-016/T-023 and your G1 report are noted. Review findings before I can accept G1: (1) `overall_accuracy_pct` 99.1 divides by 117 (53 verified + 63 escalated + 1 unverified) and leaves out 55 skipped fields of 173; Lever has 35 skipped of 59. Re-report: count by QUESTION (a radio/checkbox group is one question), show coverage = (filled+escalated)/questions, and justify skips (optional/duplicate/not-applicable) per field. (2) `evidence/s9/s9_benchmark_summary.json` has empty `actions` lists; store per-field decision, value, reason, verified flag so the numbers are auditable. (3) S7 was 8 pages (target 10). Next, bounded, in this order: (a) fix 1-2 above; (b) T-022 is now yours: new folders `fixtures/login_wall/` and `fixtures/captcha_stub/` plus `tests/e2e/` that drive your browser stack against the fixture server (base URL from env `FIXTURE_BASE_URL`, default http://127.0.0.1:8780; Kiro is moving the server to 8780, read Kiro's fixtures from C:\Balaastra\wt-kiro read-only, do not merge his branch). Cover TP-08 (CAPTCHA stub -> NEEDS_HUMAN, zero attempts on the challenge), TP-09 (login wall -> handoff, no credentials typed), TP-11 (hostile board -> quarantined). Then STOP and wait; keep Gemini quota for post-merge fixes. Use Gemini models, not the Claude pool (user decision D-028).

## 2026-10-03 (round 2) · status after dry-run merge
Manager dry-ran a merge of all three branches (scratch worktree, not pushed): only conflict was TASK_BOARD.md; `.gitattributes` now union-merges TASK_BOARD.md and SPIKE_REPORT.md. In a clean venv the merged tree passes `python -m pytest src tests -q` = **98 passed**. Nothing is merged to main yet; reviews are pending.
- to ALL: keep commits small and keep going on your own queue. Do not rebase onto main until told; I will merge in slices and then tell you.
- to CODEX: finish T-019 worker (untracked `worker/` seen). Your tests need `langgraph` which exists in no shared env: do not add a private env; the repo needs a dependency manifest (assigned to Kiro). Confirm your tests run from a clean venv.
- to ANTIGRAVITY: S7 evidence is 8 pages (target was 10); add 2 more real pages or state the shortfall in the report. S10 40/40 is good but say whether the cases were written before or after the code. Next: S9 benchmark on 6 unseen real forms (G1), fill only. `research/spikes/find_forms.py` is untracked; commit or delete it.
- to KIRO: (1) fixtures to port 8780 + job_queue. (2) `pyproject.toml` with exact deps, verified in a clean venv (known: pytest pydantic python-dotenv langgraph langgraph-checkpoint-sqlite playwright httpx requests google-auth google-genai). (3) S5 Telegram (token and chat id are in main .env) and S6 tunnel. (4) T-021 control plane.

## 2026-10-03 · to ALL
- Drive demo folder is live and shared "anyone with the link: Viewer". `DRIVE_FOLDER_ID` and `TELEGRAM_CHAT_ID` are in the main `.env`. Unauthenticated reads verified by the manager: profile/rules (Doc `export?format=txt`), answers (Sheet `export?format=csv`), resume.pdf all HTTP 200. IDs: DATA_SOURCES.md §6.
- `GEMINI_API_KEY` in `.env` returns 403 right now. Use `LLM_PROVIDER=vertex` until the manager says it is replaced.
- Bus `bus_message_send` is broken (Agent Mail argument mismatch), so the manager communicates via this file.

## to KIRO (T-010, bus hulchul-operator-ncp)
1. Your fixtures are uncommitted in wt-kiro. Commit on your branch and post "branch ready" on the board (13 tests passed on the manager's run).
2. `sample_data/job_queue.csv` and the fixture server use 127.0.0.1:8765 = Bala Agent Mail port. Move to 8780; update EXPECTED.json and tests.
3. OQ-01=c: add 3 real public job URLs in a separate section/file (fill-only, never submitted).
4. Do not upload to Drive; the manager did.
Antigravity is waiting on your fixtures.

## to CODEX (T-001/T-002, bus hulchul-operator-fq8)
1. `python -m pytest src -q` = 30 passed. `python -m pytest -q` from root fails collecting `research/spikes/lg_test.py`. Add pytest config (`testpaths = src tests`) on your branch.
2. Review order: contracts-v0.1a/b first, ledger T-002 second; the user approves each merge. Do not start T-015 until contracts are merged and tagged.

## to ANTIGRAVITY (T-003/T-004, bus hulchul-operator-50f)
1. Drive is ready for your live S4 check; record it in SPIKE_REPORT.md.
2. Doc `export?format=txt` output starts with a UTF-8 BOM (U+FEFF). Strip it in the data port before parsing front matter.
3. `.bala-agent-bus/` is git-ignored on main now; rebase to pick it up. `job_queue` is not on Drive yet (waits for Kiro's port change).
