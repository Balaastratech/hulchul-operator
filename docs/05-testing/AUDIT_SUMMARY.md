# independent adversarial audit summary

Historical independent audit, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## Top 10 findings and fix order

| Order | Severity / ID | Finding | Location |
|---|---|---|---|
| 1 | CRITICAL AUDIT-001 | Submit control labelled Continue is clicked before approval | `src/operator/browser/navigate.py:80` |
| 2 | CRITICAL AUDIT-002 | Combobox fallback submits the enclosing form | `src/operator/browser/execute.py:182` |
| 3 | HIGH AUDIT-007 | Second-tier classifier errors fail open | `src/operator/policy/injection.py:115` |
| 4 | HIGH AUDIT-008 | Keyword gate skips the required second-tier review | `src/operator/policy/injection.py:135` |
| 5 | HIGH AUDIT-009 | Subtle attack after 3000 characters is invisible | `src/operator/policy/injection.py:102` |
| 6 | HIGH AUDIT-016 | UTF-8 BOM silently drops authoritative YAML rules | `src/operator/data/local.py:43` |
| 7 | HIGH AUDIT-018 | Partial refresh mixes fresh and stale authoritative files | `src/operator/data/drive.py:111` |
| 8 | HIGH AUDIT-015 | Document profile download is incompatible with the loader | `src/operator/data/drive.py:120` |
| 9 | HIGH AUDIT-020 | Expired approval blocks every fresh approval for the same snapshot | `control_plane/store.py:639` |
| 10 | HIGH AUDIT-026 | Uploaded screenshot links have no serving route | `control_plane/routes/human.py:370` |

Fix the two preapproval submit paths first, before any further real ATS fill rehearsal. Next restore fail-closed/full-text injection handling and authoritative data consistency. Then unblock expired/no-op reviews and restore screenshot access. Follow with verifier hardening, redacted logs, real composition-layer review delivery, cost accounting and multi-chat reply identity. Changes belong to the assigned production owners and manager; this branch fixes none.

## Scope and findings

28 reproduced findings: **2 CRITICAL, 14 HIGH, 11 MEDIUM, 1 LOW**. There are 32 individual failing cases because AUDIT-003 has five independently parameterized mismatches. One positive offline DOM control verifies safe fill/read-back, ordinary Submit/Apply refusal and detected login/CAPTCHA handoff. Xfail strictness means a future fix produces XPASS and requires deliberate audit-test promotion/removal.

Reports: browser, LLM, data, injection, control plane, channels. Each includes purpose, solid behavior, severity-ranked scenarios, minimal fix proposals, exact source line and full source/template coverage ledger. Imports/exports and all historical control-plane spike sources/artifacts are included. Graph/worker production code was consulted only to establish caller reachability and mitigations, not independently re-audited or edited.

## Required safety checks

| Obligation | Evidence / conclusion |
|---|---|
| Only the guarded submit step may submit | **FAIL** AUDIT-001/002; actual local Chrome form-submit events during navigation/fill. No employer POST was sent. |
| CAPTCHA/login detected only | Positive local cases pass with zero typing/challenge interaction; mixed login/confirmation state fails AUDIT-006. Historical S7 pages were not re-fetched. |
| No secrets in URLs/HTML/logs | Provider keys are headers; CP server keys not in HTML; intentional view/evd capability URLs and POST-only act forms follow D-008. **FAIL** synthetic output in logs AUDIT-012 and configured URL credentials AUDIT-023. Telegram Bot API necessarily embeds bot token in its API path, with own logging suppressed/scrubbed; it is never a human review URL. |
| GET never mutates persistent state | Existing enumeration and authorized SSE checks rerun offline; source uses read-only DB helpers. Pure capability minting/in-memory subscriptions are intentional. Evidence GET is absent, not mutating. Test results recorded below. |
| EEO/legal never automatically filled | Graph caller retains deterministic field policy; browser executor assumes prevalidated actions. The historical S9 benchmark passes model actions directly to executor with only prompt rules, so S9 is not a deterministic legal/EEO-safety proof. No real sensitive field was filled. Core policy tests rerun in full suite. |
| Page text is data, never authority | **FAIL** unclassified input can proceed AUDIT-007/008/009. Classifier/judge prompts interpolate page text and do not provide an inviolable boundary. Graph policy gates still mitigate external actions; they do not replace quarantine. |
| Cost counters honest | **FAIL** AUDIT-010/011; billed thoughts omitted and tariffs understate published Gemini API standard cost. Parsed invalid JSON still records usage, which is solid. No paid calls were made. |

## Spike claim reconciliation / offline S9-style scope

S10: current committed 40 tuples yield **38/40**, not documented 40/40. Existing >=95% benchmark passes exactly at the threshold; AUDIT-027 makes the contradictory headline executable. The two misses are numeric '1' versus 'true' and Bachelor's Degree versus B.Tech in Computer Science. Additional independently constructed negative pairs all falsely match (AUDIT-003); raw S9 'verified' counts inherit this verifier limitation.

