# T-035 fixture chaos and red team

From the repository root, with the documented Python dependencies and system Chrome:

```powershell
$env:RUN_CHAOS='1'; python -m pytest tests/chaos tests/redteam -q -ra
```

Run serially. This command exclusively owns **127.0.0.1:8780** until pytest exits.
It fails if another fixture server owns that address. CP and CDP use generated ports;
all signing keys and worker credentials are generated. No `.env`, external ATS,
Telegram, real model, candidate data or real CAPTCHA is used. The `chaos` marker
and `RUN_CHAOS` flag keep browser fault injection out of ordinary offline runs.

The executable node inventory and CP method/route inventory must match production.
Each node is killed immediately before execution and after return before its state
is checkpointed. Interrupting gates reach the after boundary when resumed. The
parent `application` container is observed at child entry and the next parent-node
entry after its exit. Chrome stays alive while the Python worker is killed. The
next process reopens the same checkpoint, ledger, command database and CDP target.

Request failures occur before route handling or after route/store completion;
the test-only ASGI shim replaces the unavailable response with 503. A retry uses
the same action token. Read-only pages, HEAD and SSE, all human actions and all
worker routes are covered. Rejection, skip and cancellation correctly end without
a submission; they must never be labelled VERIFIED.

Every route fault also kills/restarts the worker. Event delivery uses the production
HTTP sink's three attempts (the demo normally configures one). A synthetic native
question exercises `ask_user` and `/api/answer`. Evidence uploads an actual fixture
review screenshot; skip uses a synthetic documented E02 `chosen` shortlist envelope
through the real worker API, since the G3 demo emits `selected` instead. This covers
the CP skip gate, not a graph shortlist-confirmation flow.

Every successful operator input is journalled by field key. Recovery may not
repeat one. Only the explicit email-edit scenario permits a second email input.
Fixture counters independently check at most one submission and no false VERIFIED.
Approval replay is rejected before and after worker recovery. The repair variant
fails one initial input before touching the DOM, so repair gets one first input,
not permission to refill an already successful field.

For a capability-free machine-readable matrix, optionally set `CHAOS_RESULTS` to
an absolute path outside the repository. The export preserves status, submission
count and field-input counts before pytest's temporary-directory cleanup. Each test also leaves its synthetic
state, node journal and outcome under pytest's temporary directory. Do not publish
CP databases or generated review links: the report contains only case IDs,
counts, statuses and finding references.

Known defects use strict xfails. A fixed defect must produce XPASS and fail the
command until its finding and expectation are reviewed. Production code is never
patched by this suite.
