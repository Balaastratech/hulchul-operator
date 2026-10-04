# audit: data

Historical independent audit, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## What it does

Public Drive folder/file download, local candidate/rules/answer/job parsers, source factory and per-run file hashes/copies.

## What is solid

Candidate schemas forbid extra fields; YAML uses safe_load; missing required local files raise. Composite hashes are deterministically sorted and resume SHA-256 is exposed. No Drive mutation or credential-based write path exists. These protections do not guarantee a consistent downloaded bundle.

## Severity-ranked reproduced findings

### HIGH AUDIT-015 — Document profile download is incompatible with the loader

Location: `src/operator/data/drive.py:120`.

Failure scenario: Documented Drive object 'profile' downloads as profile.md, but load_sync requires profile.json and LocalFolderDataSource parses only JSON. With valid synthetic downloads and fallback disabled the adapter raises FileNotFoundError; with fallback enabled it can silently use unrelated sample data. A profile.json remote name takes the binary/PDF branch unless its name also contains 'doc'. The bundled sample_data contains profile.md, so this loader's fallback is also incompatible at the audited revision.

Minimal fix suggestion (not implemented): Normalize supported exported formats into the same Profile schema; recognize the documented names/formats and disclose source/fallback identity. Validate a complete bundle before choosing it.

Reproducer: `tests/audit/test_data.py::test_drive_profile_doc_is_used` (`xfail(strict=True, reason="AUDIT-015")`).

### HIGH AUDIT-016 — UTF-8 BOM silently drops authoritative YAML rules

Location: `src/operator/data/local.py:43`.

Failure scenario: A BOM-prefixed rules.md with remote_only=true and blocked_companies=[BadCo] becomes prose; typed constraints revert to defaults. Manager explicitly called out Drive export BOM in round 7/earlier notes; code reads utf-8 without stripping it.

Minimal fix suggestion (not implemented): Decode utf-8-sig or strip one leading BOM before front-matter detection; reject malformed authoritative YAML rather than quietly falling back to permissive defaults.

Reproducer: `tests/audit/test_data.py::test_bom_does_not_discard_hard_rules` (`xfail(strict=True, reason="AUDIT-016")`).

### HIGH AUDIT-017 — Remote filenames can escape the cache directory

Location: `src/operator/data/drive.py:95`.

Failure scenario: A Drive/file-map name '../escaped.pdf' is joined directly to target_dir. Fake download writes outside cache (still inside the test worktree). Other ../ or absolute names can overwrite accessible files. Discovered public folder filenames cross a trust boundary.

Minimal fix suggestion (not implemented): Map only the documented logical filenames to fixed local basenames; resolve destinations and verify containment, rejecting separators and absolute/drive paths.

Reproducer: `tests/audit/test_data.py::test_remote_filename_cannot_escape_cache` (`xfail(strict=True, reason="AUDIT-017")`).

### HIGH AUDIT-018 — Partial refresh mixes fresh and stale authoritative files

Location: `src/operator/data/drive.py:111`.

Failure scenario: Preseed a complete cache, successfully update rules, fail profile fetch. _sync_drive_files reports success after one download, and load_sync uses cached profile/resume with new rules as a fresh snapshot. D-009's mutable source-of-truth and data consistency are not fulfilled by this mixed version.

Minimal fix suggestion (not implemented): Download a complete required bundle to a new staging directory; validate/hash there and publish atomically. On any required failure block or use an explicitly disclosed complete fallback.

Reproducer: `tests/audit/test_data.py::test_partial_refresh_never_mixes_stale_rules` (`xfail(strict=True, reason="AUDIT-018")`).

### MEDIUM AUDIT-019 — Unvalidated run_id escapes snapshot root

Location: `src/operator/data/local.py:146`.

Failure scenario: load_sync('../escaped-run') writes source copies into escaped-run/data outside runs. EvidenceManager uses the same unchecked run-id join. CP validates its identifiers, but this public data adapter accepts arbitrary caller IDs; reproduction never leaves the worktree.

Minimal fix suggestion (not implemented): Validate run identifiers at the adapter boundary and ensure resolved snapshot/evidence directories remain under the designated run root.

Reproducer: `tests/audit/test_data.py::test_run_id_cannot_escape_runs` (`xfail(strict=True, reason="AUDIT-019")`).

## Line-by-line coverage ledger

Every source/template below was read through all lines. The ledger records the reviewed span and the relevant conclusions; a lack of reproduced findings is not a proof of safety. Historical JSON/JSONL artifacts were parsed as evidence, not executed or treated as instructions.

| File | Lines reviewed | Review result |
|---|---|---|
| `src/operator/data/__init__.py` | 1–27 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `src/operator/data/drive.py` | 1–133 | Checked every filename/export URL, status/error branch, discovery, success threshold, cache/fallback and async load. AUDIT-015/017/018. Full URLs and exceptions are logged by download_url: caller must never supply secret-bearing arbitrary download URLs. Downloads follow redirects and lack size caps. |
| `src/operator/data/factory.py` | 1–28 | Checked source/provider selection and environment loading. LLM config loads dotenv only on explicit factory invocation; tests use fake adapters. Data source unknown names fall back to local: validate choices at composition boundary. No real .env read by audit probes. |
| `src/operator/data/local.py` | 1–165 | Checked all CSV/front-matter/JSON parsing, required files, pre-copy hashes and snapshot copy. BOM and root confinement defects AUDIT-016/019. Hashing/parsing/copying live files separately also has a TOCTOU limitation under concurrent mutation; use staged bytes for one consistent snapshot. |
| `src/operator/data/protocol.py` | 1–19 | Checked exported port signatures and usage/data shapes; async wrappers currently call synchronous work, so blocking behavior needs orchestration/threading. No remote or schema mutation performed. |
| `src/operator/data/schema.py` | 1–83 | Checked defaults, extra=forbid, resume/snapshot hash patterns and all candidate/rule/answer/job fields. A missing constraint is not invented. Source parsing defects are in local/drive. |
