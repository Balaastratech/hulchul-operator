# SQLite ledger integration

Import `SQLiteLedger` from `src.operator.ledger`. Use a local file path; one
connection per operation, WAL, BEGIN IMMEDIATE for writes. Clock returns an aware
UTC datetime. No browser objects or capability tokens belong here.

Control-plane approval ordering:
1. Graph reserves `register_application(run_id, job_id, company, canonical_url)`.
2. Graph publishes `record_review(run_id, job_id, snapshot_hash)`.
3. Control plane validates the signed token (including action/run/job/hash/expiry)
   then calls `record_approval(run_id, job_id, token_hash, snapshot_hash, expires_at)`.
4. `consume_approval(...)` returns true once and sets durable APPROVED in the same
   transaction. It must not be replaced by a separate token-consume/state-update.
5. Worker checks live read-back against the exact snapshot. Only
   `begin_submission(run_id, job_id, snapshot_hash)` may persist SUBMITTING; click
   only when it returns true. Capability expiry is rechecked at this point.
6. On SUBMITTING after any crash, verification only. `finish_submission(...,
   verified=False)` makes uncertainty visible. Never reclaim a submit intent.

Do not call `record_review` again for the same published gate during re-entry:
it deliberately invalidates earlier approvals. A changed or edited form needs
another review and another capability. Already-used capability digests cannot be
overwritten or reset. Signature verification is the control plane's responsibility;
the ledger does not assert that an arbitrary supplied digest is authenticated.

Action claims use a composite tuple, avoiding concatenation collisions. A claimed
but unfinished reversible action is uncertain: inspect its live read-back, mark
success if it already matches, otherwise hand off. A completed action is skipped.
Repair/edit actions use a revision-specific field key so the original fill is
never replayed. Approval tokens and URLs containing capabilities are not events.

Run checks: `python -m pytest src/operator/ledger/tests -q`.
