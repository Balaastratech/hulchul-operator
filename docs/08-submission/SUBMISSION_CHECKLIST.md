# Submission checklist (deadline 4 Oct 2026, 11:59 PM IST — target done by 10:00 PM)

## Deliverables
- [ ] **Repo link** reviewers can open (OQ-06). No secrets, no real personal data; `.env.example` only. Clean-checkout run verified.
- [ ] **README**: no-keys fixture setup (Python ≥3.13, system Chrome, setup script / `pip install .`); separate model-backed run, optional Telegram + Drive, requirements and honest limits.
- [ ] **Demo video ≤ 5:00**, viewable link (YouTube unlisted / Drive link). Test the link in a private window.
- [ ] **Engineering note** (1–2 pages): see outline below.
- [ ] **AI-assistance disclosure** in the note: which tools (Claude Code, Codex, Kiro, Antigravity, Gemini) did what; what the user wrote/decided/reviewed; confirm the user can explain and modify.
- [ ] **Reply in Internshala chat**: repo link + video link + **current start date (04-10-2026, decided)** + **availability for a 6-month internship, ≥30 focused hrs/week** + any timing adjustment (must be raised BEFORE the deadline).

## Video script (≤ 5:00)
| Time | Segment | Shows | Req |
|---|---|---|---|
| 0:00–0:30 | Problem + architecture in one slide: model proposes, code disposes | LangGraph diagram, control-plane/worker split | — |
| 0:30–2:10 | **Task**: goal sentence → Drive data loaded → shortlist with reasons → real unseen form being filled in the visible browser → review summary lands in Telegram → click link → review page with read-back values → **Approve & Submit** (on fixture) → confirmation verified → ledger row | actual interactions, evidence | R1 R4 R5 |
| 2:10–3:10 | **Variation**: edit the Drive `rules`/`answers` (and goal sentence) with no code change → different shortlist and answers; also show the hostile job post being quarantined | R2 + safety | R2 |
| 3:10–4:25 | **Failure**: (a) kill the worker mid-form → restart → resumes with zero refills; (b) CAPTCHA-stub/login fixture → "outside my authority" → user completes → resume; (c) duplicate application blocked | R3 | R3 |
| 4:25–5:00 | Pause + edit-one-field + re-approve in 10 s; honest limitations; what's next (FUTURE_SCOPE) | R5 | R5 |

Rules: real unseen employer forms are filled but **never submitted** (state it on screen). If a live segment fails, use a rehearsal recording and say so.

