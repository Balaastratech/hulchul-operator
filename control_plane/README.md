# Hulchul control plane

Worker API, review pages, POST-only actions, Telegram channel and live progress (SSE).
The contract is `docs/03-architecture/CONTROL_PLANE_API.md` (frozen). The worker only makes
outbound calls to this service; nothing here ever connects to the worker.

## Run

```powershell
python -m control_plane                      # 127.0.0.1:8790
python -m control_plane --port 8791          # other port
python -m control_plane --host 0.0.0.0       # explicit, warns: see Security
uvicorn control_plane.app:create_app --factory --no-access-log
```

`python -m control_plane` loads the dotenv file at `ENV_FILE` (default
`C:\Balaastra\hulchul-operator\.env`) and refuses to start (exit status 2, variable name
only, never a value) when `CP_SIGNING_KEY` is missing or shorter than 32 bytes, or when
`CP_WORKER_TOKEN` is unset. Nothing else reads the dotenv file. Always keep the access log
off: request lines carry `?t=<view token>`.

## Ports

| Port | Used by |
|---|---|
| 8790 | this control plane (default) |
| 8765 | Bala Agent Mail on this machine. Do not use. |
| 8780 | fixture job-board server |
| 8781 | reserved. Do not use. |

## Configuration (variable names only)

Loaded from the process environment over the dotenv file named by `ENV_FILE`
(`--env-file` overrides it). Real environment variables win over the file.

| Name | Needed | Purpose |
|---|---|---|
| `CP_SIGNING_KEY` | yes | HMAC key for human capability tokens (>= 32 bytes) |
| `CP_SIGNING_KEY_PREVIOUS` | no | verify-only key during rotation |
| `CP_WORKER_TOKEN` | yes (prod) | bearer secret for `/api/worker/*` (>= 32 bytes, differs from the signing key) |
| `CP_BASE_URL` (or `CP_PUBLIC_URL`) | yes (prod) | public HTTPS origin used to build links and for the Origin check |
| `CP_DB_PATH` | no | SQLite file (default `control_plane/.state/control_plane.sqlite`) |
| `CP_ENV` | no | `prod` (default) or `dev` |
| `CP_DEV_ALLOW_UNAUTH_WORKER` | no | `1` only with `CP_ENV=dev` and a loopback `CP_BASE_URL` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | no | Telegram channel and chat allowlist |
| `ENV_FILE` | no | path of the dotenv file |

## Security

These are network-exposed endpoints. Auth exists, but there is no user login system.

- Worker routes: `Authorization: Bearer <CP_WORKER_TOKEN>` on every request, whatever the
  peer address (a tunnel connects from loopback).
- Human routes: HMAC capability tokens. `view` on GET pages (`?t=`), `act`/`run` in the POST
  body (single use, bound to the snapshot or gate hash), `view` in the `Authorization`
  header for `GET /events/{run}`. GET never changes state.
- Telegram: only chats in `TELEGRAM_CHAT_ID` are accepted, and only to answer an open question.
- HTTPS is required when deployed (`CP_ENV=prod` rejects a non-HTTPS `CP_BASE_URL`). Binding
  a non-loopback `--host` without a TLS proxy in front exposes tokens in clear text.
- Event streams: at most 5 per run; a stream ends with `event: bye` when its token expires or
  the server stops (graceful shutdown waits at most 5 s).

## Live progress

```powershell
curl.exe -N -H "Authorization: Bearer <view token>" http://127.0.0.1:8790/events/<run>
curl.exe -N -H "Authorization: Bearer <view token>" -H "Last-Event-ID: 42" http://127.0.0.1:8790/events/<run>
```

Native `EventSource` cannot set headers and is not supported; a page uses `fetch()` and
reads the streamed body. The bundled HTML pages do not include that client script yet.

## Tests

```powershell
python -m pytest tests/fixtures tests/control_plane tests/channels -q -p no:cacheprovider
```

Temp SQLite files and generated keys only; no network and no real `.env`.

## Expose with a cloudflared quick tunnel

```powershell
cloudflared tunnel --url http://127.0.0.1:8790
```

It prints an `https://<random>.trycloudflare.com` URL. Set `CP_BASE_URL` (or `CP_PUBLIC_URL`)
to that origin and start the control plane; every link is built from it. The URL changes on every tunnel restart, so links already sent to
Telegram point at the old origin; restart the control plane with the new `CP_BASE_URL` and
request fresh links.
