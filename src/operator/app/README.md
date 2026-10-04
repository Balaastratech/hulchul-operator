# Real run

From the repository root, with system Chrome installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe scripts/run_real.py --goal "Apply to the 3 best-fit roles under my rules"
```

The launcher reads the one `ENV_FILE` (default `C:\Balaastra\hulchul-operator\.env`),
starts its own fixture server on 8780, guarded control plane on 8790, headed Chrome with
a separate persistent profile, and the real Worker. It prints milestones and signed
read-only review links. Use those pages for answers, human handoff and POST approval.
No application is submitted before approval, and only this launcher's fixture origin
may submit. Every other origin is fill-only; its review page hides Approve, explains
D-014, and refuses even a valid manually supplied approve capability.

`LLM_PROVIDER=vertex`, `LLM_MODEL`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION`
configure the peer LLM port; Vertex uses existing ADC. `DATA_SOURCE=local_folder` reads
`LOCAL_DATA_DIR` (default bundled `sample_data`). `DATA_SOURCE=drive_public` uses
`DRIVE_FOLDER_ID`; the documented demo file IDs are used for that exact folder because
the merged folder scraper returns no files. For another folder, optionally provide
`DRIVE_FILE_IDS_JSON` (filename -> file ID), or use peer folder discovery. Partial Drive
data explicitly falls back to local candidate files. The Drive demo's absent job queue
is supplemented by local `job_queue.csv`; `data-manifest.json` records the actual source
and original/normalized/posting hashes. Candidate files and `.env` are never rewritten.

Telegram is enabled when `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID` (or `CHAT_ID`) exist.
The worker uses the real TelegramChannel, with preview disabled and only view tokens in
messages. Local review links work on the PC; phone access requires `--use-public-url`
and `CP_BASE_URL` pointing to a tunnel forwarding to `--cp-port`. No tunnel is created
implicitly. `--no-telegram` disables delivery for a local rehearsal.

One explicit synthetic-human rehearsal, proving exactly one fixture submission:

```powershell
.\.venv\Scripts\python.exe scripts/run_real.py --goal "Apply to the 3 best-fit roles under my rules" --data-source drive_public --auto-fixture --public-job-id public-job-002
```

`--auto-fixture` is a fixture-only human actor: it completes fixture consent/eligibility,
POSTs the CP-issued signed approval for the first ready job, and rejects other selected
jobs after review. Its actions are outside the planner's authority. It never completes
a public site's legal/login/CAPTCHA control or approves a public application.
`--public-job-id` performs a separate, explicit form proof using an existing entry from
`job_queue_real.csv`, the real Gemini answer planner and the same source/policy checks.
It fills permitted reversible fields, publishes an honest read-back (including unanswered
questions), checks the D-014 page and a refused approval POST, and never calls Submit.
This targeted form proof is separate from fit ranking; public queue entries lacking
required salary data or matching roles remain filtered from the goal's shortlist.

If another agent owns the default ports, leave it running:

```powershell
.\.venv\Scripts\python.exe scripts/run_real.py --auto-fixture --data-source drive_public --fixture-host 127.0.0.2 --cp-port 8792 --public-job-id public-job-002
```

Use a fresh `--state-dir` for each launcher run. It retains candidate snapshots, SQLite
checkpoints/ledger/control-plane data, screenshots, a redacted `timeline.jsonl`,
`report.json` and optional `public-proof.json` under ignored `runs/`. Ctrl-C cleans up
only owned processes and retains artifacts. The default run has no scripted approval.
Occupied ports fail before startup. `--headless` is opt-in; Chrome is headed by default.

For an already running guarded CP and Chrome, the factory is also compatible with
`python -m worker.main --factory src.operator.app.factory:build_services` and the worker's
existing endpoint/run/state arguments. Set `REAL_STATE_DIR` and `CP_DB_PATH` consistently;
this local composition stores snapshot submission authority beside the CP database.
Use this composition for the real-run UI guarantees: it carries fixture-only submission
authority and publishes the review snapshot through the configured control plane.
See [architecture](../../../docs/03-architecture/ARCHITECTURE.md) and the
[control-plane API](../../../docs/03-architecture/CONTROL_PLANE_API.md).

Offline checks: `python -m pytest src/operator/app/tests -q`.
