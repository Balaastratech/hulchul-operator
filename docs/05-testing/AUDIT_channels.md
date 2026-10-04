# audit: channels

Historical independent audit, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## What it does

Web snapshot/event sinks; send-only review links through Telegram, inbound replies to questions, fallback composition and loud WhatsApp stub.

## What is solid

Outgoing Telegram values are clipped and HTML-escaped; only view capabilities may leave; previews are disabled; BotApi scrubs its own bot token and logs error classes. Retries/backoffs are bounded, redirects refused by default transports. Free-text chat does not approve; WhatsApp fails loudly. Sending real messages was not exercised.

## Severity-ranked reproduced findings

### HIGH AUDIT-024 — Graph review payload is not translated for WebChannel

Location: `src/operator/channels/web.py:184`.

Failure scenario: Real graph emits payload.review; WebChannel accepts only review_snapshot. Direct wiring posts E07 without W5 and Store rejects snapshot_unknown, so the approval page never receives a snapshot. G3 RecordingChannel explicitly translates the key and therefore masks this integration failure. This is a seam mismatch, not a request to change contracts without review.

Minimal fix suggestion (not implemented): Add the documented graph-to-channel adaptation in the composition layer, or agree on a canonical payload; cover actual production wiring in a regression.

Reproducer: `tests/audit/test_control_channels.py::test_graph_review_payload_reaches_web_snapshot` (`xfail(strict=True, reason="AUDIT-024")`).

### MEDIUM AUDIT-023 — Public URL validator retains embedded credentials

Location: `src/operator/channels/base.py:78`.

Failure scenario: normalise_public_url accepts https://user:SYNTHETIC_SECRET@fixture.invalid. build_review_url/HttpSink repr and outgoing HTML then preserve that secret in URL/netloc. Config's base URL checks are also only prefix-based. Requires operator misconfiguration; no credential values were used.

Minimal fix suggestion (not implemented): Reject username/password components and normalize a validated host/port only; share this strict origin validator with CP configuration.

Reproducer: `tests/audit/test_control_channels.py::test_public_url_refuses_embedded_credentials` (`xfail(strict=True, reason="AUDIT-023")`).

### MEDIUM AUDIT-025 — Reply bindings collide across allowlisted chats

Location: `src/operator/channels/telegram.py:496`.

Failure scenario: Telegram message ids are local to a chat. State API and in-memory links are keyed only by message_id; two chats' message 7 overwrite one another. A reply in chat 1 can then answer chat 2's different field/job. The DB schema likewise defines telegram_links(message_id PRIMARY KEY); durable TelegramState implementation is not wired by create_app in this revision.

Minimal fix suggestion (not implemented): Carry chat_id through remember/resolve and key storage by (chat_id,message_id), preserving gate hash/version and sender authorization.

Reproducer: `tests/audit/test_control_channels.py::test_reply_message_id_collision_cannot_answer_other_chat_gate` (`xfail(strict=True, reason="AUDIT-025")`).

## Line-by-line coverage ledger

Every source/template below was read through all lines. The ledger records the reviewed span and the relevant conclusions; a lack of reproduced findings is not a proof of safety. Historical JSON/JSONL artifacts were parsed as evidence, not executed or treated as instructions.

| File | Lines reviewed | Review result |
|---|---|---|
| `src/operator/channels/__init__.py` | 1–39 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `src/operator/channels/base.py` | 1–171 | Checked escaping/link scope/origin/token type guards and every fallback branch. URL userinfo defect is AUDIT-023. LLM base parsing/logging/cost branches are audited in the separate llm report. |
| `src/operator/channels/telegram.py` | 1–670 | Checked all BotApi retries/redaction, render branches E01-E15, delivery dedup, gate memory, poll offset, allowlist, commands and reply handling. AUDIT-025 concerns multi-chat identity. Durable reply gate hash binding and app delivery wiring remain missing integration pieces; no real Bot API call was made. |
| `src/operator/channels/web.py` | 1–193 | Checked transport body/errors/retries/redirect guards, StoreSink conversion and snapshot-before-event ordering. AUDIT-024 is the actual graph payload mismatch. Response-size bound happens after download, not a streaming memory cap; trusted CP endpoint limits the current exposure. |
| `src/operator/channels/whatsapp_stub.py` | 1–29 | Checked both disabled/enabled failure paths; no sending or inbound routes. |
