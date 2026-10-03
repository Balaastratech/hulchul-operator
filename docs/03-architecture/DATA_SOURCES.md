# Data sources — Google Drive as the mutable source of truth (D-009)

Nothing candidate-specific is hardcoded. Changing a Drive file changes the next run. This is the "adaptability without code change" proof (R2) and the seed of the future answer-library UI.

## 1. Files (synthetic persona; names are contracts)
| Drive object | Format | Purpose | Schema owner |
|---|---|---|---|
| `profile` | Google Doc or Sheet → exported JSON/CSV/markdown with front-matter | identity, contact, links, education[], experience[], skills[], work_authorization, notice_period, location prefs | `data/schema.py: Profile` |
| `rules` | Google Doc (markdown) | application rules in plain English + a small YAML block of hard constraints (`min_salary`, `remote_only`, `blocked_companies[]`, `target_roles[]`, `max_applications_per_run`, `eeo_policy`) | `Rules` |
| `answers` | Google Sheet: `pattern, answer, sensitivity, source, updated` | answer library: matched by the planner before it asks the model/user | `AnswerLibrary` |
| `resume` | PDF | uploaded to forms | file hash recorded |
| `job_queue` | Google Sheet: `url, company, title, added_by` | candidate postings (see OQ-01) | `JobQueue` |
| `application_log` | local SQLite now; Sheet write-back = OQ-02 / future | dedupe + history | ledger |

Plain-English prose in `rules` is interpreted by the LLM **into** the typed `Rules` object at `load_data`; hard constraints in the YAML block are authoritative and are enforced by code.

## 2. Access methods (S4 decides)
| Method | Reviewer needs | Write? | Notes |
|---|---|---|---|
| `drive_public` | nothing (folder shared "anyone with link"); fetch via `…/export?format=csv|txt|pdf` | no | Preferred for reviewers; zero credentials; only synthetic data may be shared this way |
| `drive_api` (service account) | key file | yes | Optional; needed only for log write-back |
| `local_folder` | repo's `sample_data/` | n/a | Fallback and for tests/CI; same schemas |
Config: `DATA_SOURCE=drive_public|local_folder`, `DRIVE_FOLDER_ID=…`.

## 3. Refresh and consistency
- Fetch at `load_data`; compute SHA-256 per file and a combined snapshot hash; store files under `runs/<run_id>/data/`.
- A run always uses its snapshot. If Drive changes mid-run the user can say "refresh data" → new snapshot, **already-filled fields whose source changed are flagged for re-fill and re-approval**.
- Validation errors name the file, row and field; the run becomes `BLOCKED(data_invalid)` instead of guessing.

## 4. Demo edits that prove R2 (no code change)
1. Change `rules`: `remote_only: true` → different shortlist.
2. Change `answers`: notice period `30 days` → `immediately` → a different value appears in the review table.
3. Swap `resume` PDF → new file hash recorded and uploaded.
4. Change the goal sentence: "apply to 2, skip fintech".

## 5. Privacy
Persona is fictional (e.g. "Aarav Mehta"), phone/email use reserved test values. Real personal data never goes to Drive folders shared publicly. The real `Job Applications` folder on the user's PC is **not** read by this project.
