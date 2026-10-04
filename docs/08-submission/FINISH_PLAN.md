# FINISH PLAN (written 2026-10-04 ~17:50 IST; deadline 2026-10-04 23:59 IST) - any agent can execute this

State: main is green (714 passed offline), phone proof passed, README/requirements/doctor merged. Open: T-046 real-page rehearsal (codex-e, in progress), video, final checks, reply.

## 1. Merge what is left (manager asks the user before each merge)
- agent/codex-e/T-046-real-rehearsal-run: when "branch ready", dry-merge on origin/main, run `python -m pytest -q -p no:cacheprovider` in a venv built by `pip install .`, ask the user, merge, push.
- Do NOT merge Kiro's agent/kiro/T-037* branches (stale tests).

## 2. Video (target 4:40, hard limit 5:00)  -- files in C:\Balaastra\video-work (NOT in the repo)
- Script and captions: docs/08-submission/VIDEO_SCRIPT.md. Narration already generated (Gemini 3.1 Flash TTS, voice Puck): video-work/audio/*.wav, durations in audio/meta.json (total 166 s); regenerate with `python gen_audio.py <ids>` (edit BLOCKS in gen_audio.py).
- Record the PRIMARY monitor: in the 3840x1080 desktop capture it is the RIGHT half: `ffmpeg -f gdigrab -framerate 30 -offset_x 1920 -offset_y 0 -video_size 1920x1080 -i desktop -c:v libx264 -preset ultrafast -crf 20 -pix_fmt yuv420p clips\<name>.mkv` (use .mkv so a killed ffmpeg still leaves a playable file). Telegram Web = Chrome profile "Bala": `chrome.exe --profile-directory="Profile 1" --new-window https://web.telegram.org/k/#@operator_hul_bot`.
- Segment A (real approval): `python deploy/real_phone_proof.py --state-dir runs/demo-a --data-source drive_public` (USER presses "Review & approve" then "Approve and submit (fixture)"). Segment B: `python scripts/plan_only.py --goal "Apply to the 3 best-fit roles under my rules" --data-source drive_public`, edit Drive doc `rules` on screen (USER), run again. Segment C: `python scripts/demo_g3.py --crash before_claim` and `--crash after_claim`; CAPTCHA/login: `python -m pytest tests/e2e -k "captcha or login" -s`.
- Post-production: assemble with ffmpeg (trim/speed, burned captions, slides, narration mixed, concat) to 1920x1080 30fps H.264/AAC mp4 under 100 MB, total <= 4:55; verify duration with ffprobe and look at 6-8 sample frames. USER uploads to YouTube as UNLISTED.

## 3. Public repo (owner decided: public after a final secret scan)
- Scan: `git ls-files` must not include .env*, private/, runs/, *.sqlite, egg-info; run regex scan over `git ls-files` and `git log -p --all` for AIza..., gho_..., ghp_..., sk-..., bot-token pattern `\d{8,10}:[A-Za-z0-9_-]{30,}`, `BEGIN PRIVATE KEY`; confirm company-research file and brief PDF are only under private/ (ignored).
- Then: `gh repo edit Balaastratech/hulchul-operator --visibility public --accept-visibility-change-consequences`. No LICENSE unless the owner asks.

## 4. Owner-only items
- README "AI assistance and my contribution": replace the TODO sentences with the owner's own words.
- Upload the video (YouTube unlisted) and paste both links.
- Send the Internshala reply (draft below). Send nothing before the owner has read it.

## 5. Reply draft (Internshala chat)
Hi Hulchul team, thank you for the assignment. Submission:
- Repository: <REPO LINK> (README: run it in 3 minutes with no keys; real run needs a Gemini key or Vertex).
- Demo video (4:xx): <VIDEO LINK>
- Engineering note: docs/08-submission/ENGINEERING_NOTE_DRAFT.md (in the repo), AI tools used and my own contribution are disclosed in the README.
- Start date: I can start from 4 October 2026.
- Availability: confirmed, at least 30 focused hours per week for the six-month internship; no timing adjustment needed.
Known limits are stated honestly in the README (real job sites are fill-only; no CAPTCHA solving).
Thanks, Yuvraj Kodvara
