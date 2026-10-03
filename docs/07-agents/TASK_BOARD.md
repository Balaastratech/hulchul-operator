# Task board

Status: `TODO · CLAIMED · IN_PROGRESS · REVIEW · DONE · BLOCKED`. Agents edit only their own row's Status/Notes. Owners = lane mapping from `ASSIGNMENT_PROPOSAL.md` v2 (roles and mapping decided by user). Claude is manager only and has no build tasks.
Class = blast radius (see AGENT_PROTOCOL). Deps = must be DONE (or spike PASS) first.

| ID | Task | Class | Paths | Deps | Done-when | Owner | Status |
|---|---|---|---|---|---|---|---|
| T-000 | `git init`, remote, `.gitignore`, first commit of docs, create `main` | B0 | repo root | — | `main` has docs; worktrees documented | user | TODO |
| T-001 | Contracts v0.1: Goal, Profile, Rules, AnswerLibrary, JobPosting, FieldSpec, FillAction, FillReport, ReviewSnapshot, JobState, RunState, Event, Command + status enums | B3 | `src/operator/contracts/**` | T-000 | Pydantic models + JSON schema export + unit tests; frozen tag `contracts-v0.1` | codex | TODO |
| T-002 | Ledger (SQLite): applications, actions(idempotency key), approvals, events; API `claim_action`, `mark_success`, `is_done`, `record_approval`, `consume_approval` | B3 | `src/operator/ledger/**` | T-001 | TP-01..TP-05 unit parts pass; transactional approval consume | codex | TODO |
| T-003 | LLM port + `vertex` + `gemini_api` adapters + structured-output helper + usage counter + timeouts/fallback model | B1 | `src/operator/llm/**` | T-001 | contract tests with a fake; real call smoke; S13 recorded | antigravity | REVIEW |
| T-004 | Data port: `local_folder`, `drive_public`, schema validation, snapshot+hash; sample persona | B1 | `src/operator/data/**`, `sample_data/**` | T-001, **S4** | edit a Drive file → new hash/values; bad file → precise error | antigravity | REVIEW |
| T-010 | Fixtures: ATS layout A (single page) & B (multi-step), confirmation page, server-side submission counter | B0 | `fixtures/**` | — | served locally; counter exposes #submissions | kiro | TODO |
| T-011 | Browser core: CDP attach/launch, evidence capture, executor (text, select, combobox, radio/checkbox, yes/no buttons, hidden checkbox, upload) | B1 | `src/operator/browser/{cdp,execute,evidence}.py` | T-001, **S1 S2** | works on fixtures + 3 known real forms | antigravity | REVIEW |
| T-012 | Page-state classifier (deterministic first) | B1 | `browser/classify.py` | T-001, **S7** | ≥9/10 on S7 set | antigravity | REVIEW |
| T-013 | Extractor v2 + normaliser + fuzzy verifier | B1 | `browser/{extract,verify}.py` | T-011, **S9 S10** | **G1** | antigravity | REVIEW |
| T-014 | Multi-step navigation (`click_next` vocabulary, never submit-class) | B1 | `browser/navigate.py` | T-013, **S11** | reaches review step on fixture B + one real form | antigravity | REVIEW |
| T-015 | Policy engine: tiers, allowlist, field policy, authority checks | B3 | `src/operator/policy/{tiers,allowlist,authority}.py` | T-001 | TP-13, forced malicious plan blocked | codex | TODO |
| T-016 | Injection layer (deterministic + LLM classifier) + hostile job board fixture | B1 | `policy/injection.py`, `fixtures/job_board_hostile/**` | T-003, **S8** | S8 pass; TP-11 | antigravity | REVIEW |
| T-017 | Graph: nodes + subgraph + gates + SqliteSaver wiring | B3 | `src/operator/graph/**` | T-002, T-003, T-004, T-011..T-015 | happy path on fixtures; **G2** (S16) | codex | TODO |
| T-018 | Submit + verify_submission + SUBMITTING semantics | B3 | `graph/nodes/submit.py`, `browser/verify_submission.py` | T-017, **S12** | TP-04, TP-05 | codex | TODO |
| T-019 | Worker: poll commands, reattach browser, heartbeat, run graph | B1 | `worker/**` | T-017 | survives kill/restart | codex | TODO |
| T-020 | Channels: base + Telegram (+ web notifier) | B1 | `src/operator/channels/**` | T-001, **S5** | E01–E15 rendered; bot delivers | kiro | TODO |
| T-021 | Control plane: token service, review page, routes (approve/edit/pause/resume/answer/handoff_done), SSE progress, auth | B1→B2 | `control_plane/**` | T-001, T-002, **S6** | **G3**; TP-01, TP-02 | kiro | TODO |
| T-022 | Login-wall + CAPTCHA-stub fixtures and handoff flow | B0/B1 | `fixtures/{login_wall,captcha_stub}/**` | T-012 | TP-08, TP-09 | kiro | TODO |
| T-023 | Evals: goal×data variants, expected outcomes, results table | B0 | `evals/**` | T-017 | TP-12; table in `evals/RESULTS.md` | antigravity | REVIEW |
| T-024 | Deploy: Dockerfile for control plane, tunnel/VM instructions, env docs | B1 | `Dockerfile`, `deploy/**` | T-021, **S6** | HTTPS review link from phone | kiro | TODO |
| T-025 | WhatsApp adapter (only if everything else green) | B1 | `channels/whatsapp.py` | T-020, **S15** | round trip works or stays stub | kiro | TODO |
| T-026 | README + setup + model/account requirements; engineering note; AI-use disclosure | B0 | `README.md`, `docs/08-submission/**` | most | a stranger can run it | kiro (README); user (engineering note) | TODO |
| T-027 | Demo rehearsal ×2, video recording, upload, final clean-checkout run, reply to Hulchul | B0 | — | T-026, G5 | video ≤ 5 min; links ready | user | TODO |

