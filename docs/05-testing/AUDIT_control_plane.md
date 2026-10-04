# audit: control_plane

Historical independent audit, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## What it does

FastAPI worker API, HMAC capabilities, SQLite command/review/gate persistence, HTML pages and authorized read-only SSE; historical S5/S6 spike programs/results included as separate evidence.

## What is solid

POST-only action routes; pure token minting; constant-time MAC/bearer comparison; bounded streamed request bodies; atomic BEGIN IMMEDIATE consume+command; recomputed snapshot hashes; stale/replay guards; Jinja autoescape, CSP/no-store/no-referrer; authorized SSE reads with per-run caps and cleanup. CLI disables token-bearing access logs. Spike programs are explicitly synthetic and separate from deployed app.

## Severity-ranked reproduced findings

### HIGH AUDIT-020 — Expired approval blocks every fresh approval for the same snapshot

Location: `control_plane/store.py:639`.

Failure scenario: An approval expires before the worker picks it up. Even after ack, its approval row stays active/consumed_by_worker and review_state remains approved. consume_act's one-live-row check ignores expiry and rejects a newly minted token as already_approved. The user cannot perform D-030's re-review of unchanged content.

Minimal fix suggestion (not implemented): Define explicit expiration/invalidation and reset review state before a new approval, preserving submit-once history separately. Worker outcome/expiry handling must distinguish unexecuted expired commands from completed submits.

Reproducer: `tests/audit/test_control_channels.py::test_expired_approval_can_be_reviewed_again` (`xfail(strict=True, reason="AUDIT-020")`).

### HIGH AUDIT-026 — Uploaded screenshot links have no serving route

Location: `control_plane/routes/human.py:370`.

Failure scenario: W6 successfully stores a screenshot; job_page builds /evidence/{id}?t=<evd>. No router implements that path, so a valid scoped token gets 404 and the human cannot inspect visual evidence. Test calls W6 storage then performs a real TestClient GET.

Minimal fix suggestion (not implemented): Implement a read-only evidence endpoint validating evd type, run/job/id, TTL and contained file path, with safe MIME/no-store; include it in GET-never-mutates enumeration.

Reproducer: `tests/audit/test_control_channels.py::test_uploaded_evidence_link_is_served` (`xfail(strict=True, reason="AUDIT-026")`).

### MEDIUM AUDIT-021 — No-op edit leaves edit_pending forever

Location: `control_plane/store.py:408`.

Failure scenario: After an edit command is acknowledged, the worker posts an identical snapshot hash (same value or normalized equivalent). put_snapshot's duplicate branch updates only body_json and leaves edit_pending. Page refuses approve even though fresh read-back is complete.

Minimal fix suggestion (not implemented): Tie edit completion to acknowledged command and fresh read-back, including unchanged hash; clear edit_pending only when those conditions hold.

Reproducer: `tests/audit/test_control_channels.py::test_noop_edit_returns_to_reviewable_state` (`xfail(strict=True, reason="AUDIT-021")`).

### MEDIUM AUDIT-022 — Dev unauthenticated mode accepts a non-loopback look-alike

Location: `control_plane/config.py:119`.

Failure scenario: CP_DEV_ALLOW_UNAUTH_WORKER=1 with CP_BASE_URL=http://localhost.attacker.invalid passes startswith and permits missing bearer. CLI independently rejects missing worker token, but app factory/direct deployment does not. This is a dev-mode boundary defect, not a production default bypass.

Minimal fix suggestion (not implemented): Parse the URL and compare hostname against exact loopback names/IPs, reject userinfo/path/query, and enforce safe binding for this explicit dev mode.

Reproducer: `tests/audit/test_control_channels.py::test_unauth_dev_mode_requires_actual_loopback_host` (`xfail(strict=True, reason="AUDIT-022")`).

## Line-by-line coverage ledger

Every source/template below was read through all lines. The ledger records the reviewed span and the relevant conclusions; a lack of reproduced findings is not a proof of safety. Historical JSON/JSONL artifacts were parsed as evidence, not executed or treated as instructions.

| File | Lines reviewed | Review result |
|---|---|---|
| `control_plane/__init__.py` | 1–4 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `control_plane/__main__.py` | 1–77 | Checked startup config refusal, CLI loopback default, explicit host warning and disabled access_log. No server was exposed externally. |
| `control_plane/app.py` | 1–125 | Checked middleware, exception responses, lifespan/store ownership and all included routers. Missing evidence endpoint is AUDIT-026. create_app does not launch Telegram outbound/inbound delivery tasks; W4 returns telegram=queued without a dispatcher in this app. This remains an integration completion gap, not proof of message delivery. |
| `control_plane/config.py` | 1–153 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/models.py` | 1–42 | Checked model validation, action enum, ask_user question requirement and empty FillReport semantics. CP models re-export frozen contracts. Models alone grant no action authority. |
| `control_plane/README.md` | 1–90 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/routes/__init__.py` | 1–1 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `control_plane/routes/human.py` | 1–558 | Checked every HTML context, escaped value, URL mint and POST media/body/origin/binding branch. View/evd URL capabilities and hidden POST act tokens are intentional D-008 design, not leaked server keys. No EEO/legal input is automatically typed by this module; edits need worker policy rejection. Evidence route gap is AUDIT-026. |
| `control_plane/routes/sse.py` | 1–521 | Checked all summary filtering, scope SQL, framing, replay/burst, expiry, subscription and response-cleanup branches. Only in-memory subscriber state changes on GET; persistent DB stays read-only. Pages explicitly do not yet implement fetch-stream UI. |
| `control_plane/routes/worker.py` | 1–349 | Checked every bearer/id/body/event/snapshot/evidence branch, filesystem write/rollback and response. W6 stores bounded magic-byte image data under generated names. No credential is placed in route URLs. GET commands is read-only. |
| `control_plane/spikes/s5_s6/app.py` | 1–281 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/requests.jsonl` | 1–56 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/results_phone.json` | 1–34 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/results_s5.json` | 1–96 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/results_s6.json` | 1–86 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/run_spike.py` | 1–287 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/spikes/s5_s6/tokens.py` | 1–76 | Historical synthetic spike only: checked full source flow or parsed every JSON/JSONL record; did not execute credential loading, delete DB, write temp tokens or send messages. Recorded results do not establish production route behavior. |
| `control_plane/store.py` | 1–762 | Checked schema and every read/write transaction through the end of the file: snapshot/event gates, supersession, act consume, run controls, retention. AUDIT-020/021 show state-lifecycle gaps; transaction/replay behavior remains solid. Telegram links schema exists but no durable TelegramState methods are implemented here. |
| `control_plane/templates/base.html` | 1–35 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/templates/error.html` | 1–7 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/templates/job.html` | 1–144 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/templates/result.html` | 1–7 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/templates/run.html` | 1–63 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `control_plane/tokens.py` | 1–333 | Checked each mint/verify/type/time/scope/encoding branch and key separation/rotation. Production token mint and verify are pure. Historical spike tokens are a different synthetic format and were not reused for production probes. |
