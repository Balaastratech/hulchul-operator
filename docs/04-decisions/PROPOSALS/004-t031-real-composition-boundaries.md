# T-031: real composition interface mismatches (2026-10-03)

Owner: Codex. Status: proposal for manager/peer review; no locked decision changed.

The factory must use the merged ports without editing Antigravity's LLM/data/browser or
codex-b's control plane/channels paths. These issues were found by reading the merged code:

1. LocalFolderDataSource requires profile.json, but sample_data and Drive contain profile.md
   with front matter. Profile keys include first_name, last_name, city and requires_sponsorship;
   frozen Profile instead has name, location and sponsorship. Rules hard constraints are in a
   fenced YAML block, while the port parses front matter only; decline_to_answer maps to decline.
   Composition stages normalized files under the run directory, delegates parsing/snapshotting
   to LocalFolderDataSource and retains raw source hashes. Explicit name components/country become
   source-attributed answer rows. No source file is changed.
2. The queue port loads only URL/company/title, omitting descriptions, apply links, salary/remote
   metadata. Composition reads fixture posting HTML (including hidden text and comments), extracts
   its apply link and explicit salary/remote signals before the existing scanner/ranker. Live public
   postings with absent salary or incompatible roles are still filtered by authoritative rules.
3. DrivePublicDataSource's folder discovery/naming expects profile.json for success. Composition
   uses its download operation, then normalizes profile.md when the required bundle is complete.
   Partial downloads fall back explicitly; the manifest names the actual source. Drive lacks job_queue
   in the documented folder, so the local queue supplements it, also named in the manifest.
4. Graph E07/E08 payload.review differs from WebChannel's payload.review_snapshot. RealChannel
   translates at composition and uses the real HttpSink/WebChannel and optional TelegramChannel.
   Graph E14 supplies jobs as a dict; Telegram expects a list (composition should adapt this too).
5. The control plane has no origin/submission authority setting and its job page offers Approve
   for every snapshot. T-031's app composition wraps it with durable snapshot-bound submission
   policy, hides the approve form and shows "submission disabled for this site (D-014)" for public
   sites; even a manually supplied valid approve token is denied with 403 before queuing. Core
   pre-submit and BrowserBridge checks remain independent fixture-only guards. Proposed long-term
   home: codex-b/Kiro's CP, with an authenticated worker-supplied job origin and deterministic
   fixture authority; never infer permission from model output. Until then, start the guarded app
   through run_real.py, not the bare control_plane app, for the real-run UI.
6. InjectionClassifier.check_llm returns flagged=false on provider error. Composition treats
   confidence=0 as quarantined (fail closed); the peer should fix that behavior in its lane.
7. CP answer values are text-only, while graph ask_user requires booleans for checkbox/radio.
   The launcher uses RealTransport to convert only exact true/false text for the checkpoint's
   named boolean field. Unknown values stay unmodified and fail closed. The peer CP should accept
   contract JsonValue for answers. The stock worker CLI still uses its existing HttpTransport;
   use run_real.py for this boolean gate compatibility. No worker/CP path was changed.
8. Gemini sometimes chooses Goal's dry_run default for an ordinary Apply request. RealLLM adds
   explicit intent guidance (Apply = normal with mandatory approval; an explicit dry/fill-only
   request remains dry_run), never overrides the validated mode. Source-path guidance and
   same-file upload path normalization bridge model output to the frozen vocabulary. Optional
   proposals rejected by the same existing policy become skip rather than guessed inputs;
   required proposals still pass unchanged into the graph's human escalation. check(false) on a
   radio becomes skip (do not select that radio). No policy authority was enlarged.
9. The merged Drive folder scraper discovers no files for the known public demo. The composition
   uses the exact demo folder/file IDs from DATA_SOURCES.md as documented configuration; another
   folder can provide DRIVE_FILE_IDS_JSON or use discovery. Google Sheet exports omit final newlines;
   staging rewrites CSV before appending facts so the first-name row cannot corrupt the last answer.
   Legacy sensitivity labels are explicitly normalized: low -> normal, medium/high/eeo -> sensitive.

BrowserBridge itself already satisfies the CDP facade and final submission URL checks. No shared
contract, graph topology, peer path, .env, credential or locked decision was modified. AI assistance:
Codex generated the composition, launcher, regression checks and this proposal.
