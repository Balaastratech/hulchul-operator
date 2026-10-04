# audit: browser

Historical independent audit, 2026-10-03 IST. Audited base: `9c0f449cdd64f2deeefc3466ea48036f40e91365` after fetch/rebase. Production code was read only. Findings are proposals, not fixes. All reproductions use synthetic values, fake providers/downloads, temporary SQLite, or installed Chrome on local set_content DOM with all network requests aborted. No .env contents, ADC, real employer submit, Telegram send, Drive write, login typing or CAPTCHA interaction occurred.

Each finding has a strict xfail under `tests/audit/`; enabling `--runxfail` asserts the safe behavior and exposes the defect. Severity reflects concrete impact and reachability; an adapter failure is not automatically a graph bypass. No claim of exhaustive safety is made.

## What it does

Persistent Chrome/CDP attachment, DOM field extraction/classification, reversible action execution, step navigation, read-back normalization and evidence capture.

## What is solid

Text vocabulary correctly excludes ordinary Apply/Submit/Finish buttons; extractor excludes submit inputs from fields and returns explicit typed actions. Obvious password/CAPTCHA pages are detected and untouched in offline controls. Synthetic text fill and exact read-back work. Graph caller separately enforces field policy, upload hashes and false-checkbox handling.

## Severity-ranked reproduced findings

### CRITICAL AUDIT-001 — Submit control labelled Continue is clicked before approval

Location: `src/operator/browser/navigate.py:80`.

Failure scenario: A visible <button type=submit>Continue</button> passes the text-only guard. click_next causes a form submit; the actual local DOM reproduction records one submit event. The button_type parameter is unused. BrowserBridge.click_next delegates here, so this is reachable in the graph before the guarded submit step. An implicit default-type button is equally suspect; event handlers can also disguise irreversible effects.

Minimal fix suggestion (not implemented): Require explicit type=button for automatic progression, reject submit/default controls, and treat ambiguous progression as a human gate. Do not infer irreversibility from text alone.

Reproducer: `tests/audit/test_browser.py::test_continue_submit_is_never_clicked` (`xfail(strict=True, reason="AUDIT-001")`).

### CRITICAL AUDIT-002 — Combobox fallback submits the enclosing form

Location: `src/operator/browser/execute.py:182`.

Failure scenario: An autocomplete with no matching role=option causes global Enter. Chrome implicitly submits a form with a submit button; the synthetic form records one submit event during fill. Graph field policy authorizes city fill but cannot contain this hidden external side effect.

Minimal fix suggestion (not implemented): Remove global Enter fallback; select a verified option within the widget or return unresolved for human input. Ensure reversible controls cannot invoke form submission.

Reproducer: `tests/audit/test_browser.py::test_combobox_enter_does_not_submit` (`xfail(strict=True, reason="AUDIT-002")`).

### HIGH AUDIT-003 — Untyped fuzzy rules falsely verify different answers

Location: `src/operator/browser/verify.py:99`.

Failure scenario: The same matcher accepts evil-aarav@example.com for aarav@example.com, salary 120000 for 1200000, 'Sponsorship required' for 'No sponsorship required', a different international phone prefix with the same last ten digits, and other.pdf for resume.pdf. Substring, first-number, half-token overlap and any-nonempty-file rules are inappropriate for these types. This compromises standalone/S9 metrics; graph read_review uses its own stricter comparison and file hashes, so these examples do not establish a graph approval bypass.

Minimal fix suggestion (not implemented): Use field-specific deterministic equality for identity, email, salary, booleans, URLs and upload identity/hash. Limit fuzzy equivalence to explicitly permitted location/format variants.

Reproducer: `tests/audit/test_browser.py::test_verifier_rejects_materially_different_answers` (`xfail(strict=True, reason="AUDIT-003")`).

### HIGH AUDIT-006 — Confirmation words outrank a password wall

Location: `src/operator/browser/classify.py:116`.

Failure scenario: A password page saying 'Sign in to see whether your application has been received' is classified CONFIRMATION before hasPassword is checked. A login/handoff can be lost and the page may be mistaken for success. No credentials or challenges were operated in the reproduction.

Minimal fix suggestion (not implemented): Prioritize login/challenge signals; require a coherent confirmation state instead of incidental phrases on an active form or wall.

