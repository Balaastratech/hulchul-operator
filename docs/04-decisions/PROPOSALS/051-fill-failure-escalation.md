# Proposal 051: Fill Failure Escalation and Bounded Human Hand-off Routing

- **Author**: Antigravity (`agent/antigravity/T-051-llm-adaptive-fill`)
- **Date**: 2026-10-04
- **Target Owner**: Codex (`src/operator/graph/nodes/verify_fill.py`)
- **Status**: PROPOSED
- **Relates to**: D-004, D-005, D-033, T-051

---

## 1. Context & Motivation

During execution of the Platform Engineer ATS B fixture under live test, `<input type="date">` for "EARLIEST START DATE" received `"Within 30 days of an offer"` from the answers library. Playwright threw:
```
Locator.fill: Error: Malformed value (expected yyyy-mm-dd)
```
In the previous baseline graph implementation, repeated execution/read-back failure caused `verify_fill` to enter a loop leading directly to `route = "human_handoff"`:
```python
if not job.fill_report.verified:
    route = "repair" if job.repair_attempts < 2 else "human_handoff"
```
When `human_handoff` was triggered for a fill failure, the user was shown a confusing browser hand-off prompt ("Action needed in the browser") rather than an answer request. Because the browser page was not actually blocked by a CAPTCHA, authentication wall, or legal disclosure, human hand-off was inappropriate and caused the run to stall indefinitely or loop rather than proceeding to review.

Under **D-033** (*"adapt with the LLM, hard-code only safety"*), browser rejections trigger one bounded in-flight repair attempt by passing the error text and field constraints back to the LLM. If the repair also fails, the field must be marked as needing the user's answer (ask user), rather than escalating the entire run into an endless browser hand-off loop.

---

## 2. Graph Node Analysis: `verify_fill.py`

In `src/operator/graph/nodes/verify_fill.py`:
```python
def verify_fill(state: GraphState, services: Services) -> dict:
    """Escalations become human gates; missing result keys fail verification."""
    run = read_run(state)
    job = active_job(run)
    if any(action.action == "ask_user" for action in job.actions):
        job.status = JobStatus.NEEDS_ANSWER
        return update(run, route="ask_user")
    expected = [action for action in job.actions if action.action != "skip"]
    job.fill_report = services.call(services.browser.verify(expected))
    if {item.field_key for item in job.fill_report.fields} != {
        item.field_key for item in expected
    }:
        raise ValueError("verification omitted an intended field")
    if not job.fill_report.verified:
        route = "repair" if job.repair_attempts < 2 else "human_handoff"
    else:
        route = "click_next"
    return update(run, route=route)
```

Notice:
1. `ActionExecutor` (in `src/operator/browser/execute.py`) now catches execution errors, performs one LLM repair, and if that repair fails, converts the failed action in-place to `action="ask_user"` with `question="Needs your answer: {field.label} (suggested: {val})"`.
2. When `verify_fill` runs, `if any(action.action == "ask_user" for action in job.actions):` intercepts this and cleanly transitions to `JobStatus.NEEDS_ANSWER` (`route="ask_user"`).
3. However, if a field fails read-back verification (i.e. browser fill succeeded without throwing an exception, but the read-back value doesn't match the expected value) after 2 repair attempts, line 22 routes to `"human_handoff"`.

---

## 3. Recommended Codex Modification

For Codex's ownership of `src/operator/graph/nodes/verify_fill.py`:
When `job.repair_attempts >= 2` and `not job.fill_report.verified`, the unresolved fields should be escalated as questions to the user (`route = "ask_user"`, setting `job.status = JobStatus.NEEDS_ANSWER`) rather than routing to `"human_handoff"`, unless the page state classifier has specifically detected a `CAPTCHA` or `LOGIN` wall.

Specifically:
```python
    if not job.fill_report.verified:
        if job.repair_attempts < 2:
            route = "repair"
        else:
            # Mark unverified fields as ask_user so user is prompted in review/chat
            # instead of triggering a misleading browser handoff
            for item in job.fill_report.fields:
                if not item.matched:
                    item.escalated = True
            job.status = JobStatus.NEEDS_ANSWER
            route = "ask_user"
```

This ensures that form fill discrepancies cleanly surface to the user via the control plane and messaging channels without getting trapped in manual browser hand-off loops.
