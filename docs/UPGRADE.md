# ApplyPilot local upgrade

This release moves application execution into one foreground runtime. The local dashboard and CLI are clients of the same Unix socket. The original source backup is under `output/upgrade-backup-20260912-111357/`. No live records have been imported and no employer or mailbox acceptance was performed during development.

## Start

The unified local launcher replaces Streamlit as the normal interface:

```bash
.venv-upgrade/bin/python -m src.pilot.desktop
```

It starts the singleton runtime when needed and opens an authenticated loopback dashboard. See [the local workspace guide](LOCAL_WORKSPACE.md) for the complete merged interface, daily Gmail tracking, Codex integration and migration controls. Browser startup is optional until opening or filling employer pages. Keep using `.venv-upgrade`; the original environment remains preserved.

For application tabs, separately start `.venv-upgrade/bin/python scripts/open_browser.py`. Do not stop an active runtime during uncertain submission. Streamlit remains available only as a compatibility interface.

## Profile and documents

On first launch, use **My Information**. Confirm
contact details, education or an explicit none, work history or an explicit none,
country-specific work rights, sponsorship needs and the screening items marked
required for setup. The categorized blank catalogue also offers optional reusable
answers for availability, mobility, qualifications, references and social
mobility. Leaving optional answers unknown does not block Autofill. Salary,
declarations, signatures, employer-specific relationships, sensitive voluntary
disclosures and exact conditional wording are requested only when encountered.
Register an approved CV. Autofill and LinkedIn Easy Apply remain unavailable
until this setup is complete; Find Jobs and Open Job Links remain available.

Existing users can use **Preview existing records**. Resolve conflicting source sections, review the combined private preview and select the sections you confirm. The import button makes a consistent database backup first and preserves every original source file. Unconfirmed imported values appear as suggestions in relevant questions; they are not automatically promoted to facts. Unreconciled historical applications block only target-specific submission authority; filling to review remains available.

Register approved CV, transcript, cover-letter or writing-sample files. Original files stay in place. Registration records their content hash, type, version and applicability. A changed file requires a new approved registration. Candidate authorship is required for narrative documents when employer AI policy is unknown/prohibited. Multiple equally applicable documents require a choice.

The engine selects hidden file inputs as well as ordinary controls. A selected filename alone is insufficient: acceptance needs a server attachment identifier or an appropriate same-origin attachment URL. Unsupported acceptance widgets remain a visible upload handoff. Same-origin upload/file/attachment endpoints must transmit the approved bytes; unclassified write APIs are parked for review; named provider Save/Continue actions have a separate checked POST boundary.

## Daily use

Choose Find Jobs, LinkedIn Easy Apply, Autofill or Open Job Links. Add 1–10
Autofill targets or select their exact managed-browser tab IDs. Use the real
requisition/cycle as identity; where it is unavailable, supply a reviewed unique
fallback. Do not use a generic role label shared by multiple vacancies.

- CURRENT_PAGE fills only the selected page.
- EXISTING_APPLICATION also uses supported non-final transitions within that application.
- Autofill defaults every target to REVIEW. A target marked SUBMIT needs its own current authority, confirmed eligibility and qualified adapter immediately before transmission.
- LinkedIn Easy Apply attempts at most three times the requested review-ready count, capped at 30, serialises the signed-in account and never clicks Submit.
- Find Jobs performs a bounded crawl of Bright Network, locally registered Workday/AllHires/Apply4Law career roots and supplied public pages/feeds. It follows permitted same-site vacancy links, filters by the requested job area, and persists requirements, opening/closing dates and a CHECK PORTAL checklist without applying.
- Open Job Links opens exactly the requested 1–10 unique links and performs no application action.
- Account signup, password entry, CAPTCHA, passkeys and email verification are completed by the user in the retained browser before Resume.
- Unknown required information parks only that application. Other fields/applications continue. The dashboard collects the questions together.
- After editing a form, use **Resume after my edits**. The runtime re-reads it without asking which fields changed. **Detect my edits** previews reusable additions for one combined confirmation. Changes create new profile versions and invalidate affected submission approval.
- **I submitted it** immediately records SUBMITTED_USER_REPORTED, removes pending work and prevents duplicates. It does not wait for email or pretend a portal receipt was verified.
- PAGE_FILLED is not REVIEW_READY. Missing navigation, unchanged steps, ambiguous controls and exhausted operation limits remain incomplete.

