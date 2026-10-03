# Bus task map (board ID -> Bala Agent Bus ID)

| Board | Bus ID | Owner | Task |
|---|---|---|---|
| T-001 | hulchul-operator-fq8 | codex | contracts v0.1a/b |
| T-002 | hulchul-operator-aqn | codex | Ledger (SQLite): applications, actions(idempotency key), approvals, ev |
| T-003 | hulchul-operator-6qk | antigravity | LLM port + `vertex` + `gemini_api` adapters + structured-output helper |
| T-004 | hulchul-operator-50f | antigravity | Data port: `local_folder`, `drive_public`, schema validation, snapshot |
| T-010 | hulchul-operator-ncp | kiro | Fixtures: ATS layout A (single page) & B (multi-step), confirmation pa |
| T-011 | hulchul-operator-qux | antigravity | Browser core: CDP attach/launch, evidence capture, executor (text, sel |
| T-012 | hulchul-operator-pdn | antigravity | Page-state classifier (deterministic first) |
| T-013 | hulchul-operator-a45 | antigravity | Extractor v2 + normaliser + fuzzy verifier |
| T-014 | hulchul-operator-uow | antigravity | Multi-step navigation (`click_next` vocabulary, never submit-class) |
| T-015 | hulchul-operator-b0m | codex | Policy engine: tiers, allowlist, field policy, authority checks |
| T-016 | hulchul-operator-t6z | antigravity | Injection layer (deterministic + LLM classifier) + hostile job board f |
| T-017 | hulchul-operator-o1c | codex | Graph: nodes + subgraph + gates + SqliteSaver wiring |
| T-018 | hulchul-operator-11d | codex | Submit + verify_submission + SUBMITTING semantics |
| T-019 | hulchul-operator-os4 | codex | Worker: poll commands, reattach browser, heartbeat, run graph |
| T-020 | hulchul-operator-jsq | kiro | Channels: base + Telegram (+ web notifier) |
| T-021 | hulchul-operator-ugz | kiro | Control plane: token service, review page, routes (approve/edit/pause/ |
| T-022 | hulchul-operator-dae | kiro | Login-wall + CAPTCHA-stub fixtures and handoff flow |
| T-023 | hulchul-operator-jxx | antigravity | Evals: goal×data variants, expected outcomes, results table |
| T-024 | hulchul-operator-g40 | kiro | Deploy: Dockerfile for control plane, tunnel/VM instructions, env docs |
| T-025 | hulchul-operator-rgn | kiro | WhatsApp adapter (only if everything else green) |
| T-026 | hulchul-operator-o9k | kiro (README); user (engineering note) | README + setup + model/account requirements; engineering note; AI-use  |

Workers claim with: `busctl claim <Bus ID> --agent <id> --paths "<owned globs>"`.
