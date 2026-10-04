# T-035 production defect proposals

Author: codex-d; 2026-10-04. This branch changes tests and documentation only.
Claude and each production-path owner should review fixes separately under the
existing contract/lease rules. No locked decision is changed by this proposal.

## Confirmed adversarial findings

- **RT-01, medium:** `InjectionClassifier.classify` reports a standalone instruction
  to an automated agent to email the candidate's resume to an external address as
  clean. Detect targeted exfiltration directives and fail closed when a suspicious
  classifier path is unavailable. Ordinary employer contact instructions must
  remain distinguishable from instructions addressed to the operator.
- **RT-02, low:** `_check_origin` returns immediately for the correct Origin,
  ignoring `Sec-Fetch-Site: cross-site` or `same-site`. Reject contradictory Fetch
  Metadata, or explicitly document the accepted precedence. This is defense in
  depth: the test supplies a valid Origin, and is not proof that an ordinary
  cross-origin browser can forge that header.
- **RT-03, medium:** fill policy accepts a source-bound name action whose form
  label contains an instruction override and resume-exfiltration directive.
  Scan/quarantine extracted form text before it enters the planner. Separate
  forced malicious-value tests show that source binding still blocks the tested
  attacker-address/resume-as-email substitutions; no actual exfiltration occurred.
- **RT-04, high:** the real browser's navigation route guard misses an off-allowlist
  redirect hop during a local fixture input. Enforce navigation authority across
  redirect chains; inspecting only the initially routed request is insufficient.
  The stronger regression observes the destination fixture server, not just a
  missing callback. Destination remains our synthetic local fixture on port 8780.

## Confirmed recovery findings

- **RT-10, medium:** losing the worker commands GET before or after handling
  produces `action_boundary: HTTPError` and a persisted FAILED job. Restart reads
  that terminal failure rather than recovering after the CP returns. Both cases
  have zero inputs and zero submissions. `_control_command` performs the poll
  inside the guarded node, so a transient transport exception becomes a durable
  application failure. Retry safely or pause while preserving the recoverable
  checkpoint; do not ignore unavailable pause/cancel control before a mutation.

- **RT-09, medium:** a native non-sensitive checkbox question reaches E06, but
  the signed `/api/answer` rejects `value: true` with 422 `invalid_value` before
  queuing a command. The graph requires a boolean for checkbox/radio answers;
  the CP validates answers as strings only. Align the typed answer contract and
  render the appropriate input without weakening field/gate binding, legal/EEO
  handoff or single-use token checks. A string answer must not be silently coerced
  into consent. The chaos matrix uses a text question to reach both node boundaries;
  the separate strict red-team regression retains the boolean mismatch.

- **RT-05, medium:** kill before or after `repair` inside the handoff continuation;
  restart yields `FAILED` at `review_gate` with a PermissionError. No repeated
  successful inputs or fixture POST occurred. Preserve the resume command's
  association with its original gate across interrupted node continuations,
  including repair/re-review. Review snapshot/intent restoration is another
  possible contributing factor and must be traced before selecting a fix.
  Also reproduced at `click_next` before execution, `build_review` before/after
  return, and `review_gate` before entry. Those cases likewise end FAILED at
  `review_gate` after the synthetic handoff continuation, without a submit.
- **RT-07, medium:** kill after `click_next` has durably marked its no-next result
  successful, but before the node output is checkpointed. Restart enters repeated
  "Next-step outcome uncertain" handoffs; ten explicit synthetic handoff responses
  do not reach review. Preserve/observe the completed transition result so an
  already-successful action cannot trap recovery in a handoff loop. No repeated
  input or submit occurred.
- **RT-06, medium:** kill before `paused` in the pause continuation; restart feeds
  an unsupported command into `paused`, yields FAILED, and ends the CP run so the
  Resume form disappears. The after-return pause case recovered successfully.
  Resume-command scoping across continuation boundaries needs a regression.
- **RT-08, medium:** kill before/after `apply_edit`, restart and apply the requested
  edit once. The continuation then fails at `apply_edit` with PermissionError
  instead of waiting for approval of the new snapshot. Each original input stays
  at one execution; email has exactly the initial fill plus its one requested edit.
  Preserve edit-command completion while entering the fresh review gate; do not
  relax stale-snapshot checks or repeat the edit.

The matrix report is the authority for measured row outcomes and any additional
findings. Strict xfails are restricted to the corresponding assertion/error type;
unrelated safety violations must still fail. Fixes must retain D-004, D-008,
D-014 and D-015: no model-owned safety, no GET mutation, fixture-only submit, and
no repeated click after durable SUBMITTING.
