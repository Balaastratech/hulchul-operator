# Graph integration and current evidence

`Services` holds adapter instances outside checkpoints. `sqlite_graph(services,
path)` wires synchronous LangGraph 1.2.12 with SqliteSaver 3.1.1. Use `graph.invoke`
with configurable `thread_id=run_id`, recursion_limit=5000. Ports run on one
Services-owned event loop, preserving Playwright's loop affinity; do not use
this synchronous runner from an already running asyncio loop. One worker owns
one application at a time.

Supply trusted allowlisted apply URLs, known ATS URLs and fixture origins. The
model cannot add hosts or fixture authority. `injection_scan(description)` must
be supplied for nonempty posting descriptions; missing scanner blocks those
postings. The scanner must implement change deterministic plus model classification.
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

Current checks use synthetic Ports, SQLite checkpoints, and opt-in system Chrome
CDP probes through BrowserBridge and the browser owner's actual extractor,
executor, classifier and navigator. Four abrupt process exits prove no refill,
no new planning on resume, and at most one server-counted fixture submission.
A crash after durable SUBMITTING but before the click yields UNVERIFIED with
zero clicks; recovery never retries it. These probes use one simple local form,
headless Chrome and fake model/data/channel Ports. Shared ATS A/B also pass fixture review/approval/submit and graph re-entry
checks. Full product source/model/channel integration and phone/G3 remain
separate checks; see the dated S12/S16 and shared-layout reports.

Runtime failures become visible FAILED/BLOCKED outcomes with node/type-only
reasons; no exception text or candidate inputs are logged. BrowserBridge restores
stable field/action metadata, reattaches the exact target and checks actual
page/form targets and upload bytes before submit.

Clean environment check (no project/private venv dependency):
`uv run --isolated --no-project --python 3.13 --with pytest==9.1.1
--with-requirements worker/requirements.txt python -m pytest -c worker/pytest.ini -q`.
Opt-in Chrome probes require HULCHUL_BROWSER_SOURCE pointing to the browser
owner's checkout or the merged repo. Integration supplies the trusted factory,
source adapters, injection scanner, channel and control-plane endpoints.