Requested workers are 1–10. The global execution cap is ten; same-account/shared-context mutations serialize. The resource sampler uses an advisory 128 MiB available-memory allowance per admission, capped at ten. Actual effective concurrency may be lower because of resource pressure, account locks or retained forms. The measured synthetic browser overlap is in TEST_REPORT.md, not a performance promise for arbitrary portals.

Parked work releases execution slots. Up to ten contexts remain retained. Close/release a reviewed form explicitly before replacing it when capacity is full. Stop revokes the run; continuing requires new authority. Pause preserves authority until its expiry. In-flight external effects cannot be recalled. After a runtime/browser interruption, use **Refresh tabs for reconnect** and confirm the same application; the runtime never silently opens a replacement draft.

## CLI

```bash
.venv-upgrade/bin/python cli.py runtime status
.venv-upgrade/bin/python cli.py runtime doctor
.venv-upgrade/bin/python cli.py runtime tabs
.venv-upgrade/bin/python cli.py runtime setup-status
.venv-upgrade/bin/python cli.py runtime find-jobs --query "graduate software engineer" --location London --count 10
.venv-upgrade/bin/python cli.py runtime register-source https://tenant.myworkdayjobs.com/en-US/careers --name "Example careers"
.venv-upgrade/bin/python cli.py runtime open-job-links --count 2 https://employer.example/job/1 https://employer.example/job/2
.venv-upgrade/bin/python cli.py runtime linkedin-easy-apply --keywords "graduate software engineer" --location London --count 3 --profile PROFILE_VERSION --document DOCUMENT_ID
.venv-upgrade/bin/python cli.py runtime linkedin-resume LINKEDIN_BATCH_ID
.venv-upgrade/bin/python cli.py runtime run --plan reviewed-plan.json
.venv-upgrade/bin/python cli.py runtime pause RUN_ID
.venv-upgrade/bin/python cli.py runtime resume RUN_ID
.venv-upgrade/bin/python cli.py runtime submitted APPLICATION_ID
.venv-upgrade/bin/python cli.py runtime detect-edits APPLICATION_ID
.venv-upgrade/bin/python cli.py runtime backup /absolute/new-backup.sqlite3
```

Example plan shape (replace the version, URLs, identity and expiry deliberately):

```json
{
  "request_schema": 2,
  "function": "AUTOFILL",
  "profile_version": "VERSION_FROM_RUNTIME_PROFILES",
  "targets": [{"url": "https://employer.example/application", "employer": "Employer", "role": "Graduate", "identity": "reviewed-requisition-cycle", "scope": "EXISTING_APPLICATION", "tab_id": "SELECTED_TAB_ID", "final_action": "REVIEW"}],
  "documents": [],
  "workers": 1,
  "permissions": ["fill", "session"],
  "approval": "Explicit review of this target and scope",
  "expires_at": 0
}
```

An expiry of zero intentionally cannot run. Preview is the optional `preview`
boolean. `final_action` defaults to REVIEW and can differ between targets in the
same batch. SUBMIT also requires the `submit` capability; giving the capability
without a SUBMIT target is rejected. Legacy FILL_ONLY/AUTO_APPLY plans remain
accepted by the compatibility parser but are no longer the public interface.

Legacy executable application scripts accept `--runtime-plan reviewed-plan.json` and forward to this runtime; their old ad-hoc execution paths no longer run. Cover-letter/answer CLI helpers remain separate. Assessment automation is not activated by the new dashboard.

The legacy `runtime discover --plan` command remains accepted. New callers use
`runtime find-jobs`, which records the query, location, permitted sources and
normalized results in SQLite. It is not an unrestricted crawler or an automatic
eligibility certification.

