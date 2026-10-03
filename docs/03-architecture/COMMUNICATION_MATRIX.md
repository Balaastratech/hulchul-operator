# Communication matrix — how the system talks to the human

Every message has: **event id, trigger, audience, channels, payload, link(s), expected response, timeout/escalation.**
Channels: **T** Telegram, **W** web review page, **L** live progress (SSE on W), **WA** WhatsApp (adapter later, D-007).

## 1. Link rules (D-008)
| Link | URL shape | Method | What it does | Token |
|---|---|---|---|---|
| Review | `/r/{run}/{job}?t=…` | GET | shows read-back form values, screenshots, unanswered fields, generated texts flagged | view token (multi-use, expires 24 h) |
| Approve & Submit | page button → `POST /api/approve` | POST | records approval for **this snapshot hash** and releases the gate | action token: single-use, ≤30 min, bound to `run+job+snapshot_hash` |
| Edit field | form on review page → `POST /api/edit` | POST | changes one field, invalidates approval, triggers re-review | action token |
| Answer question | `POST /api/answer` or Telegram reply | POST | supplies a value for `ask_user` field | action token |
| I'm done (login/CAPTCHA) | `POST /api/handoff_done` | POST | operator re-classifies the page and resumes | action token |
| Pause / Resume / Cancel | `POST /api/pause` etc. | POST | halts after the current atomic action | run token |
| Open live browser | instruction text only ("switch to the Chrome window titled …") | — | v1 browser is local; remote view is future scope | — |

"Click the link, click Submit" = **two clicks by design**: the first only views; the second is the explicit POST. Mail and chat apps prefetch links, so a one-click GET-submit would be a bug.

## 2. Events
| ID | Trigger | Channels | Payload | Link(s) | Response expected | Timeout / escalation |
|---|---|---|---|---|---|---|
| E01 | Run started | T, L | goal, mode, data snapshot hash + files used | Review (run) | none | — |
| E02 | Shortlist ready | T, W | chosen jobs + reasons + skipped + why | Review (run) | optional "skip job N" | proceeds automatically after 60 s unless `confirm_shortlist` is on |
| E03 | Injection quarantined | T, W | job id, offending excerpt (escaped), rule matched | Review (job) | acknowledge | none; run continues without that job |
| E04 | Login required | T, W | site, what the operator saw, what to do | Handoff-done | log in in the visible Chrome, press done | 15 min reminder; 60 min → job `NEEDS_HUMAN` parked, next job continues |
| E05 | CAPTCHA / human check | T, W | same; states "outside my authority" | Handoff-done | solve in Chrome, press done | same as E04 |
| E06 | Question the operator cannot answer | T, W | field label, why unknown, suggestions from AnswerLibrary | Answer | value or "skip" | 30 min → job parked |
| E07 | **Ready for review** | T (summary), W (full) | every field as read back vs intended, uploads, generated text flagged, unanswered list, screenshot, snapshot hash | **Review + Approve & Submit**, Edit, Reject, Pause | approve / edit / reject | token expires 30 min → `APPROVAL_EXPIRED`, form stays open until user returns |
| E08 | Edit applied | T, W | old → new, other fields untouched, new hash | Review + Approve | re-approve | as E07 |
| E09 | Submitting | L, T | "approved by you at HH:MM for hash ab12…" | — | none | — |
| E10 | Submitted & verified | T, W | evidence: confirmation text, application id, screenshot, ledger row | Review (job) | none | — |
| E11 | Submitted, not verified | T, W | what was clicked, what was seen, what is unknown | Review (job) | user checks manually | stays visible in report |
| E12 | Job failed / blocked | T, W | concrete blocker, last good step, retries used | Review (job) | decide retry / skip | — |
| E13 | Paused / Resumed | T, L | by whom, at which step | Resume | resume | — |
| E14 | Run summary | T, W | status (COMPLETED/PARTIAL/BLOCKED/CANCELLED), per-job table, cost, time, what remains | Review (run) | none | — |
| E15 | Worker offline | T | last heartbeat age | — | start worker | 10 min without heartbeat |

## 3. Telegram message template for E07 (short, scannable)
```
✅ Ready to review — Backend Engineer @ Acme (job 2/3)
Filled 31/34 fields · 2 need you · 1 skipped
Generated text: "Why us?" (flagged)
Not touched: gender, ethnicity (left blank by rule)
→ Review: https://<host>/r/run123/job2?t=…   (expires 24h)
Approve button is on that page (expires 30 min).
```
Edits and approvals are never done by replying "yes" in chat alone, because a chat message cannot be bound to a snapshot hash.

## 4. Progress visibility (R5)
Live timeline on the web page: step name, status, duration, screenshot per step, LLM calls so far, current job, pause button. The Telegram bot sends only decisions needed and milestones, not every step.

## 5. Failure of the channel itself
If Telegram send fails: retry with backoff; fall back to W; if no channel can reach the user, the worker **pauses at the next gate** (never proceeds autonomously) and logs `CHANNEL_UNREACHABLE`.