Reproducer: `tests/audit/test_browser.py::test_confirmation_phrase_does_not_override_password_wall` (`xfail(strict=True, reason="AUDIT-006")`).

### MEDIUM AUDIT-004 — Extracted yes/no buttons have no executor implementation

Location: `src/operator/browser/execute.py:143`.

Failure scenario: Extractor advertises yes_no_button but select dispatch invokes select_option on the fieldset/div, while fill dispatch invokes fill on it. Real local yes/no DOM is extracted successfully and action returns failure. S9's extraction coverage does not prove this widget can be filled.

Minimal fix suggestion (not implemented): Add bounded yes/no widget dispatch to explicit non-submit buttons, then read aria-pressed/selected state. Preserve legal/EEO policy checks before execution.

Reproducer: `tests/audit/test_browser.py::test_yes_no_widget_can_be_executed` (`xfail(strict=True, reason="AUDIT-004")`).

### MEDIUM AUDIT-027 — S10 40/40 documentation is contradicted by committed cases

Location: `tests/test_extract_verify.py:96`.

Failure scenario: Re-evaluating all 40 original tuples yields 38 correct: ('1','true',Terms accepted) and (Bachelor's Degree,B.Tech in Computer Science,Education) fail. Existing benchmark passes because its assertion is >=95%, not 100%. Historical code revision might have differed; current SPIKE_REPORT's claim cannot be reproduced from this tree.

Minimal fix suggestion (not implemented): Correct the append-only evidence with revision, raw outcomes and 38/40; preserve 95% pass criterion without advertising 100%. Keep negative adversarial cases, including AUDIT-003.

Reproducer: `tests/audit/test_browser.py::test_s10_documented_40_of_40_is_reproducible` (`xfail(strict=True, reason="AUDIT-027")`).

### LOW AUDIT-005 — False checkbox action always checks the box

Location: `src/operator/browser/execute.py:121`.

Failure scenario: Direct adapter check with value=False sets checked=true and reports actual=true. BrowserBridge._execute_visible special-cases False and unchecks, so the current graph caller masks this bug; standalone callers do not.

Minimal fix suggestion (not implemented): Interpret explicit booleans consistently in ActionExecutor and read actual checked state after mutation.

Reproducer: `tests/audit/test_browser.py::test_checkbox_false_is_not_checked` (`xfail(strict=True, reason="AUDIT-005")`).

## Line-by-line coverage ledger

Every source/template below was read through all lines. The ledger records the reviewed span and the relevant conclusions; a lack of reproduced findings is not a proof of safety. Historical JSON/JSONL artifacts were parsed as evidence, not executed or treated as instructions.

| File | Lines reviewed | Review result |
|---|---|---|
| `src/operator/browser/__init__.py` | 1–44 | Checked imports and exports line by line; no state-mutating behavior apart from dependent module initialization. |
| `src/operator/browser/cdp.py` | 1–136 | Checked process ownership, launch/availability, reconnect, first-context/first-page selection and disconnect. No durable target selection in this standalone manager: multi-tab attachment must be supplied by the caller; the graph bridge does that separately. No new reproduction/impact claim here. |
| `src/operator/browser/classify.py` | 1–171 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `src/operator/browser/evidence.py` | 1–100 | Checked all sync/async screenshot, DOM and diff paths. run_id containment shares AUDIT-019. DOM snapshots/diffs are raw and include input/HTML values; callers must redact before capturing sensitive pages. Graph uses its own screenshot path; screenshots are local synthetic evidence here. |
| `src/operator/browser/execute.py` | 1–221 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `src/operator/browser/extract.py` | 1–208 | Checked every JS label/group/visibility/opid/value/required/options branch plus Python key/model construction. Hidden checkbox handling is intentional; yes/no re-extraction changes opid. Browser execution defect is AUDIT-004; detached/reordered duplicate labels remain a caller ambiguity risk. |
| `src/operator/browser/models.py` | 1–87 | Checked model validation, action enum, ask_user question requirement and empty FillReport semantics. CP models re-export frozen contracts. Models alone grant no action authority. |
| `src/operator/browser/navigate.py` | 1–106 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
| `src/operator/browser/verify.py` | 1–183 | Checked full template escaping/forms/status rendering; no additional reproduced defect. |