S13/S14: usage total can retain thoughts while cost drops them (AUDIT-010); pricing evidence and one-million-token arithmetic contradict 'accurately' tracked costs (AUDIT-011). Historical $0.00344/INR0.29 cannot be independently recalculated as billed cost without raw per-call usage and provider tariff at the measurement date. Latency, actual invoices, live provider parity and budget qualification are not re-established by an offline audit.

S8: original obvious/benign deterministic checks pass offline; the full 12-item test is live and asserts >=11 rather than an exact 12/12. Its 4 subtle cases are not proof of paraphrase/long-post/error handling; our probes contradict general robustness. No ADC/Vertex call was made to re-measure live catch rate. Some 'subtle' samples already match deterministic patterns.

S7: offline standard password/challenge detection works; incidental confirmation text defeats login priority. The report says 12 evaluated pages but enumerates 13; this is a documentation count inconsistency, not a new production bug. Historical real ATS observations are not retroactively disproved by synthetic edge cases.

S9/G1: independently authored local layouts exercise native text, checkbox False, yes/no group, combobox, safe navigation, disguised submit and read-back. This is an offline adversarial S9-style check, **not** a repeat of six real unseen ATS forms with a live planner. Benchmark inventiveness is initialized at zero without an independent fact oracle, legal/EEO actions rely on model prompt obedience, and resume uploads are marked verified immediately after execution without file-content read-back. Those metrics prove what the harness counted, not deterministic safety or zero hallucinations. Existing historical screenshots/results are retained unchanged.

The tracked 2,390-line `evidence/s9/s9_benchmark_summary.json` was parsed and its six per-site records reconciled: **53 verified + 63 escalated + 56 skipped + 1 unverified = 173 fields**; per-site counts and action-list lengths agree. The report's 54 filled can mean 53 verified plus one executed-but-unverified fill; it must not be read as 54 verified. This distinction is not an additional bug. The stored question coverage is 74/115 (64.3%), and the stored cost is $0.00344/INR0.2889 under the defective estimator. All 56 historical S5/S6 request-log records and three result JSON files were parsed without printing tokens or credentials.

S5/S6: historical records remain partial for phone action. Production GET/replay/auth checks are independently rerun locally, not a phone/tunnel test. G3's RecordingChannel explicitly converts review to review_snapshot, masking AUDIT-024; no claim that the unadapted production seam passed G3. No outbound bot message or webhook change was performed.

## Reproduction and verification

All commands are run from this worktree. Scratch output and pytest basetemp are under ignored `runs/`. Audit fixture restores cwd before deleting its temporary directories.

```powershell
python -m pytest tests/audit -q -p no:cacheprovider --basetemp=runs/audit-final
python -m pytest tests/audit --runxfail -q -p no:cacheprovider --basetemp=runs/audit-final-unmasked
$env:ENV_FILE = Join-Path (Get-Location) 'runs/audit-absent.env'
$env:FIXTURE_BASE_URL = 'http://127.0.0.1:1'
$env:FIXTURE_PORT = '0'
python -m pytest -q -p no:cacheprovider --basetemp=runs/audit-full-default
```

The unmasked command is expected to exit nonzero: it asserts the desired safe behavior and exposes every reported bug. No fixes are embedded in mocks or production code. Per-test artifacts are temporary synthetic inputs; full unmasked traceback output is ignored local evidence only.

Plain pytest collection initially failed with six `control_plane.test_*` import errors: the existing `tests/control_plane` package shares a name with production `control_plane`, and audit's early imports exposed that collision. Keeping audit control-plane imports inside test functions/fixtures resolved it; **default import mode now passes** without changing existing tests or production/test configuration. A separate importlib-mode run also passed. An initial E2E run reused a shared fixture server whose counter already contained two submissions; a fresh ephemeral fixture server resolved that contamination. No shared server was reset or stopped.

Verified results (local scratch outputs are ignored):

| Check | Result |
|---|---|
| Final audit suite | **1 passed, 32 xfailed** in 10.77 s; exit 0 |
| Final audit with `--runxfail` | **32 failed, 1 passed** in 11.19 s; expected exit 1, no setup errors |
| Full offline suite, default import mode and isolated fixtures | **432 passed, 6 skipped, 6 deselected, 32 xfailed** in 126.35 s; exit 0 |
| Full offline suite, importlib mode and isolated fixtures | **432 passed, 6 skipped, 6 deselected, 32 xfailed** in 68.50 s; exit 0 |
| Control-plane suite alone, importlib mode | **175 passed** in 28.75 s, including GET enumeration and authorized SSE checks |
| Ruff on `tests/audit` | **All checks passed** |
| Installed-dependency pip-audit | **No known vulnerabilities found**; local `hulchul-operator` skipped because not published on PyPI |

Ruff and pip-audit were run with `uv --cache-dir runs/audit-tool-cache tool run --from <tool> <tool>` because the system interpreter lacked them. The pip-audit invocation used `--path C:/Users/YUVRAJ/AppData/Local/Programs/Python/Python313/Lib/site-packages` to scan installed project dependencies rather than only the temporary tool environment. No project dependencies were modified. Test-generated `evals/RESULTS.md` was restored to its original content and excluded from this branch.
