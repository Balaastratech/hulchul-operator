# Assignment brief → numbered requirements

Source: `private/Hulchul-AI-Engineering-Assignment.pdf` (local-only) (1 page) + invitation message. Deadline **4 Oct 2026, 11:59 PM IST**. Aim 8–12 focused hours; stop after 12; explain unfinished parts.

## The ask
"Build a small working prototype that accepts a plain-English goal and completes a meaningful business task" by operating applications. Must use **browser or desktop interaction alongside files or another application**, in a **test environment you control**, with **synthetic data**, **no credentials in the submission**, and state any model/account requirements.

## Requirements (every one gets a test or a demo proof — see TEST_PLAN)
| ID | Requirement (brief wording) | Our interpretation | Proof |
|---|---|---|---|
| R1 | **Working execution** — performs the task, usable result, shows actual application interactions | Real browser fills real, never-seen forms; Drive data read live; result = filled form + review summary + application ledger | Video + evidence screenshots |
| R2 | **Adaptability** — variation in goal or input *without changing code* | Edit the Drive rules/profile/answers (or the goal sentence) → different shortlist and answers, same code | Video segment 2, eval table |
| R3 | **Recovery** — meaningful failure/interruption; recover or explain a concrete blocker; avoid duplicate/unintended actions | Kill the worker mid-form and resume with zero refills; CAPTCHA/login → human handoff → resume; duplicate application blocked | Video segment 3, tests |
| R4 | **Verified completion** — check resulting state/artifacts, show evidence, make incomplete work visible | Field read-back diff before approval; post-submit confirmation check; final status COMPLETED / PARTIAL / BLOCKED with reasons | Report + screenshots |
| R5 | **Human control** — show progress, allow pause, seek approval beyond granted authority | Live progress, Pause button, approval gate before Submit, targeted edits with re-approval | Video + communication matrix |

## Deliverables
| ID | Deliverable | Notes |
|---|---|---|
| D1 | Source + setup instructions so they can run it | README with one-command setup; list model/account requirements; reviewers bring their own Gemini API key |
| D2 | Demo video ≤ 5 min showing the task, a variation, and the failure case | Script in `08-submission/SUBMISSION_CHECKLIST.md` |
| D3 | Brief engineering note: choices, personal contribution, tools/AI assistance, limitations, next steps | Outline in checklist; disclose every AI tool used |
| D4 | Reply in Internshala chat: repo link (accessible), viewable video link, **current start date**, **availability for 6-month internship, ≥30 focused hrs/week**; ask for any timing adjustment BEFORE the deadline | Only the user can supply start date/availability |

## Assessment lenses (how they will judge)
Usefulness & ambition · Execution quality · Reliability · Engineering judgment · How well I understand and own it (be ready to explain and modify code live).

## What this implies for design
- "Operator that completes the work with evidence" → verification and evidence are first-class, not afterthoughts.
- "Seeks approval when an action exceeds the authority given" → explicit authority tiers enforced in code (POLICY_AND_SAFETY).
- "Be ready to explain and modify the code" → keep the system small, readable, and owned. Prefer few files over clever frameworks.
