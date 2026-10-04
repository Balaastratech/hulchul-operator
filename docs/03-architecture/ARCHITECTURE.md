# Architecture

## 1. Shape: control plane + worker (D-006)
```
 YOU (phone / laptop)                              GOOGLE DRIVE (synthetic, mutable)
   │  Telegram msg + link                            profile · rules · answers · resume · job_queue
   ▼                                                        ▲ read at run start (snapshot+hash)
┌───────────────────────── CONTROL PLANE (deployable, FastAPI) ─────────────────────────┐
│ Channel port ─ Telegram | Web | (WhatsApp stub)                                          │
│ Review page (/r/{run}?t=TOKEN)  ·  POST /approve  /edit  /pause  /resume  /answer       │
│ Token service (HMAC, single-use, expiring, bound to snapshot hash)                       │
│ Event log + run store (SQLite)  ·  SSE /events for live progress                         │
└───────────────▲──────────────────────────────────────────────┬───────────────────────────┘
        worker→CP │ outbound HTTPS only (events, snapshots)       │ commands (poll): approve/edit/pause
                  │                                               ▼
┌──────────────── WORKER (runs on YOUR PC, next to the browser) ─────────────────────────────────┐
│ LangGraph graph + SqliteSaver checkpoints  ·  Policy engine  ·  Idempotency ledger (SQLite)   │
│ LLM port: vertex | gemini_api      Data port: drive | local                                    │
│ Browser port: Playwright over CDP ──────────────► persistent headed Chrome (separate process)  │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```
Why this shape: the half-filled form lives in a real Chrome profile the **human can see** (CAPTCHA/login handoff), the worker can crash and reattach (S2), and the control plane can be deployed and secured independently (reusable for the user's own system). The worker only makes **outbound** calls, so no inbound port is opened on the PC.

## 2. Ports and adapters (so nothing is rebuilt later)
| Port | v1 adapter(s) | Later |
|---|---|---|
| `LLM` | `vertex` (gemini-2.5-flash default), `gemini_api` | Anthropic, OpenRouter |
| `DataSource` | `drive_public` (export URLs), `local_folder` | `drive_api` service account, Postgres |
| `Channel` | `telegram`, `web` | `whatsapp` (S15), email |
| `Browser` | `cdp_chrome` | remote VM + noVNC |
| `Ledger` | `sqlite` | Postgres |
| `Clock/IDs` | system | injectable for tests |

## 3. Repository layout (target; created by the build, not by the docs)
```
src/operator/
  contracts/   pydantic models: Goal, Profile, Rules, JobPosting, FieldSpec, FillAction, ReviewSnapshot, RunState, Event, Command   [B2/B3]
  graph/       build.py (graph), nodes/*.py (one file per node), subgraph_application.py                                           [B3 topology, B1 nodes]
  browser/     cdp.py, extract.py, execute.py, classify.py, verify.py, evidence.py                                                  [B1]
  policy/      tiers.py, allowlist.py, injection.py, authority.py                                                                   [B3 rules, B1 impl]
  data/        base.py, drive_public.py, local_folder.py, schema.py, snapshot.py                                                    [B1]
  llm/         base.py, vertex.py, gemini_api.py, prompts/*.md, judge.py                                                            [B1]
  channels/    base.py, telegram.py, web.py, whatsapp_stub.py                                                                       [B1]
  ledger/      sqlite.py, idempotency.py                                                                                            [B3 schema]
control_plane/ app.py, tokens.py, routes/, templates/, static/                                                                       [B1]
worker/        main.py (poll commands, run graph, reattach browser)                                                                  [B1]
fixtures/      ats_a/ ats_b/ (two form layouts), job_board_hostile/, login_wall/, captcha_stub/, confirm_page/                      [B0]
evals/         goals.yaml, expected.yaml, run_evals.py                                                                               [B0]
tests/         unit/, integration/, e2e/                                                                                             [B0]
docs/
```
Contracts are the shared typed interface between the worker, adapters and review service. Changes require checking every consumer.

## 4. State and persistence
| Store | Holds | Notes |
|---|---|---|
| LangGraph checkpoints (SQLite) | graph state per `thread_id = run_id` | resume point; no browser objects inside |
| Ledger (SQLite) | applications, actions (idempotency keys), approvals (token hash, snapshot hash, used_at), events | the source for "did this already happen?" |
| Browser profile dir | cookies, the open page | reattach via CDP endpoint + target id saved in state |
| Evidence dir | screenshots, DOM snapshots, read-back diffs per step | linked from review page + report |
| Drive snapshot | the data used for this run, with SHA-256 | makes runs reproducible even if Drive later changes |

## 5. Resume model (the hard part, tested pieces)
1. Worker starts; if `RunState.cdp_endpoint` is set, reattach (S2) — never relaunch if Chrome is alive.
2. Graph resumes from the last checkpoint (S3).
3. Each side-effect node first consults the ledger by idempotency key; `SUCCESS` → skip; `SUBMITTING` → never re-click, verify only.
4. After a hard reload, replay recorded `FillAction`s keyed by field key with **zero LLM calls** (S2: 15/15 in 1.2 s).
5. Combined end-to-end behaviour (graph + CDP + ledger) is spike S16 and gate G2.

## 6. Security (control plane)
HMAC-signed capability tokens (≥128-bit, per run+action+snapshot hash, expiry ≤30 min, single use) · Telegram `chat_id` allowlist · CSRF-safe POST with token in body · rate limits · HTTPS only when deployed (tunnel/VM) · no secrets in logs · screenshots may contain personal data → synthetic only, auto-expire.