## Engineering note outline
1. Problem and why this workflow (Raj's candidate side; unseen forms are the real difficulty).
2. Architecture: LangGraph agents, control plane vs worker, ports/adapters.
3. **What the model decides vs what code decides** (central section).
4. Reliability: checkpoints, CDP reattach, idempotency keys, `SUBMITTING` semantics, replay with 0 LLM calls.
5. Human control: tiers, signed single-use approval bound to snapshot hash, POST-only submit, edits, pause.
6. Safety: no CAPTCHA solving (why), injection quarantine, domain allowlist, EEO/legal rules.
7. Measured results: link to `SPIKE_REPORT.md` and `evals/RESULTS.md` (numbers, not adjectives).
8. Limitations (be specific): supported ATS list as measured, no account creation/OTP, local browser, single user, Drive-public data path, etc.
9. AI assistance and personal contribution.
10. Next steps → `FUTURE_SCOPE.md` (inbox/OTP, adapters, answer-library UI, remote browser, WhatsApp).

## Final pre-submit gate
- [ ] Confirm `private/` is not in the repo or any archive (company research, brief PDF)
- [ ] Fresh clone → setup → fixture demo passes on the user's PC
- [ ] Manager verifies integration tests/evals at merge; T-037 runs only its one fresh-clone setup/doctor/demo proof, not a full suite
- [ ] grep repo for keys/tokens/emails/phones that aren't synthetic
- [ ] Control-plane link from phone works (or documented as local-only)
- [ ] Video under 5:00 and plays logged-out
- [ ] Message to Hulchul drafted and approved by the user before sending

## T-037 public-repo hygiene review (4 Oct 2026)

`git ls-files` on base `739e6c1` contains 372 files. No tracked `.env` (except the empty template), private folder, run folder, venv, build output or egg-info; no file exceeds 5 MiB. A credential-pattern scan found only the explicitly fake token in `tests/channels/support.py` used with MockTransport. Sample profiles/resumes are documented synthetic. Retained screenshots, phone proof, redacted spike logs and public posting URLs are evidence and remain in Git. This is a scoped inventory/pattern inspection, not a guarantee about every historical commit.

Keep credentials, personal candidate data, browser profiles, runtime SQLite databases, runs, egg-info, build output, caches and generated evaluation output out of new commits. `.gitignore` now covers packaging, alternate venvs, WAL files and generated eval output. Already tracked `evals/RESULTS.md` and `evals/real_forms/artifacts/latest/` are retained baseline evidence: ignore rules do not stop tracked-file churn. Restore incidental rewrites before committing; moving future generated results to an untracked path is an evaluator-owner follow-up. No evidence was removed and no LICENSE was added.

The final-main branch contains **no `tests/coverage/` files and no `DIAGRAMS.md` sync test**. The eight stale tests mentioned in earlier manager notes belong to an unmerged historical T-037 branch; they were not imported. The maintained architecture picture is in the README with links to the design docs.

## T-037 fresh-clone proof (4 Oct 2026)

One local `git clone --no-hardlinks --branch agent/codex/T-037-final-docs C:/Balaastra/wt-codex <temp>/repo`
of implementation commit `f99cd0b`, on Windows with Python 3.13 and system Chrome. No full suite, audit or eval was run.
Followed the README's `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1`;
it created a new venv, ran `pip install .`, copied the template and ran doctor successfully.
Downloads used the local pip wheel cache; this is not a cold-network timing measurement.

Then moved the copied template to `.env.setup-template`, cleared inherited `G3_*`, `CP_*`, `GOOGLE_*`,
`GEMINI_*`, `TELEGRAM_*`, `LLM_*`, `DATA_*`, `REAL_*`, `ENV_FILE` and `LOCAL_DATA_DIR` variables,
and ran the exact README configuration and demo:

```powershell
$env:ENV_FILE = Join-Path (Get-Location) '.env.demo-missing'
$env:CP_BASE_URL = 'http://127.0.0.1:8790'
.\.venv\Scripts\python.exe scripts/doctor.py --offline
.\.venv\Scripts\python.exe scripts/demo_g3.py --headless --no-telegram
```

Both `.env` and the selected ENV_FILE were absent. No model/Telegram request or `.env` value was printed.
Optional tools happened to be installed, but were unused. Sanitized output (view capability suppressed):

```text
Doctor: 0 required failure(s).
ISOLATION PASS: no .env, selected ENV_FILE missing, credential environment cleared.
Doctor: 0 required failure(s).
1. Filled ATS A; review gate paused; fixture submissions = 0
2. Review GET displayed read-back without changing any CP table; SSE without view token = 401
3. Pause survived a fresh worker with no submit; resume returned to review
4. Edited email via POST; new hash; old snapshot approval rejected = 409
5. Approved with page-issued act token and browser Origin; replay rejected = 409
6. Killed worker after approval at before_claim; Chrome remains alive
7. Fresh worker and replay: SUBMITTED_VERIFIED; fixture submissions = 1; no second click
G3 PASS: {"status": "SUBMITTED_VERIFIED", "submissions": 1}
FRESH CLONE PASS in 154.73 seconds
```

The clone's final `git status --short` was empty, including after pip created packaging output.
Online provider checks, macOS/Linux setup, Docker and phone messaging were not exercised in this proof.
