# T-030: preserve pending human gates on worker restart

Observed in the real Chrome + real HTTP G3 test on main ac05b3e: the graph reaches
READY_FOR_REVIEW, then a new worker's `start_or_resume()` calls `graph.invoke(None)`.
LangGraph reuses the previous `handoff_done` resume while entering `review_gate`;
the job becomes FAILED (`review_gate: PermissionError`) before the queued pause is
handled. CP then expires the pause because E14 has ended the run.

Proposed owned-worker fix (B1; no contract or control-plane changes): after CDP
reattach/restore, if the recursively nested checkpoint has a pending interrupt,
return its state without invoking the graph. `Worker.tick()` supplies the fresh
CP command through `Command(resume=...)`. If no interrupt is pending (including
a worker killed during submission), continue with `invoke(None)` for recovery.
Validation: the three real G3 scenarios, existing worker tests and offline suite.

The headed demo retains a second review tab. Terminal re-entry previously tried
to attach without a target after `active_job_id` was cleared and failed with
"multiple browser targets require a saved target ID". Completed checkpoints now
return their persisted result without browser attachment or another graph invoke.

Two integration limitations are explicit:

- Graph E07/E08 uses `payload.review`, while WebChannel expects
  `payload.review_snapshot`. The demo's RecordingChannel adapts this explicitly
  before calling the unmodified WebChannel; production wiring should do the same
  or align the producer in a separately reviewed change.
- A kill after `begin_submission()` but before the click must yield zero submits
  and SUBMITTED_UNVERIFIED on restart, per the locked at-most-once policy. A kill
  before that durable claim can recover to exactly one verified submit. Requiring
  exactly one in both windows would violate D-004 and D-015 safety semantics.

No changes to `control_plane/**` are proposed. User authorized the owned-worker
fix in this chat on 2026-10-03; this document records the failing evidence and scope.
