# Real public form rehearsal

Run `rehearsal-20261004-031857` · revision `5f2fb8d73ccd09e79e0ddac10a5eb3a0bcd2a54e` · Vertex `gemini-2.5-flash`.

Synthetic persona only. FILL-ONLY: zero clicks, Enter, uploads or submissions. All browser traffic is frozen before filling; write requests, beacons and sockets are blocked from startup. Public feed listings prove discovery, not application availability.

Historical guarded baseline: the production extractor/executor/classifier/verifier are used; exact fresh read-back additionally rejects permissive fuzzy positives. Click/custom widgets and uploads are explicitly withheld. No full-stack LangGraph/approval/submit or production-readiness claim. Each control is counted separately (including radio alternatives), not as a question. Missing and duplicate planner decisions fail closed. Unknown/non-form states receive no Gemini call or input.

| Site | Fields total | Filled+verified | Escalated | Skipped | Execution failures | Unverified | Blocker | Status |
|---|---:|---:|---:|---:|---:|---:|---|---|
| greenhouse | 34 | 7 | 22 | 4 | 0 | 1 | none | FILL_ONLY_REVIEW |
| lever | 62 | 4 | 53 | 5 | 0 | 0 | none | FILL_ONLY_REVIEW |
| ashby | 0 | 0 | 0 | 0 | 0 | 0 | unknown | BLOCKED |
| workable | — | — | — | — | — | — | — | NOT_RUN |
| breezy | — | — | — | — | — | — | — | NOT_RUN |
| smartrecruiters | — | — | — | — | — | — | — | NOT_RUN |
| recruitee | — | — | — | — | — | — | — | NOT_RUN |
| personio | — | — | — | — | — | — | — | NOT_RUN |
| teamtailor | — | — | — | — | — | — | — | NOT_RUN |
| bamboohr | — | — | — | — | — | — | — | NOT_RUN |

## greenhouse

Posting: https://job-boards.greenhouse.io/vercel/jobs/6129441004

Public source: https://boards-api.greenhouse.io/v1/boards/vercel/jobs (discovered 2026-10-04T03:17:28.161255+00:00).

Gemini attempts: 1; blocked browser requests: 6; site errors: none.

Page observation: See page state and saved screenshot.

escalated: 6 × Click/keyboard/custom control withheld by fill-only containment; 5 × No explicit synthetic source; human answer required; 11 × Sensitive, login/challenge or legal field; human answer required
skipped: 2 × Upload withheld: may persist candidate data before submission; 1 × Optional field without an explicit synthetic source; 1 × Click/keyboard/custom control withheld by fill-only containment
failed: 0
unverified: 1 × Fresh read-back did not exactly match synthetic source

## lever

Posting: https://jobs.lever.co/palantir/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2/apply

Public source: https://api.lever.co/v0/postings/palantir?mode=json (discovered 2026-10-04T03:17:33.393901+00:00).

Gemini attempts: 1; blocked browser requests: 0; site errors: none.

Page observation: See page state and saved screenshot.

escalated: 9 × No explicit synthetic source; human answer required; 41 × Click/keyboard/custom control withheld by fill-only containment; 3 × Sensitive, login/challenge or legal field; human answer required
skipped: 1 × Upload withheld: may persist candidate data before submission; 4 × Optional field without an explicit synthetic source
failed: 0
unverified: 0

## ashby

Posting: https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application

Public source: https://api.ashbyhq.com/posting-api/job-board/ashby (discovered 2026-10-04T03:17:36.520060+00:00).

Gemini attempts: 0; blocked browser requests: 0; site errors: ['application_unavailable_no_controls'].

Page observation: HTTP 200, application tab says application submission is unavailable; no form controls. Cause unproven. No typing or Gemini request..

escalated: 0
skipped: 0
failed: 0
unverified: 0

Re-run against the hardened browser (fresh browser, fresh plan, no cached fill claims):

```powershell
python scripts/rehearse_real_forms.py --sites greenhouse,lever,ashby --output evals/real_forms/artifacts/after-t040-smoke
python scripts/rehearse_real_forms.py --refresh-targets --all --output evals/real_forms/artifacts/after-t040-all
```

Detailed fields/counts: `evals/real_forms/artifacts/latest/results.json`; portable offline viewer: `evals/real_forms/artifacts/latest/report.html`. Use `--output` and `--markdown` to preserve previous runs. Gemini: one attempt per accessible form, no retry/fallback or semantic-judge calls. Costs omitted because the audited provider tariff is pending correction.
