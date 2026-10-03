# Graph integration and current evidence

`Services` holds adapter instances outside checkpoints. `sqlite_graph(services,
path)` wires synchronous LangGraph 1.2.12 with SqliteSaver 3.1.1. Use `graph.invoke`
with configurable `thread_id=run_id`, recursion_limit=150. Ports run on one
Services-owned event loop, preserving Playwright's loop affinity; do not use
this synchronous runner from an already running asyncio loop. One worker owns
one application at a time.

Supply trusted allowlisted apply URLs, known ATS URLs and fixture origins. The
model cannot add hosts or fixture authority. `injection_scan(description)` must
be supplied for nonempty posting descriptions; missing scanner blocks those
postings. The scanner must implement T-016 deterministic plus model classification.
Descriptions never enter the field planner. Ranking can only choose eligible IDs.

`submission_urls` is a required browser-adapter callback for any submit. It
returns the live current page URL and every candidate form/submit target URL,
not URLs copied from the posting. All must be configured fixture origins.
Without the callback the handler raises before recording intent or clicking.
The source BrowserPort interface remains stable; this safety callback is wired
in Services by the integration factory and must inspect the actual browser.

The application graph has native review, answer, handoff and pause interrupts.
The review interrupt payload identifies run/job/snapshot and contains read-back
data. The control plane renders it at its own review URL, verifies signed POST
capabilities, records the approval digest, and delivers a typed Command. Resume
with `langgraph.types.Command(resume=command.model_dump(mode='json'))`.

Legal/EEO/challenge controls always require manual browser completion. Automatic
unknown-fact answers and edits only target a known reversible field. Each edit
invalidates old approvals before touching the browser. Control-plane commands
are authenticated at the transport boundary; the graph checks their bindings.

Current checks use synthetic fake Ports and a real SQLite checkpointer. They
prove persisted gate re-entry, no refills at review resume, pause and CAPTCHA
handoff routing. They do not prove CDP reconnect or actual fixture submission.
S12/S16 and G2 require T-003/T-004/T-011..T-014 adapters and fixtures, not present
on this branch. Runtime failures become visible FAILED/BLOCKED outcomes with
node/type-only reasons; no exception text or candidate inputs are logged.

Run: `src/operator/graph/.venv/Scripts/python.exe -m pytest
src/operator/graph/tests src/operator/ledger/tests src/operator/contracts/tests -q`.
The local ignored venv pins the documented runtime. Integration must provide
the adapters and attach the persistent browser target before resuming.
