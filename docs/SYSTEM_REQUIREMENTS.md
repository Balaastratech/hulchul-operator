# System requirements

| Requirement | Supported / needed |
|---|---|
| OS | Windows 10/11 is the tested target family; recorded executions used Windows 11. macOS/Linux are expected but untested. |
| Python | 3.13 or newer, with `venv` and pip. Dependencies are pinned in `pyproject.toml`. |
| Browser | Installed Google Chrome stable. Playwright uses system Chrome (`channel="chrome"`); launchers start that executable and attach over CDP. Bundled Playwright browsers are **not needed**. |
| Memory | Recommended 8 GB RAM, with roughly 2 GB available for Chrome and worker. Planning guidance, not a measured minimum. |
| Disk | Allow at least 2 GB free for environment, browser profiles and evidence; retained runs can grow. Planning guidance. |
| Network | Setup needs package downloads. The scripted demo needs only loopback after installation. Model-backed runs need Google HTTPS; Drive, Telegram and tunnels add their own HTTPS endpoints. |
| Git | Needed to clone; no Node runtime needed for the operator. |

`scripts/setup.ps1` / `scripts/setup.sh` create `.venv`, install `pip install .`, copy `.env.example` only if `.env` is missing and run the offline doctor. They never overwrite a configured `.env`. Commands must run in the checkout because pip installs dependencies only.

## Accounts and configuration

No account or key is needed for the scripted fixture demo. Missing optional capabilities print WARN; required local failures print FAIL and return a nonzero exit code.

For a model-backed run choose **one**:

* Gemini Developer API: `LLM_PROVIDER=gemini_api`, `GEMINI_API_KEY` from Google AI Studio.
* Vertex: `LLM_PROVIDER=vertex`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`; billing and Vertex AI access enabled, and `gcloud auth application-default login` for ADC. `LLM_MODEL` defaults to `gemini-2.5-flash`.

`DATA_SOURCE=local_folder` defaults to `sample_data/`; `LOCAL_DATA_DIR` overrides the directory. Drive is optional (`drive_public`, `DRIVE_FOLDER_ID`; public read-only exports). Optional phone approval requires `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` and a reachable control plane; `--tunnel` uses optional cloudflared. Docker is optional for control-plane deployment. No paid cloud job is needed for the demo.

For a standalone control plane set random `CP_SIGNING_KEY` and `CP_WORKER_TOKEN`, at least 32 bytes each; generate them locally with Python's `secrets.token_urlsafe(48)` and store only in your ignored `.env`. The demo and `run_real.py` generate isolated per-run credentials themselves. Always set `ENV_FILE` explicitly to your checkout's `.env` for real runs: existing launchers otherwise default to the original developer's absolute Windows path. Never print the file or credentials to troubleshoot.

Doctor reads only the selected file and environment, reports variable **names/presence**, and suppresses response bodies and exception details. `python scripts/doctor.py --real` checks selected provider credentials and authenticated model metadata reachability using GET, without generating paid tokens. This does not prove generation quota or output quality. `--offline` skips network checks. In the new no-keys setup, credentials and optional tools are warnings.

## Ports and cost

| Port | Use |
|---|---|
| `127.0.0.1:8780` | Local fixture server; fixed in the G3 demo. Real launcher also supports `--fixture-host 127.0.0.2`. |
| `127.0.0.1:8790` | Demo and real launcher's default control plane; override with `--cp-port`. |
| OS-assigned free loopback port | Chrome CDP, allocated by launchers; do not expose it publicly. |
| `8781` | Historical S5/S6 standalone spike only; not the current demo default. |

An optional standalone control plane may use its own port; align `CP_BASE_URL` and tunnel target with it. Doctor accepts `--cp-port` and `--fixture-host` to match your launcher.

Typical measured model cost in the retained phone fixture run was approximately **INR 3.65 for seven Gemini calls** (proof and context, [JSON](05-testing/evidence/phone-proof-2026-10-04.json)). This is a run estimate, not a fixed price or invoice; model, tokens, repairs and quotas change the cost. The scripted demo makes zero model calls and has no model charge.

## Troubleshooting

| Symptom seen | Fix |
|---|---|
| Port in use / unrelated fixture counters | Stop only your own previous launcher. Run doctor, identify the owner with `Get-NetTCPConnection -LocalPort 8780,8790` (Windows) or `lsof -i :8780 -i :8790`. Never kill another worker. For the real launcher use `--fixture-host 127.0.0.2 --cp-port 8792`; G3 requires 127.0.0.1:8780 free. |
| Chrome minimized / background ATS tab; screenshot or review stalls | Restore Chrome and activate the application tab. The screenshot adapter now brings the target tab forward; keep headed Chrome visible during human handoffs. Measured cause (historical process reference removed). |
| Windows PermissionError / file lock on state or cleanup | Stop your own run and close only its Chrome instance; wait for handles to release. State replacement retries transient locks. Use a fresh run directory; never erase evidence or reuse a live browser profile. |
| Approval gives `403 bad_origin` | Open the issued review link in a normal browser and use its buttons. Do not POST from a different host, stale tunnel, or `file://` page. Match `CP_BASE_URL` to the current tunnel origin; use `--tunnel` to configure it. Keep Origin checks enabled. |
| Chrome not found | Install Chrome stable or set `CHROME_PATH` to its full executable path. On macOS/Linux this override is required unless `chrome` is on PATH; do not run `playwright install`. |
| Missing model keys / Vertex auth denied | For Gemini set the key and `LLM_PROVIDER=gemini_api`. For Vertex set project/location/provider, log in with ADC and check Vertex/billing permissions. Use `doctor.py --real`; it never prints key values. |
| Real run ignores the clone's `.env` | Set `ENV_FILE` to the absolute path of this checkout's `.env` in the same shell as the launcher. |
| PowerShell blocks setup | Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1`; this applies to that invocation. |
| Demo slow or cleanup fails | Check free ports and available RAM; close your own previous run. Historical startup and Windows cleanup timeouts are recorded in the spike report. A timeout is a failure, not a successful proof. |

See [README](../README.md) for the exact no-keys command and model-backed flow.
