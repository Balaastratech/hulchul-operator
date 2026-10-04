# Demo video script (target 4:40, hard limit 5:00)

Format: screen recording (1920x1080, one monitor) + on-screen captions + voiceover. Segments are recorded separately and cut together. Every claim in the narration maps to a measured fact (see SPIKE_REPORT.md, REDTEAM_REPORT.md, AUDIT_SUMMARY.md, evidence/phone-proof-2026-10-04.json).

Layout for the real-UI segments: left half = Telegram Web (user's Chrome, logged in), right half = the operator's Chrome window; a narrow terminal strip on the bottom shows the timeline log.

## 0:00-0:25 · Intro (title card + architecture slide)
Caption: "Job-Apply Operator: model proposes, code decides"
Narration: "This is a computer operator for job applications. You give it a goal in plain English. It reads your rules and answers from Google Drive, fills real application forms it has never seen, and then stops and asks me before anything is submitted. The model proposes. Deterministic code decides what is allowed, whether something already happened, and whether the result is really complete."

## 0:25-2:10 · Segment A: the task, with real approval (real UI)
Command: `python deploy/real_phone_proof.py --state-dir runs/demo-a --data-source drive_public`
Screens, in order, with captions:
1. Terminal: "Goal: Apply to the single best-fit role under my rules" - caption "Data loaded from Google Drive: profile, rules, answers, resume (fictional persona)".
2. Telegram: "Run started", then "3 postings blocked - hidden instructions, not used". Caption "Hostile job posts with hidden instructions are quarantined before the model sees them".
3. Operator Chrome: form filled live, step 1 then Next then step 2. Caption "Real browser, a form it has never seen. The model picks values; code checks every one by reading it back".
4. Telegram: "Ready for your review" message. Caption "Plain-language summary: filled / needs you / left blank by your rules".
5. Tap "Review & approve": review page. Show read-back table, "derived from: <source>" on the start date, screenshot of the filled form, "Left for you" on the consent box. Caption "Every value shows where it came from".
6. Press "Approve and submit (fixture)". Caption "Approval is a signed, single-use token bound to this exact form".
7. Terminal: "Submitted and verified", fixture counter = 1. Press Approve a second time on the old page: refused (409). Caption "Replay refused. Exactly one submission".
Narration: "I give it one sentence. It pulls my rules from Drive. Three job posts contain hidden instructions, for example 'ignore previous rules', and they are blocked before the model reads them. It opens the application in a real browser, fills the form, and reads every field back. Then Telegram tells me, in plain words, what is ready. The link opens a review page: every value, where it came from, and a screenshot. Nothing has been sent yet. I press Approve. The approval is a one-time signed token tied to exactly this form. The submission goes through, it is verified, and pressing Approve again is refused. Real job sites are fill-only by design; submissions only go to our test site."

## 2:10-3:05 · Segment B: the variation, no code change
Commands (same terminal):
`python scripts/plan_only.py --goal "Apply to the 3 best-fit roles under my rules" --data-source drive_public`
then edit the Drive document `rules` in the browser (for example set `remote_only: true` or add a company to `blocked_companies`) and run the same command again.
Caption: "Same code, same goal. Only the Drive file changed". Show the shortlist before and after side by side.
Narration: "Now the variation. I change one line in my rules document in Google Drive, and run the same command again. The shortlist changes, and each choice has a reason. No code was touched."

## 3:05-4:10 · Segment C: failure and recovery
Commands: `python scripts/demo_g3.py --crash before_claim` then `python scripts/demo_g3.py --crash after_claim` (headless off).
Captions: "Worker killed after approval, before the click" -> "Restarted: nothing refilled, exactly one submission"; second: "Killed right after recording intent" -> "Zero submissions, status UNVERIFIED, never a second click".
Then 15 s: e2e CAPTCHA/login fixtures: the operator stops, says "outside my authority", waits for the human, resumes without refilling. Caption "It detects CAPTCHAs and logins. It never tries to solve them".
Narration: "Failures. I kill the worker right after I approve. On restart it does not refill the form and it does not submit twice. If it dies at the worst moment, after recording intent but before clicking, the safe result is no submission and an honest 'unverified' status, never a duplicate. And when it meets a login wall or a CAPTCHA, it stops and hands over to me. It never solves them."

## 4:10-4:50 · Limits and next
Slide with three columns: "Measured", "Not claimed", "Next".
Measured: independent audit found 28 issues incl. 2 critical, all critical/high fixed and re-tested; 50 crash points x 40 route-loss points kept the safety invariants; real-page rehearsal numbers (REAL_REHEARSAL.md).
Not claimed: submitting to real employers; solving CAPTCHAs; every ATS; WhatsApp (adapter stubbed).
Next: dedicated inbox and OTP handling, account creation, per-ATS adapters, answer-library editor, remote browser (docs/06-roadmap/FUTURE_SCOPE.md).
Narration: "What I measured: an independent audit found 28 issues, including two critical ones, and I fixed and retested them. What I do not claim: real submissions, CAPTCHA solving, or coverage of every job site. Real pages are filled, never submitted. Next would be an inbox for verification codes, account creation, per-site adapters and a remote browser."

## Recording and post-production plan
- Record segments with ffmpeg gdigrab on the monitor holding the windows; narration generated from the text above (or re-recorded by the user over the cut).
- Burn captions (ASS subtitles), add title/limits slides, concatenate, export H.264 MP4 under 100 MB, check duration <= 5:00 and sample frames.
- Upload to YouTube as UNLISTED (user), put the link in the Internshala reply.
