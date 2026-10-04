# T-034 audit: llm

Auditor: codex-c (Codex session 3), T-034, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## What it does

REST Vertex/Gemini adapters, provider selection, retry/fallback, typed parsing, usage aggregation and semantic judging.

## What is solid

Gemini key uses x-goog-api-key header, Vertex uses bearer header, not URL query. Transport timeouts exist; structured schema rejects invalid output; successful parsed API responses record usage before schema validation. UsageTracker uses a lock and returns deep copies. Default forbidden preview-model guard is present.

## Severity-ranked reproduced findings

### HIGH AUDIT-012 — Validation logs echo full untrusted model output

Location: `src/operator/llm/base.py:226`.

Failure scenario: An invalid structured response containing SYNTHETIC_SECRET_SENTINEL is printed verbatim by logger.error and embedded in exception text. A response can echo profile/email or a credential accidentally supplied by a caller. Provider errors also include response bodies; no real secrets were loaded to demonstrate this.

Minimal fix suggestion (not implemented): Log schema/error class and sanitized field paths only; omit raw output and Pydantic input values from logs and exceptions. Store tightly controlled redacted diagnostics separately if necessary.

Reproducer: `tests/audit/test_injection_llm.py::test_validation_log_does_not_echo_sensitive_output` (`xfail(strict=True, reason="AUDIT-012")`).

### HIGH AUDIT-014 — Negative error text is treated as submission proof

Location: `src/operator/llm/judge.py:85`.

Failure scenario: 'ERROR: no application received. Try again.' returns confirmed=True at 0.99 confidence. Confirmation substring presence cannot establish successful submission; page text may also quote/hijack expected phrases. This adapter's failure is proven; a successful real employer submission was never attempted.

Minimal fix suggestion (not implemented): Require contextual deterministic confirmation plus absence of active/error state and traceable submission evidence; models may propose evidence but must not be the sole success authority.

Reproducer: `tests/audit/test_injection_llm.py::test_negative_submission_text_is_not_confirmation` (`xfail(strict=True, reason="AUDIT-014")`).

### MEDIUM AUDIT-010 — Thinking tokens are recorded in total but excluded from cost

Location: `src/operator/llm/base.py:133`.

Failure scenario: Fake provider returns 10 prompt, 20 candidate, 1000 thought and 1030 total tokens. total_tokens includes thoughts while calculate_cost receives only 20 outputs; reported cost is less than the same tariff applied to 1020 billed output tokens. S13/S14 'accurate' cost claims are too strong.

Minimal fix suggestion (not implemented): Separate visible output/thought/cached token counters and cost all billed components by provider/model; preserve usage on billed empty/blocked responses where metadata is available.

Reproducer: `tests/audit/test_injection_llm.py::test_thinking_tokens_are_costed` (`xfail(strict=True, reason="AUDIT-010")`).

### MEDIUM AUDIT-011 — Default Flash tariff substantially underestimates paid cost

Location: `src/operator/llm/cost.py:14`.

Failure scenario: Code uses $0.075 input/$0.30 output per million; current published Gemini Developer API standard text rates are $0.30/$2.50. For one million of each code returns $0.375 rather than $2.80. These are estimates, not invoices; Vertex rate verification and historical billing remain separate.

Minimal fix suggestion (not implemented): Version provider-specific tariffs with date and token/context categories; unknown models should report unknown instead of silently using Flash pricing. Recompute S14 from raw usage, including thoughts, and avoid exact INR claims with a fixed exchange-rate default.

Reproducer: `tests/audit/test_injection_llm.py::test_flash_tariff_matches_published_standard_rate` (`xfail(strict=True, reason="AUDIT-011")`).

### MEDIUM AUDIT-013 — Empty read-back is accepted as a semantic match

Location: `src/operator/llm/judge.py:47`.

Failure scenario: The empty actual string is contained in every nonempty expected string; judge returns match=True with 0.95 confidence for an entirely blank field before asking any model.

Minimal fix suggestion (not implemented): Require both values to be nonempty before containment and restrict containment by field semantics.

Reproducer: `tests/audit/test_injection_llm.py::test_semantic_judge_rejects_empty_readback` (`xfail(strict=True, reason="AUDIT-013")`).

### MEDIUM AUDIT-028 — Provider silently returns only the first response part

Location: `src/operator/llm/gemini_api.py:95`.

Failure scenario: A response containing thought=true text followed by two answer text parts returns internal thought instead of 'final answer'. Both Gemini and Vertex adapters use parts[0]. Valid multi-part output is truncated or non-answer content is treated as the structured answer.

Minimal fix suggestion (not implemented): Concatenate all non-thought text parts, handle non-text/blocked finish states explicitly, and record usage before parse failures.

Reproducer: `tests/audit/test_injection_llm.py::test_multipart_provider_answer_is_complete` (`xfail(strict=True, reason="AUDIT-028")`).

Price evidence consulted 2026-10-03: [official Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) and [official thinking-token billing explanation](https://ai.google.dev/gemini-api/docs/thinking/). AUDIT-011 freezes the standard text price used for this audit; update this expectation deliberately if official tariffs change. This does not establish the historical Vertex invoice or current Vertex tariff.

## Line-by-line coverage ledger

Every source/template below was read through all lines. The ledger records the reviewed span and the relevant conclusions; a lack of reproduced findings is not a proof of safety. Historical JSON/JSONL artifacts were parsed as evidence, not executed or treated as instructions.

| File | Lines reviewed | Review result |
|---|---|---|
| `src/operator/llm/__init__.py` | 1–47 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `src/operator/llm/base.py` | 1–252 | Checked escaping/link scope/origin/token type guards and every fallback branch. URL userinfo defect is AUDIT-023. LLM base parsing/logging/cost branches are audited in the separate llm report. |
| `src/operator/llm/cost.py` | 1–92 | Checked model/prefix/default selection, USD/INR conversion, rounding and all lock-protected cumulative fields. AUDIT-010/011; unknown model fallback and fixed FX cannot be described as billing precision. |
| `src/operator/llm/factory.py` | 1–100 | Checked source/provider selection and environment loading. LLM config loads dotenv only on explicit factory invocation; tests use fake adapters. Data source unknown names fall back to local: validate choices at composition boundary. No real .env read by audit probes. |
| `src/operator/llm/gemini_api.py` | 1–99 | Checked request headers/body, timeout/error/response parse and usage. API key stays in header. AUDIT-028 covers part extraction; remote error bodies need the same redaction policy as AUDIT-012. |
| `src/operator/llm/judge.py` | 1–119 | Checked all deterministic/LLM/error branches for value and submission equivalence. AUDIT-013/014. Untrusted labels/page content are interpolated into prompts without a dedicated system/data framing; models are not a deterministic authority. |
| `src/operator/llm/protocol.py` | 1–86 | Checked exported port signatures and usage/data shapes; async wrappers currently call synchronous work, so blocking behavior needs orchestration/threading. No remote or schema mutation performed. |
| `src/operator/llm/vertex.py` | 1–136 | Checked ADC init/refresh, endpoint/body/bearer, errors and response extraction. No auth initiated by audit; multipart defect mirrors AUDIT-028. Provider-specific pricing is not established by the Gemini API price citation. |
