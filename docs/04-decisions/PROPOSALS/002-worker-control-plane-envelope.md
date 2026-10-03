# T-019 outbound worker integration envelope

Status: proposed integration surface for Claude/Kiro review; implements the
user-authorized missing initial interfaces. Locked D-006/D-008/D-015 are preserved.
No shared Command model changes and no raw action tokens enter checkpoints.

The control plane and local worker have separate SQLite files. A remote approval
cannot assume that writing the control plane's approval table populates the
worker's local ledger. The authenticated command response must carry expiry
metadata alongside the existing digest-bound Command:

```json
{"commands": [{"command": {"command_id":"example-id", "run_id":"example-run",
"job_id":"example-job", "action":"approve", "token_hash":"<SHA-256 digest>",
"snapshot_hash":"<SHA-256 digest>"}, "approval_expires_at":"<aware ISO-8601 datetime>"}]}
```

The CP verifies the user's signed POST capability and uses its own transactional
single-use token/command queue. WorkerTransport trusts only the explicitly
configured authenticated HTTPS origin (HTTP allowed solely on loopback). The
worker registers the immutable approval digest/expiry in its local ledger when
handling the command; the graph consumes it atomically with its local APPROVED
gate release. TLS verification and bearer authorization are mandatory remotely.
Expiry cannot exceed 30 minutes. A repeat digest cannot reset used_at or expiry.
An already-released identical gate can recover a checkpoint-write crash.

GET command polling is read-only. Acknowledgement is POST with command_id after
checkpoint persistence; heartbeat is POST with run_id/status only. Endpoint URLs
are supplied explicitly by CLI configuration; no route paths are invented.
Single-use action tokens are not included in the polling response or event log.

Services.review_url(run_id, job_id, snapshot_hash) can mint a read-only web-review
URL via the owning CP adapter. It enters only the outgoing Event.links, never
the ledger or LangGraph state/interrupt payload. Services.submission_urls reads
actual browser and form action targets; Services.restore_browser and target_id
support CDP restart without changing frozen browser contracts.

Left for CP owner: implement the envelope, endpoint authorization, signing,
review-link minting and the POST routes; phone/TLS G3 remains unproven.