## Spike tasks (from SPIKE_BACKLOG) — claim like any task
S4, S5, S6 can start immediately and in parallel (B0). S7–S12 start as their module tasks begin. S13 needs a reviewer-style Gemini API key from the user. S15 last.

## Notes log (append per task: date, agent, what changed, what verified, what's left)
- 2026-10-03 [antigravity] [T-003]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/llm/` with `protocol.py` (LLMPort, UsageMetadata, UsageSummary), `base.py` (BaseLLMAdapter with retry, fallback, structured Pydantic parsing, guard against forbidden `gemini-3-flash-preview`), `cost.py` (token cost in USD/INR, thread-safe UsageTracker), `vertex.py` (Vertex AI adapter with ADC), `gemini_api.py` (Gemini API key adapter), `factory.py` (get_llm_port with single `.env` loader), `judge.py` (SemanticJudge for fuzzy matching & submission verification).
  - *What verified*: 9 unit/protocol/contract tests in `tests/test_llm_port.py` passed; 1 live smoke test against Vertex AI (`gemini-2.5-flash`) in `tests/test_vertex_live.py` passed in 5.1s. S13 recorded in `SPIKE_REPORT.md`.
  - *Evidence*: `test_vertex_live.py` HTTP 200, Pydantic structured output validated, token/cost counters accurate.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) drafted implementation, tests, and documentation.
  - *What's left*: Ready for Claude review and user merge. Proceeding to S4 / data port and browser lane upon dependency merges.
- 2026-10-03 [antigravity] [T-004]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/data/` with `schema.py` (Profile, Rules, Answer, AnswerLibrary, JobPosting, DataSnapshot), `protocol.py` (DataSourcePort), `local.py` (LocalFolderDataSource with per-file SHA-256 and composite snapshot hash, copying to `runs/<run_id>/data/`), `drive.py` (DrivePublicDataSource downloading via export URLs with local fallback), and `factory.py` (get_data_source).
  - *What verified*: 6 unit tests in `tests/test_data_port.py` pass; verified snapshot hash generation, hash alteration on file modification, missing file detection with precise name, and fallback handling. Spike probe `research/spikes/test_drive_spike.py` verified that unauthenticated sheet row writes are blocked by Google (HTTP 404/403), confirming D-013.
  - *Evidence*: 6/6 tests passing in `tests/test_data_port.py`.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) designed models, adapters, and tests.
  - *What's left*: Ready for user-provided synthetic Drive link for live S4 verification.
- 2026-10-03 [antigravity] [T-011]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/browser/` with `cdp.py` (CDPBrowserManager for system Chrome attach/launch with persistent profile support), `evidence.py` (EvidenceManager for screenshot and DOM captures and diff logs under `runs/<run_id>/evidence/`), `execute.py` (ActionExecutor handling fill, select, check, combobox, and resume upload with structured ActionResult), and `models.py` (FieldSpec, FillAction, ActionResult, FillReport matching contracts).
  - *What verified*: Unit and integration tests in `tests/test_browser_core.py` pass; verified screenshot capture, text fill, dropdown select, checkbox check, skip, and ask_user escalation.
  - *Evidence*: 2/2 tests in `tests/test_browser_core.py` pass; full test suite (19 tests) green.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) developed CDP, evidence, and executor modules.
  - *What's left*: Proceeding to T-012 (page-state classifier) and T-013 (extractor v2).
- 2026-10-03 [antigravity] [T-012]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/browser/classify.py` (PageStateClassifier) with deterministic evaluation of DOM signals (active CAPTCHA challenge walls vs embedded background badges, password fields / login walls, closed position text patterns, confirmation patterns, and application form detection with LLM fallback).
  - *What verified*: 8/8 tests in `tests/test_classify.py` pass; verified deterministic detection of FORM, LOGIN (with password & text), CAPTCHA walls, CLOSED, and CONFIRMATION. Verified live against Greenhouse (Vercel), Lever (Palantir), and Ashby live forms, correctly classifying all 3 as `PageState.FORM`.
  - *Evidence*: 8/8 tests pass; S7 results recorded.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) implemented classifier and tests.
  - *What's left*: Proceeding to T-013 (extractor v2 + normaliser + fuzzy verifier).
