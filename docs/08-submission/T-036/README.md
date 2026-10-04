# T-036 run report

Open `report.html` directly in Chrome. It is one portable file: inline CSS,
embedded local PNG/JPEG/WebP evidence, no remote assets or network calls.
`report-preview.png` shows the opening view for the demo video.

From the repository root:

```powershell
python -m src.operator.report <run_id> --source <saved-run-directory>
python -m src.operator.report --latest --source <parent-of-saved-runs> --output report.html
python -m pytest tests/report -q
python -m tests.report.make_g3_sample
```

The default source is the current directory. The reader discovers only named
`state.json`, `events.jsonl`, `ledger.sqlite` and optional `cp.sqlite`,
`control-plane.sqlite` or `control_plane.sqlite` artifacts. SQLite opens read-only;
rows are filtered by run ID. `--latest` prefers the newest saved state, then
recorded event/approval times; undated multi-run databases require an explicit ID.
No checkpoint deserialization, .env access, worker commands or submissions occur
when generating a report. Unknown runs fail without creating a report.

The sample came from the actual G3 harness with real local HTTP gates and system
Chrome, a synthetic candidate and a deterministic fake LLM. It edited the email,
rejected stale approval and replay, paused/resumed, killed the worker before the
submit claim, restarted and verified exactly one fixture submission. A temporary
copy of the harness used a free fixture port because port 8780 was occupied.
No real employer, Telegram or paid LLM service was contacted.

Read-back mismatches, generated drafts and unanswered fields are visible;
historical snapshots are expandable in the timeline and retained in ledger rows.
The report shows recorded graph counters, including zero token/cost counters in
the fake-LLM sample. Milestone intervals include human waits and are not node
execution timings. Missing screenshots or measurements are labelled. Approver
identity is not persisted by the current schemas, so it says “not recorded”;
the control-plane click time and worker consumption time are distinct.

Page-derived text is escaped; CSP blocks scripts, forms and remote resources.
Credential keys, credential-shaped text, query capabilities and known credential
echoes are redacted; hashes display only a prefix. Only local raster evidence
under artifact directories embeds. Screenshot pixels are unchanged trusted run
captures: generate shareable reports from synthetic runs, as this sample does.
