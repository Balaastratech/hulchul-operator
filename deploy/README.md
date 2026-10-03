# Control plane deployment (T-024 / T-033)

The control plane runs remotely; Chrome and the worker stay on the user's PC and
poll outbound. Approval only queues a command for the reviewed snapshot. Real
employer submissions remain prohibited; this rehearsal submits only ATS A on loopback.

## Build and run

From the repository root with Docker Desktop's Linux engine running:

```powershell
python deploy/build_image.py
python deploy/smoke_image.py
docker run -d --name hulchul-cp --restart unless-stopped -p 127.0.0.1:8790:8790 --env-file C:\Balaastra\hulchul-operator\.env -e CP_ENV=prod -e CP_BASE_URL=https://YOUR-HOST -v hulchul-cp-data:/data hulchul-control-plane:t033
docker exec hulchul-cp python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8790/healthz').read().decode())"
docker inspect --format '{{.State.Health.Status}}' hulchul-cp
```

The build helper stages an allowlist of runtime files under `deploy/scratch/` and
removes it after building. It excludes real environment files, credentials,
databases, browser profiles, evidence and tests from the build context. The image
contains only the committed empty `.env.example`, used to reject template secrets.
Runtime UID/GID is 10001; `/data` is writable and should persist in a named volume.
No browser or browser worker is included. Dependencies are pinned in
`requirements-control-plane.txt`. `/healthz` is a read-only process liveness probe.

Required runtime variables: `CP_SIGNING_KEY` and `CP_WORKER_TOKEN` (distinct, random,
at least 32 UTF-8 bytes each), `CP_BASE_URL` (the exact public HTTPS origin).
Optional: `CP_SIGNING_KEY_PREVIOUS` for rotation overlap, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`. `CP_DB_PATH` defaults to `/data/control_plane.sqlite` in the image.
Supply variables through `--env-file` or `-e NAME` from the shell environment; never
put secrets on command lines. Alternatively mount a private file read-only at
`/run/secrets/cp.env` (the image's `ENV_FILE`). Never commit a real env file.
Access logs are disabled because review queries contain capability tokens.

## Fresh quick tunnel and phone proof

Start the tunnel in a separate terminal before the proof; keep it alive:

```powershell
cloudflared tunnel --url http://127.0.0.1:8790 --no-autoupdate
```

Copy its generated HTTPS URL into this second terminal (do not reuse an old URL):

```powershell
$env:ENV_FILE='C:\Balaastra\hulchul-operator\.env'
$env:CP_BASE_URL='https://GENERATED.trycloudflare.com'
$env:TEMP=(Join-Path $PWD 'deploy\scratch')
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
python deploy/phone_proof.py --auto
```

Keep port 8790 free: the proof starts its own control plane, fixture and headed
Chrome. Stop the Docker container first if it owns that port. The helper runs the
existing `scripts/demo_g3.py` main and Scenario, replacing only its scripted
exercise with a 600-second wait for the user's actual approval. It reads Telegram
credentials through ENV_FILE; demo CP credentials are fresh ephemeral secrets.
The real message uses a URL button with previews disabled. Open Telegram **on the
phone**, tap Review fixture, then Approve. The helper verifies exactly one
approval command, exactly one fixture submission and submit click, verified
completion after two fresh workers, and a 409/token_replayed refusal for the exact
accepted phone token. No scripted approval substitutes for the user.

`deploy/phone-proof-result.json` holds UTC timings and redacted request metadata;
queries, request bodies, headers, tokens and chat identifiers are omitted. The
approval body exists only in memory until replay verification. Timeout leaves the
fixture unsubmitted and closes the rehearsal. `python scripts/demo_g3.py --auto
--no-telegram` separately runs the original scripted G3 edit/crash regression;
that command alone does not prove phone approval.

Quick tunnels are temporary and [do not support SSE](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
Phone review and POST approval work through them; use a named tunnel or HTTPS
reverse proxy for the production live event stream.

## Oracle VM next step (documented, not deployed)

Provision a Linux VM only after user authorization. Build the image for the VM's
architecture on that VM (this avoids assuming ARM/x86 compatibility), using the
same allowlisted build helper. Install Docker through its official instructions.
Create the private runtime env file on the VM outside the checkout with mode 600;
generate new CP secrets there, and set the public HTTPS origin. Do not transfer
the PC's full `.env`, which also contains unrelated credentials.

Run the container with a persistent `/data` volume and a restart policy as above.
Bind port 8790 to loopback. Put a named Cloudflare tunnel or a TLS reverse proxy
in front; for a named tunnel only outbound connectivity is needed. If using a
reverse proxy, allow 443 in both the OCI security list/NSG and guest firewall,
restrict SSH to the administrator, and keep 8790 private. Set `CP_BASE_URL` to the
final HTTPS origin and configure the local worker's authenticated outbound
commands/ack/heartbeat URLs to that origin. The browser/CDP stays on the PC.

Before switching traffic, verify `/healthz`, non-root UID, volume write access,
unauthenticated worker API refusal, read-only review GET, signed POST approval,
replay refusal, worker reconnect and exactly one fixture submission. Back up the
SQLite data with SQLite's backup API, including consistent WAL handling; record
key rotation/revocation and restore procedures. No VM, DNS, firewall or third-party
resources are changed by this task.

Reference: [Dockerfile USER, HEALTHCHECK and VOLUME](https://docs.docker.com/reference/dockerfile/).
