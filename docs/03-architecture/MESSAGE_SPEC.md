# Message spec: what the human reads

Supersedes the Telegram template in `COMMUNICATION_MATRIX.md` section 3 (new file; the matrix is unchanged).
Code: `src/operator/channels/messages.py` (texts), `context.py` (names and numbering), `telegram.py` (delivery).

## Rules
1. Plain language. Company and role names, never `E07`, `job-004` or a run id. `plain()` rewrites or removes them.
2. Every dynamic value is clipped and HTML-escaped. A worker's `event.links` is never used.
3. URL buttons only, no `callback_data`: chat can never approve. E07/E08 have “Review & approve” and “Edit a field” buttons to the same review page (the latter adds `#edit`), as implemented by change/change. Other messages have at most one URL button.
4. Links are short and opaque: `https://<CP_BASE_URL host>/s/<13 characters>`. The code holds no token and no id.
   `GET /s/<code>` only reads: it mints a fresh VIEW token (pure, never stored) whose life cannot outlast the link,
   then answers `302` to `/r/<run>[/<job>]?t=...`. Codes are an HMAC over (run, job, 15-minute bucket) and work for
   24 h to 24 h 15 min. Approving is still a POST on the review page with a single-use token (30 min, bound to the snapshot).
5. If `CP_BASE_URL` is localhost, loopback or a private address a phone cannot open it, so the message carries no link
   and says "no public address is set". `CP_LOCAL_URL` is never used for links.

## Texts
| Event | Message | Button |
|---|---|---|
| E01 Run started | `Goal`, `Data from` (Google Drive folder / local sample folder), `Last updated: profile ... · rules ...` | Open progress page |
| E02 Shortlist | `Shortlist: 3 roles`, then `1. Role — Company` with `Why: <one line>` for each | View shortlist |
| E03 Blocked | `N postings blocked` / `Hidden instructions, not used.` (no excerpt, no rule name) | See details |
| E04 Handoff | `Action needed in the browser`, reason (login, legal confirmation or human check), then press Done after completing it yourself | Done |
| E05 CAPTCHA / handoff | `Action needed in the browser`, reason; do the check yourself (never solved automatically), then press Done | Done |
| E06 Question | `Question`, `Why I am asking`, `Suggestions`; a Telegram reply answers it | Answer |
| E07 Ready | see below | Review & approve; Edit a field |
| E08 Edit applied | same body as E07 under `Change saved, please review again` | Review & approve; Edit a field |
| E09 Submitting | `Submitting now`, role, `You approved this at <time>.` | none |
| E10 Verified | role, `Confirmation: "<text>"`, `Reference number` | View evidence |
| E11 Not verified | `Submitted, but I could not confirm it`, `What I saw`, `Please check the application yourself.` | Open details |
| E12 Failed | role, `Why: <concrete reason>`, `Last step that worked`, `I tried again n times.` | See details |
| E13 Paused/Resumed | `Nothing is sent while paused.` | Resume / Open progress page |
| E14 Summary | `Run finished`, `Submitted and verified: n`, one line per role with a plain status, cost and minutes | Open summary |
| E15 Offline | `Your computer is offline`, minutes since the last heartbeat | none |

### E07
```
📝 Ready for your review
Role: <title> — <company>
Job <n> of <N>
✅ <x> fields filled · ❓ <y> need your answer · ⏭ <z> left blank by your rules
⚠️ Please check: <generated-text fields>
Not filled by rule: <EEO fields>
The link works 24 h; your approval is valid 30 min after you press Approve.
```
Counts come from the review snapshot (`payload.review_snapshot`, or `payload.review` from the graph), grouped by question:
radio/checkbox alternatives count once and duplicate uploads count once. Matched answers are filled; escalated/unmatched
questions need an answer; explicit policy skips are left blank by rule. Names use labels/group text rather than raw stable
keys, and the first five unanswered questions are listed. Derived values cite their source on the review page and in messages.

## Where names come from (`context.py`)
Order: `payload["context"]` -> provider passed to the channel -> plain payload keys -> what the channel saw earlier
in the run (E02 order gives "Job 2 of 3", E03 count) -> the data folder's `job_queue*.csv` (`LOCAL_DATA_DIR`).
A missing fact drops its line; the review message then says `Role: this application`.

`payload["context"]` keys (all optional, text only): `role`, `company`, `job_number`, `job_total`, `goal`, `data_source`,
`profile_updated`, `rules_updated`, `blocked_count`, `shortlist` (`[{role, company, reason}]`), `names` (`{job_id: "Role — Company"}`).

**Open request to the graph owner (`src/operator/graph/runtime.py`):** today `Services.emit` sends no goal, no
role/company and no numbering, so a real run only gets names from the CSV fallback. Adding
`payload["context"] = {"goal": run.goal, "role": ..., "company": ..., "job_number": ..., "job_total": ..., "shortlist": [...]}`
built from `RunState` (`run.shortlist` holds the postings and their reasons) makes every message complete, including
the goal in the run-started message. This task did not edit that file (not owned).
