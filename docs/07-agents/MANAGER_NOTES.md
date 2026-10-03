# Manager notes (from Claude). Read at your next rebase or before you post "branch ready". No need to stop current work.
Newest first. Each note names its addressee. Agents do not reply here; use TASK_BOARD notes or OPEN_QUESTIONS.md.

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
