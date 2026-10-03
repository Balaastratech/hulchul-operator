# T-001 initial contract surface (Codex, 2026-10-03)

Status: initial implementation authorized by user in this chat; Claude review
and merge/freeze remain pending. No existing frozen contracts are changed. Contract lease:
`hulchul-operator-fq8`, held by codex. Implementation stays under contracts.

AGENT_GRAPH defines FieldSpec's fields, PageState and JobStatus vocabulary, but
does not define Port signatures, FillReport's layout or complete candidate,
event and command schemas. These missing interfaces must be settled before
consumers pin contracts-v0.1a/b. No locked decision changes are proposed.

Proposed v0.1a additions for review:
- FieldSpec: id/key/label/group/type/options/required/current_value. Values are
  JSON-compatible scalars or arrays; key remains extractor-owned label|type|group|n.
- FillAction: field_key, action (fill/select/check/upload_resume/ask_user/skip),
  value, question, generated and source. No submit or CAPTCHA action exists.
- FillReport: per-field intended/actual/matched/escalated/reason plus evidence;
  verified means every reported field is matched; escalated fields remain
  visibly unresolved and are never counted as verified.
- BrowserPort (async): attach(endpoint, target_id), navigate(url), classify_page(),
  extract_fields(), execute(action), verify(actions), click_next(), capture_evidence(),
  read_review(), submit(), verify_submission(). Submit is called only by the
  deterministic graph after ledger claim; the port itself grants no authority.
- LLMPort (async): structured(prompt, response_model) -> response_model instance.
- DataSourcePort (async): load(run_id) -> typed DataSnapshot.
- ChannelPort (async): emit(event) -> None; review URLs are event data.
- LedgerPort (sync, atomic): claim_action(run_id, job_id, action, field_key),
  mark_success(...), is_done(...), record_approval(...), consume_approval(...).
  Approval bindings include run/job/snapshot, token hash and expiry. Consumption
  releases the persisted gate in the same transaction; no raw token is persisted.

The initial documented primitives can be implemented independently while the
missing types and signatures are confirmed. Tests live inside owned module paths.
Imports use `src.operator.contracts` from repo root: top-level `operator` collides
with Python's standard-library module. Packaging is outside this task's ownership.

Open safety inconsistency: TP-04 expects one click after a crash between approval
and click, while D-015 requires zero re-clicks once SUBMITTING is written, even
when a crash occurs before the click. D-015 takes precedence; the uncertain
SUBMITTING case must be reported unverified. No speculative retry is allowed.

Authorized v0.1b implementation details: complete models are in `state.py` and
exported schemas. Goal uses a dry-run default and explicit permits_filling flag;
Profile's absent facts stay None; Rules defaults EEO to ask_user. AnswerLibrary
wraps source-attributed rows. Review hashing canonically sorts stable field and
upload keys and binds values, upload content hashes, generated-text flags and
unanswered keys; screenshot filenames are evidence and excluded from form
identity. State adds paused overlays, repair_attempts (0..2), review_snapshot and
browser_target_id for resumability. RunStatus adds QUEUED/RUNNING to the documented
four terminal results. Command contains capability digests only; signature
verification and transport authorization belong to the control plane, ledger
consumption is still mandatory. No checkpoint stores raw tokens or handles.