Enable the repository-owned pre-push privacy gate once per checkout:

```bash
.venv-upgrade/bin/python scripts/install_hooks.py
.venv-upgrade/bin/python scripts/privacy_gate.py
```

The gate checks staged/tracked content, filenames, reachable Git blobs and commit
messages for known local profile identifiers, credentials and private document
types. It reports paths and reasons without printing the matched personal value.

## Backup, import and rollback

```bash
.venv-upgrade/bin/python cli.py runtime migration-preview data/profile.local.json application_profile.json --output /private/tmp/applypilot-review.json
.venv-upgrade/bin/python cli.py runtime import --reviewed-preview /private/tmp/applypilot-review.json --backup /absolute/new-before-import.sqlite3 --approve-live-import
```

The review JSON preserves complete source payloads and hashes. Resolve `conflicts` in `merged`; use `confirmed_fields` for explicitly confirmed top-level sections. Historical records need reviewed employer/role/identity/URL mappings in `history`. Set `history_reviewed` only after reconciliation. Old applied/submitted labels import as SUBMISSION_UNCONFIRMED, never as fresh receipts. Unknown/custom content remains in preserved source records.

Stop the foreground runtime before an explicit restore:

```bash
.venv-upgrade/bin/python cli.py runtime restore /absolute/verified-backup.sqlite3 --approve-live-restore
```

Restore checks database integrity, stages a consistent copy, takes a backup of the current database, and preserves the original files under timestamped names. Use that before-restore backup for rollback. Never copy an active `.sqlite3` file by itself.

## At-rest and operational boundaries

The database and Unix socket use owner-only permissions; the runtime directory is 0700. SQLite is not encrypted. OS account security, FileVault and protected backups remain relevant; this release does not enable encryption or move/re-encrypt your existing files. Someone controlling the logged-in account/device can compromise local data and browser sessions.

OAuth refresh tokens and optional AI keys use explicit macOS Security APIs.
Employer passwords are outside ApplyPilot and remain in the portal/browser flow.
Refresh tokens are in Keychain; access tokens are transient. Local disconnect
stops runtime mail access but does not claim provider-side revocation.

UI bindings remain loopback with CORS/XSRF protection. Privileged backend commands are on a 0600 Unix socket. Pages/model output cannot select arbitrary files or issue backend commands. Browser/network traces and screenshots are not produced by application workers. Private field values are saved only in local profile/checkpoint/review records, not telemetry.

## Handover summary

The current release provides four primary functions: Find Jobs, LinkedIn Easy
Apply, Autofill, and Open Job Links. First-time users confirm reusable profile
facts and an approved CV locally. Find Jobs and Open Job Links remain available
without this setup. Autofill stops at final review by default, while each target
can receive separate submission authority. LinkedIn Easy Apply always stops at
review. Missing facts are grouped, and manually edited forms resume without
requiring the user to identify every changed field. Selecting “I submitted it”
immediately ends active work for that application.

Existing private records, documents, and environments remain local. A first
import requires preview, conflict resolution, confirmation, and a database
backup. Real mailbox connections, Keychain access, and employer submissions
still require their documented setup and authority. Synthetic acceptance does
not establish compatibility with every live recruitment site.

Detailed partial implementations and remaining acceptance work are listed in [ACCEPTANCE_LIMITS.md](ACCEPTANCE_LIMITS.md).

## Follow-up completion

See [portal contracts](PORTAL_CONTRACTS.md) for implemented AllHires/Apply4Law actions, record reconciliation and submission evidence. Gmail includes asynchronous connection status and explicit provider revocation. Autofill to final review does not require a Gmail connection.

Application-specific facts are requested together only when a form requires
them. Git stores blank question definitions and never candidate answers. Local
tests cover Save and Continue actions and repeated work records; each live site
still requires compatibility verification. Gmail support is implemented, but
the user must complete Google consent for the first connection. Ordinary
autofill does not require Gmail, and private records must never be pushed to
GitHub.
