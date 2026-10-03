# Spike report (append-only; each section dated; measured facts only)

## 2026-10-03 — Claude — environment
| Fact | Value |
|---|---|
| OS / runtimes | Windows 11; Python 3.13.15; Node 24.19.0 |
| Playwright (Python) | 1.63.0; bundled Chromium **not installed** → use `channel="chrome"` |
| System Chrome | `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe` |
| LangGraph | 1.2.12; `langgraph-checkpoint-sqlite` 3.1.1 (installed in `research/spikes` venv `lg`) |
| Model access | Vertex project `ai-negotiation-copilot`, location `global`. `gemini-2.5-flash` 200; `gemini-3-flash-preview` 200 (but slow, see S1); `gemini-3.1-pro-preview` 200; `gemini-3-flash` 404. **No Anthropic API key** on this machine |
| Other keys present in growth-system `.env` | OpenRouter, Telegram bot token, Composio, Browserless, etc. (names only; never copy values into this repo) |
| Tools on PATH | gcloud, cloudflared (Program Files x86), Tailscale, Docker Desktop |

## S1 — Fill UNSEEN real ATS forms with LLM + Playwright (no submit) — DONE
Code: `research/spikes/spike.py`, results `research/spikes/out/results.json`. Synthetic persona; Gemini plans; Playwright executes; read-back verifies.
| Site | Fields seen | Filled+verified | Failed/missed | Escalated (`ask_user`) | Time | Tokens |
|---|---|---|---|---|---|---|
| Greenhouse (Vercel posting) | 38 (2 file inputs) | 11 of 12 attempted | 2 custom yes/no widgets not clickable by my extractor; resume "Attach" verify returned `?` | 10 incl. all EEO, acknowledgements, "how did you hear" | 33.8 s | 4.7k |
| Lever (Palantir posting) | 59 (1 file) | 10 of 10 attempted | none | 8 (languages, university, EEO-like, consent) | 20.7 s | 7.0k |
| Ashby (Ashby posting) | 38 (2 file) | 5 verified of 7 planned (model: gemini-2.5-flash) | custom yes/no **buttons** not extracted; hidden checkbox "not visible"; 1 false-negative verify ("Ahmedabad, India" vs "Ahmedabad, Gujarat, India") | 7 (essays were wrongly escalated; EEO escalated correctly) | 17.0 s | 5.6k |
Findings: (1) all three boards load **without login**; (2) all three embed CAPTCHA (reCAPTCHA Enterprise / hCaptcha) that fires at Submit, not during filling; (3) `gemini-3-flash-preview` **timed out 3×** on Ashby (120 s) → default `gemini-2.5-flash`; (4) sensitive questions were correctly escalated instead of guessed; (5) extractor gaps are concrete: button-based yes/no, hidden/custom checkboxes, combobox display value; (6) verifier needs semantic comparison; (7) essay prompt too conservative.

## S2 — Pause in state, crash, reattach, edit one field, reload, replay — DONE
Code: `research/spikes/resume_test.py`. Lever form, Chrome launched separately with `--remote-debugging-port=9333`.
- Operator process exited after filling 15 actions (1 LLM call). New process reattached over CDP: **10/11 text fields still filled**, URL unchanged, **0 refills**.
- Edited the email by stable key `label|type|group|n` with **no LLM call** → value changed correctly.
- Hard reload, then replay of the recorded action log: **15/15 actions replayed, 0 missed, 0 LLM calls, 1.2 s**. Text-value verification matched only 7/11 under naive string compare → verifier must be fuzzy (S10); not a replay failure per se, but **unproven** which of the 4 differed and why.
- Caveat: the read of "fields filled after reload = 45" in the script output counted every non-empty value (radio "false" strings) and is **meaningless**; ignore.

## S3 — LangGraph interrupt + SqliteSaver across process exit — DONE
Code: `research/spikes/lg_test.py`. Graph `fill → gate(interrupt) → submit`. Process A paused with payload containing an approve URL, exited. Process B: `get_state().next == ('gate',)`, `Command(resume="APPROVED")` → `submit` ran once; `fill` did **not** re-run. LangGraph 1.2.12 + sqlite checkpointer 3.1.1 behave as needed.

## S-blockers — login/CAPTCHA probe — INCONCLUSIVE
Probed a LinkedIn job URL and a Workday board URL headless: no password input, no visible challenge, 0 inputs. The test pages did not exhibit the walls, so **no evidence** about detection. Do not claim login/CAPTCHA detection works until S7.

## Not yet measured (see SPIKE_BACKLOG)
Drive access (S4), Telegram + prefetch (S5), tunnel/deploy (S6), page-state classifier (S7), injection catch rate (S8), extractor v2 benchmark (S9), fuzzy verifier (S10), multi-step form (S11), submit-once on fixtures (S12), Gemini API-key parity (S13), cost/latency budget (S14), WhatsApp feasibility (S15), graph+CDP+ledger together (S16).

## 2026-10-03 — Claude (manager) — S5 human confirmation of delivery
User screenshot (Telegram Web, chat with @operator_hul_bot, 13:51-14:08 IST) shows all four spike messages delivered and rendered: "SPIKE S5" (link, preview on), "SPIKE S5 M2" (preview disabled), "SPIKE S5 M3" (URL button only, button "Open review (SPIKE)"), and "SPIKE S6 - harmless test, nothing is submitted" (phone test). This closes Kiro's "received on phone: PENDING". Combined with Kiro's server log: with preview on Telegram fetched the link once about 2 s later (GET, UA TelegramBot); with preview disabled or a URL button there were 0 fetches; the server state counter never moved. Observation for UX: view links carry a long signed `t=` token and the tunnel host is a temporary trycloudflare URL; a deployed host (D-020) would give a stable short link.

