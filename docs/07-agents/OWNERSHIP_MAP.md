# Ownership map (owners = ASSIGNMENT_PROPOSAL v2, decided by user)
Claude (manager) owns no source paths; Claude owns `docs/04-decisions/**` and board/registry consistency only.

"Consumers" = modules that import it. Changing an exported interface of a module with consumers is a B2.

| Module | Paths | Class when changed | Consumers | Depends on spikes | Owner |
|---|---|---|---|---|---|
| contracts | `src/operator/contracts/**` | B2/B3 | everything | — | codex (holds contract lease) |
| ledger | `src/operator/ledger/**` | B3 schema / B1 impl | graph, control_plane | — | codex |
| llm | `src/operator/llm/**` (ports, vertex, gemini_api, prompts, judge) | B1 | graph nodes | S13 | antigravity |
| data | `src/operator/data/**` | B1 | graph `load_data` | S4 | antigravity |
| browser | `src/operator/browser/**` | B1 | graph nodes | S1 S2 S7 S9 S10 S11 | antigravity |
| policy | `src/operator/policy/**` | B3 rules / B1 impl | graph, control_plane | S8 | codex (tiers/allowlist/authority) + antigravity (injection.py) |
| graph | `src/operator/graph/**` | B3 topology / B1 single node | worker | S3 S12 S16 | codex |
| channels | `src/operator/channels/**` | B1 | control_plane, worker | S5 S15 | kiro |
| control_plane | `control_plane/**` | B1 (B2 if routes change) | worker, channels | S5 S6 | kiro |
| worker | `worker/**` | B1 | — | S2 S16 | codex |
| fixtures (login_wall/, captcha_stub/ subfolders are antigravity; rest kiro) | `fixtures/**` | B0 | tests, evals, demo | — | kiro |
| tests / evals | `tests/**`, `evals/**` | B0 | — | — | kiro (tests of own code) / antigravity (evals) |
| sample_data | `sample_data/**` (synthetic persona, same schemas as Drive) | B0 | data, tests | S4 | kiro |
| docs | `docs/**` | B0 | all | — | user/integrator (agents append only where allowed) |
| research | `research/**` | B0 | — | — | any (append-only) |
| deploy | `Dockerfile`, `deploy/**`, `.github/**` | B1 | — | S6 | kiro |
