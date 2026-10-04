# T-050: review screenshot timeout in minimized headed Chrome

Codex, 2026-10-04. Base: origin/main 673b734. No locked policy or shared
contract changes are proposed.

The focused ATS B regression reproduces fill -> E04 -> signed POST handoff_done
-> build_review FAILED, with zero fixture submissions, when headed Chrome is
minimized immediately before hand-off continuation. It passes on baseline main
when Chrome is not minimized. The recorded Northstar run is actually ATS A:
phone-proof-03/state.json names job-1001 and /ats_a/?job=job-1001; job-1002 maps
to ATS B. The Gender/Ethnicity/Veteran/Disability controls belong to ATS A.

## Evidence and exact timeout

- Baseline focused minimized ATS B: `1 failed, 1 deselected in 37.40s`.
- Failure: `build_review: TimeoutError; inspect node and resume manually`.
- Exact await: `BrowserBridge.read_review` at
  `screenshots=await self.capture_evidence()`; then `await self._call(capture)`
  executes `self.page.screenshot(path=str(path))` on the Playwright thread.
- Playwright: `Page.screenshot: Timeout 5000ms exceeded`; fonts loaded, but
  screenshot rendering does not complete with the window minimized. The test
  sets a short timeout solely to keep this reproduction bounded.
- [Traceback](050-evidence/failure.txt), [DOM](050-evidence/failure.html),
  [screenshot](050-evidence/failure.png). The screenshot is captured after the
  failed call by foregrounding the same tab. It shows ATS B already at Step 4,
  completed consent and eligibility, intact resume and an untouched Submit.
- A local replay of the recorded ATS A actions likewise reads all 33 fields
  successfully, then times out at screenshot capture when minimized. The same
  replay succeeds after `page.bring_to_front()`. Original run state did not
  record window state or a traceback, so its minimized state cannot be proven
  retrospectively. This patch fixes the independently reproduced failure; it
  does not claim a new real phone/Gemini/Drive proof.

## Change and safety

`src/operator/graph/adapters.py`, `BrowserBridge.capture_evidence`, now calls
`self.page.bring_to_front()` immediately before screenshot capture on its
existing executor thread. This activates the saved ATS target and restores
rendering. No field, form or navigation control is clicked. No Next/Back,
implicit-submit, CAPTCHA/login, authority, approval, or at-most-once submit
rules change. The browser module requires no change: the failed screenshot is
owned by this adapter rather than by StepNavigator.

The new opt-in integration test uses real system Chrome and native Worker,
LangGraph, ledger and CP gates, generated credentials and synthetic data. Its
planner is deterministic to avoid shortlist/model variance. It fills ATS B,
requires an explicit fixture-human consent/eligibility hand-off, posts the
actual signed CP form, reads every accumulated step at review, checks resume
and email, confirms panel 4 and zero submissions, and verifies no operator
submit operation. It covers headless normal rendering and minimized headed
Chrome. Failures retain the precise trace, synthetic DOM and screenshot.

## Verification

Fresh `.venv-t050` created with `python -m venv .venv-t050`, followed by
`.venv-t050/Scripts/python.exe -m pip install .` (successful clean installation).
Initial checks on 673b734: offline **651 passed, 99 skipped, 8 deselected,
3 xfailed in 82.73s**; new live regression **2 passed in 59.55s**; existing G3
**3 passed in 162.49s**. Final fetch brought in T-048/T-049; rebased cleanly
onto 672170d (round 14 docs atop 0fe4798). The dependency manifest is unchanged.
Final-base offline command: `.venv-t050/Scripts/python.exe -m pytest -q
-p no:cacheprovider` -> **703 passed, 99 skipped, 8 deselected, 2 xfailed in
133.16s (0:02:13)**. Final-base live command: `RUN_G3=1 .venv-t050/Scripts/python.exe -m pytest
tests/integration/test_g3_click_to_submit.py tests/integration/test_multistep_review.py
-m live -q -p no:cacheprovider` -> **5 passed in 286.20s (0:04:46)** (three
G3 cases plus normal and minimized-headed ATS B). Same clean environment. Scoped Ruff check/format, compileall and
whitespace check pass. pip-audit against the fresh environment reports no known
vulnerabilities; only the unpublished local package is excluded.

AI assistance: Codex generated the focused regression, one-line activation fix,
evidence capture and this diagnosis. No .env values were printed, no employer
application or CAPTCHA/login interaction occurred, and no push or merge was
performed.

Bus create/claim failed because Beads cannot spawn. `busctl doctor` confirms the
Beads CLI is unavailable; mail and memory services remain healthy. The finish
workflow passed browser-core validation, persisted memory and released leases,
but could not close a nonexistent/unavailable Beads issue. Explicit user task
ownership was used without overriding a peer lease.

**Branch ready**: `agent/codex/T-050-multistep-review`. No merge or push.
