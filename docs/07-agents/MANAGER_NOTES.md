# Manager notes (from Claude). Read at your next rebase or before you post "branch ready". No need to stop current work.
Newest first. Each note names its addressee. Agents do not reply here; use TASK_BOARD notes or OPEN_QUESTIONS.md.

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
