# Future scope — what a production version adds (NOT built in the 12-hour window)

Purpose: show the employer where this goes when time is no constraint, and that the 12-hour version was designed so none of it requires a rewrite (ports/adapters in ARCHITECTURE).

## A. Ideas observed in AIApply Auto Apply
Source: the user's supplied research notes summarising AIApply's public help-center/terms pages (originally gathered via ChatGPT). **I have not independently verified these claims**; implementation details of AIApply are not public. Treat as inspiration, not fact.
| Idea | What it would add for us | Where it plugs in |
|---|---|---|
| Dedicated per-user application inbox | Employer emails, confirmations, OTP codes arrive at a mailbox the system controls | `InboxPort`; new graph node `await_email` |
| OTP / email-verification handling | Create employer accounts and complete verification without the user | `human_handoff` replaced by `verify_email` for the safe cases; CAPTCHA remains human |
| Employer account creation + credential vault | Account chains: create → verify → upload → submit; credentials saved to the application record | new T3 action `create_account`, encrypted vault |
| Answer Library as a first-class store | Structured questions→answers with sensitivity and review; LLM only for novel free text | already prototyped via Drive sheet; move to DB + editing UI |
| ATS system classifier + adapters | `URL → adapter` with `SUPPORTED/NOT_SUPPORTED` before spending effort | `classify_page` + adapter registry |
| Success verifier that gates "applied" | Count an application only after confirmation evidence (page + email) | extends `verify_submission` with email evidence |
| Email state tracking | Auto-label: confirmation, assessment invite, interview, rejection, OTP | inbox classifier + pipeline view |
| Spacing/queueing of submissions | Distribute activity, caps, jitter to protect platform health | rate governor in policy |
| Hybrid mode (auto-submit above a score) | Optional auto-submit for high-confidence matches under explicit user-set authority | policy tier T3 with delegated authority — **off by default** |
| Assessments are a human boundary | Hand credentials + the assessment email to the user | `human_handoff` variant |

## B. Platform upgrades
| Area | Upgrade |
|---|---|
| Deployment | Fully remote browser workers (VM + virtual display + remote view for CAPTCHA/login), multi-tenant control plane, queue + autoscaling |
| Channels | WhatsApp (Raj's native channel), email digests, Slack |
| Data | Postgres, answer-library editor UI, profile versioning, per-job tailored resume + cover letter generation with diff review |
| Reliability | Compile-and-replay "skills" per ATS with drift detection and one-step self-repair; selector-free accessibility-tree strategy; visual fallback |
| Safety | Policy-as-code editor, signed audit log, anomaly detection on agent behaviour, red-team suite from OS-Harm/OSGuard style cases |
| Evaluation | Continuous eval on a corpus of real ATS pages, regression dashboards, ERPBench-style state-grounded scoring |
| Product | Job discovery + fit scoring at scale, warm-intro workflows, referral tracking (Raj alignment), analytics |
| Compliance | Consent records, data retention, DPDP/GDPR handling, per-site ToS registry |

## C. What we deliberately do NOT plan
CAPTCHA solving or bypassing; autonomous submission without user-set authority; scraping platforms that forbid it.
