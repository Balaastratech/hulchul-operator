# Company context (pointer)

Full research: `private/HULCHUL_COMPANY_RESEARCH_AND_ASSIGNMENT_ALIGNMENT.md` (local-only, git-ignored because it holds compensation/risk commentary; dated 3 Oct 2026; company-reported numbers are not audited).

## Takeaways that shape the build
- Hulchul / **Raj** = WhatsApp-first AI career agent: scouts jobs, matches, sends 3–5/day, makes warm intros (cap: 1 per hiring manager per day), tracks follow-up. Philosophy: **signal over volume, no spam, track state after an action.**
- Listing language to mirror: applied AI workflows, evals, **deterministic safety checks**, tests, production-issue ownership, "understand and explain every change", curiosity beyond calling a model API.
- Stack signal: React/Next, TypeScript/Node, Python familiarity. We chose Python+LangGraph (D-001) because it is tested here; the engineering note must explain this trade-off honestly.
- Compensation is ₹15k fixed + variable incentive; treat the offer as startup-risk. Not relevant to the build; relevant to the user's reply.

## One sentence to defend in the technical conversation
> The model decides what makes sense; deterministic code decides what is allowed, whether an action already happened, and whether the result is actually complete.
