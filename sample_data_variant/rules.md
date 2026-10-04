# Application rules (fictional persona: Aarav Mehta)

These rules are plain English. The hard constraints in the YAML block at the bottom are authoritative
and are enforced by code. If prose and YAML disagree, the YAML wins.

## What to apply for
- Backend, platform and Python roles. Senior level is fine.
- Prefer product companies over staffing agencies.
- Remote or hybrid is fine. On-site outside Bengaluru, Pune and Hyderabad is not.

## What to skip
- Staffing or recruiting agencies that place people with other clients.
- Roles paying below the minimum salary.
- Anything that asks for payment or for documents beyond a resume.

## How to answer
- Use the answers library first. If a question is not covered, ask me; do not guess.
- Never fill gender, ethnicity, veteran or disability questions. Choose "Decline to self-identify" only where
  the form offers it, and leave the field empty otherwise.
- Keep cover-letter answers short and factual.

## Hard constraints

```yaml
min_salary: 2000000          # INR per year
remote_only: true
blocked_companies:
  - Vortex Example Staffing
target_roles:
  - Backend Engineer
  - Platform Engineer
  - Python Developer
  - Software Engineer
max_applications_per_run: 3
eeo_policy: decline_to_answer
```
