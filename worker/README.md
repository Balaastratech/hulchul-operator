# Local outbound worker

Run under Python 3.13. Install `worker/requirements.txt` in a local venv and use
`python -m worker.main --help`. A trusted `--factory module:function` returns
Services configured with the browser/data/LLM/channel adapters and exact trusted
navigation/fixture origins. No model output can select the factory or hosts.

Provide run-id, state-dir, command/acknowledgement/heartbeat endpoint URLs, and
goal for a new run. Restart with the same run-id/state-dir; saved state wins and
Chrome is reattached at the saved CDP target. Chrome is launched separately by
the browser owner; the worker never substitutes a fresh browser if it is missing.
The bridge preserves synchronous Playwright thread affinity via one executor.

Only `ENV_FILE` (default `C:\Balaastra\hulchul-operator\.env`) is loaded, without
copying or printing it. Optional `WORKER_AUTHORIZATION` supplies the exact HTTP
Authorization header; remote endpoints require it and HTTPS. No credentials are
accepted in URLs and all redirects are rejected before forwarding authorization.

The CP polling envelope is described in
`docs/04-decisions/PROPOSALS/002-worker-control-plane-envelope.md`. It contains a
digest-bound Command plus aware approval_expires_at for approvals. Local approval
registration occurs when handling the authenticated command; graph consumption
and APPROVED gate release are one ledger transaction. Raw signed action tokens
never enter polling payloads, command tables, checkpoints or logs.

Pause/cancel is polled at checkpoints between individual field actions. Pause
never cuts an atomic claimed operation in half. A persisted pause is acknowledged
without resuming it; only an explicit resume command releases the interrupt.
Command IDs are stored before execution and marked DONE only after checkpoint
persistence, before POST acknowledgement. Failed acknowledgements can replay
without reapplying browser actions. Heartbeat POST includes run-id/status only.

Services integration callbacks:
- submission_urls: live page/form targets, mandatory for submitting.
- restore_browser and target_id: restore serializable field/action metadata and
  reattach the exact CDP target.
- review_url: read-only review link delivered in Event.links, excluded from
  checkpoint/ledger storage; add the CP host to the allowlist.
- injection_scan: mandatory for nonempty posting descriptions, implemented by
  the browser lane's injection classifier.

Checks: `python -m pytest worker/tests src/operator/graph/tests
src/operator/ledger/tests src/operator/contracts/tests -q`. Real Chrome probes
are opt-in with HULCHUL_BROWSER_SOURCE pointing to the peer source checkout (or
merged repo). They use only a local synthetic fixture and fake model/data/channel
Ports, never employer submissions or real Telegram/Drive calls. Phone/G3 and the
owning control-plane adapter integration remain separate required checks.
