# Codex core review audit — 2026-10-03

Branch: agent/codex/T-001-contracts. Source through c1af160 plus the TP-10 graph
regression in this commit. All Codex-owned tasks are submitted for review;
this does not assert that the full product or external approval loop is done.

| Requirement | Authoritative evidence | Result and scope |
|---|---|---|
| T-001a models and five Protocols | contracts/primitives.py, ports.py, schemas, tests; 6234a75 | Implemented, unit/schema checks pass |
| T-001b state/event/command/status contracts | contracts/state.py, schemas, tests; 381491c | Implemented; initial missing interfaces authorized and recorded in proposal 001 |
| T-002 atomic claims and approval consumption | ledger/sqlite.py and ledger/tests/test_sqlite.py | Concurrency, rollback, stale/expired/replayed digest, durable intent and monotonic verified outcomes tested |
| T-015 deterministic policy | policy/tiers.py, allowlist.py, authority.py; ledger/tests/test_policy.py | Explicit facts, legal/EEO handoff, malicious plan, exact hosts, fixture-only submit and dry-run checks pass |
| T-017 graph/checkpoint/human gates | graph/build.py, subgraph_application.py, nodes, graph/worker tests | Native gates, truthful terminal routing, large form and pause/cancel regressions pass |
| T-018 intent before click; never re-click | graph/nodes/submit.py, test_submit.py, dated S12/S16 spike section | Four abrupt exits through real Chrome: no refill/new planning, no duplicate POST; after intent-before-click yields UNVERIFIED with zero POST per D-015 |
| T-019 outbound worker and CDP restart | worker/main.py, transport.py, worker tests and Chrome probes | Persistent command ACK/dedupe, HTTPS/auth/redirect boundaries, expiry envelopes, saved target and metadata restore tested |
| TP-01 approval binding/replay | ledger tests and submit tests | Core gate rejects missing/stale/expired/reused capabilities; actual CP signature verification remains owner work |
| TP-03..05 / S16 G2 | dated spike report plus test_cdp_resume.py | Actual graph/CDP/ledger process exits at four points; exact per-point outcomes recorded, not an unconditional exactly-one-click claim |
| TP-06 edit/re-approve | test_review_and_results.py and updated test_layouts.py | Actual ATS A/B: one edited input, old capability rejected, fresh bridge reattach, zero refill/planning and one fixture POST |
| TP-07 pause | graph and worker tests | Mid-fill and answer/login/CAPTCHA gates remain closed until explicit resume |
| TP-10 duplicate job | test_graph.py duplicate_ranked_postings regression | Both model-ranked entries normalize to one identity; second is SKIPPED_DUPLICATE, only one form opened/filled |
| TP-13 legal/EEO | deterministic policy tests and synthetic fixture human actor | Operator never automatically fills legal controls; optional EEO only under explicit blank/decline rules |
| TP-14 truthful reporting | test_review_and_results.py aggregate cases | Incomplete results cannot be labelled COMPLETED |
| Synthetic data / secrets / CAPTCHA | Probe/test sources and staged owned-path diff | No real employer submits or CAPTCHA interactions; env values never copied/read for these probes |

Latest clean check: `uv run --isolated --no-project --python 3.13
--with pytest==9.1.1 --with-requirements worker/requirements.txt
python -m pytest -c worker/pytest.ini -q` → 80 passed, 6 opt-in skipped.
The six Chrome cases passed separately; later edit-and-reattach variants and
prior-job metadata checks are recorded precisely in SPIKE_REPORT.md.
Ruff passes. `uv tool run pip-audit -r worker/requirements.txt
--progress-spinner off` reports no known vulnerabilities.

## Remaining dependencies — completion is not established

1. Kiro's current control_plane checkout contains only S5/S6 spikes; the channels
   path is absent. The actual signed review page, CP polling/ACK endpoints and
   ChannelPort snapshot/event delivery must land before the real review-link
   loop can be assembled and tested. Do not replace them with a fake and claim G3.
2. The worker/CP envelope and critical E06/E07/E08 payload conventions are in
   proposal 002. Worker accepts nested metadata and the proposed sibling expiry
   map. CP must verify capability signatures and mint read-only links; local
   consumption/release stays one worker-ledger transaction. OQ-CP-8 idle-gate
   expiry remains a manager decision; token expiry is already checked in code.
3. Integrate the actual source/model/channel adapters, then test signed POST
   approval against local fixtures and the phone/TLS gate. Real source/model/
   channel calls and the phone loop are not proven by synthetic Ports.
4. Claude reviews and the user approves merges/tags. No merge, push or rebase
   was performed; latest manager instructions retain these task branches.

The bus claim tool timed out twice during this audit despite healthy doctor
output. No lease conflict naming a peer was returned. Work stayed in Codex-owned
paths and append-only Codex notes, under the documented bus-offline fallback;
no reservation override was attempted. Prior finish workflows validated and
released leases but Beads closure failed. These backend issues do not prove
external integration or justify changing another owner's code.