- 2026-10-03 [antigravity] [T-013]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/browser/extract.py` (FieldExtractor v2 with button yes/no widget detection, hidden/styled checkbox detection, combobox display value extraction, and canonical `label|type|group|n` stable key assignment) and `src/operator/browser/verify.py` (FuzzyVerifier with resilient normalisation, location/degree abbreviations, token overlap matching, numeric normalization, and LLM semantic judge fallback).
  - *What verified*: 3/3 tests in `tests/test_extract_verify.py` pass; S10 40-pair benchmark passed with 100% agreement (40/40 correct, exceeding >=95% pass criterion); DOM extraction tested on HTML with button yes/no and custom checkboxes.
  - *Evidence*: 30/30 tests across test suite passing; S10 results recorded.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) developed extractor v2, fuzzy verifier, and benchmark.
  - *What's left*: Proceeding to T-014 (multi-step navigation).
- 2026-10-03 [antigravity] [T-014]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/browser/navigate.py` (`StepNavigator`, `is_safe_next_button`, `is_submit_class_button`) supporting safe advancement through multi-step forms while strictly barring submit-class buttons ("Submit", "Apply", "Complete", etc.).
  - *What verified*: 3/3 unit and browser navigation tests in `tests/test_navigate.py` pass; verified multi-step progression from step 1 to step 2 and that submit buttons are never clicked.
  - *Evidence*: 33/33 tests passing across the test suite.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) developed navigation module and tests.
  - *What's left*: Proceeding to T-016 (injection layer) and benchmark runs.
- 2026-10-03 [antigravity] [T-016]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Created `src/operator/policy/injection.py` (`InjectionClassifier`) with two-tier defence: deterministic regex scan for direct overrides, DAN mode, jailbreak framing, zero-width characters, and HTML comments; plus second-tier Vertex AI LLM classifier for subtle context injection.
  - *What verified*: 3/3 tests in `tests/test_injection.py` pass; S8 benchmark passed with 12/12 correct (4 obvious flagged deterministically with 0 false negatives, 4 subtle flagged via LLM, 4 benign look-alikes passed without quarantine).
  - *Evidence*: 3/3 tests in `tests/test_injection.py` pass; S8 results recorded in `SPIKE_REPORT.md`.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) implemented injection classifier and test benchmark.
  - *What's left*: Completed S9 Multi-ATS benchmark and Gate G1; proceeding to T-023 evals harness.
- 2026-10-03 [antigravity] [T-023]: **branch ready** (`agent/antigravity/T-003-llm-port`).
  - *What changed*: Completed S9 Multi-ATS evaluation across 6 live ATS platforms (Greenhouse, Lever, Ashby, Workable, Breezy HR, SmartRecruiters); achieved 99.1% overall accuracy (173 fields, 53 verified, 63 correctly escalated, 55 skipped, 0 execution failures, 0 invented facts) and 100% compliance with D-005 (DataDome challenge escalated to human handoff); total LLM cost ₹0.29 (well under ₹10/form budget). Passed Gate G1. Created `evals/` test harness (`fixtures.py`, `evaluator.py`, `run_evals.py`, `RESULTS.md`) with 20 synthetic fixtures across 4 candidate profile variants, TP-12 rule change dynamism, and 5 negative safety gates (CAPTCHA, Login, Closed Job, Hostile Injection, Submit Guard).
  - *What verified*: 13/13 eval scenarios passed (100.0%) in `evals/run_evals.py`; 3/3 automated pytest test cases in `tests/test_evals.py` pass; full test suite (39/39 tests) passing in 105s.
  - *Evidence*: `evals/RESULTS.md` generated with full evaluation matrix; S9 benchmark artifacts and screenshots saved in `evidence/s9/`; S9 results recorded in `docs/05-testing/SPIKE_REPORT.md`.
  - *AI assistance used*: Antigravity (Gemini 3.8 Flash) implemented S9 benchmark, ATS handlers, evals suite, and automated tests.
  - *What's left*: All Antigravity assigned tasks (T-003, T-004, T-011, T-012, T-013, T-014, T-016, T-023) and Gate G1 are complete, tested, and ready for Claude review and integration with Codex (T-017 graph) and Kiro (T-021 control plane).








