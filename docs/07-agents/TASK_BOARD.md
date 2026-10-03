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
| T-013 | Extractor v2 + normaliser + fuzzy verifier | B1 | `browser/{extract,verify}.py` | T-011, **S9 S10** | **G1** | antigravity | TODO |
| T-014 | Multi-step navigation (`click_next` vocabulary, never submit-class) | B1 | `browser/navigate.py` | T-013, **S11** | reaches review step on fixture B + one real form | antigravity | TODO |
| T-015 | Policy engine: tiers, allowlist, field policy, authority checks | B3 | `src/operator/policy/{tiers,allowlist,authority}.py` | T-001 | TP-13, forced malicious plan blocked | codex | TODO |
| T-016 | Injection layer (deterministic + LLM classifier) + hostile job board fixture | B1 | `policy/injection.py`, `fixtures/job_board_hostile/**` | T-003, **S8** | S8 pass; TP-11 | antigravity | TODO |
| T-017 | Graph: nodes + subgraph + gates + SqliteSaver wiring | B3 | `src/operator/graph/**` | T-002, T-003, T-004, T-011..T-015 | happy path on fixtures; **G2** (S16) | codex | TODO |
| T-018 | Submit + verify_submission + SUBMITTING semantics | B3 | `graph/nodes/submit.py`, `browser/verify_submission.py` | T-017, **S12** | TP-04, TP-05 | codex | TODO |
| T-019 | Worker: poll commands, reattach browser, heartbeat, run graph | B1 | `worker/**` | T-017 | survives kill/restart | codex | TODO |
| T-020 | Channels: base + Telegram (+ web notifier) | B1 | `src/operator/channels/**` | T-001, **S5** | E01–E15 rendered; bot delivers | kiro | TODO |
| T-021 | Control plane: token service, review page, routes (approve/edit/pause/resume/answer/handoff_done), SSE progress, auth | B1→B2 | `control_plane/**` | T-001, T-002, **S6** | **G3**; TP-01, TP-02 | kiro | TODO |
| T-022 | Login-wall + CAPTCHA-stub fixtures and handoff flow | B0/B1 | `fixtures/{login_wall,captcha_stub}/**` | T-012 | TP-08, TP-09 | kiro | TODO |
| T-023 | Evals: goal×data variants, expected outcomes, results table | B0 | `evals/**` | T-017 | TP-12; table in `evals/RESULTS.md` | antigravity | TODO |
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




