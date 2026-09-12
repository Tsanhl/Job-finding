# ApplyPilot local upgrade

This release moves application execution into one foreground runtime. The Streamlit UI and CLI are clients of the same Unix socket. The original source backup is under `output/upgrade-backup-20260912-111357/`. No live records have been imported and no employer or mailbox acceptance was performed during development.

## Start

The upgrade environment is `.venv-upgrade`, isolated from `.venv` and system packages. On this Mac:

```bash
.venv-upgrade/bin/python scripts/open_browser.py
```

Leave that managed browser owner running. In another terminal:

```bash
.venv-upgrade/bin/python cli.py runtime start
```

Then launch the UI:

```bash
.venv-upgrade/bin/python -m streamlit run app.py
```

For a fresh installation, create a separate Python 3.14 environment and install `requirements-runtime.txt`, then `python -m playwright install chromium`. The lock targets macOS arm64; do not replace an existing environment blindly.

The runtime defaults to `~/Library/Application Support/ApplyPilot`. It does not install a daemon, schedule jobs, discover unrelated tabs, use your personal Chrome profile or close the browser owner. `--home` selects an explicitly separate local runtime/database. Do not use network/cloud-synchronised storage for this database.

## Profile and documents

On first launch, use **Profile & documents → Reusable profile setup**. Confirm
contact details, education or an explicit none, work history or an explicit none,
country-specific work rights, sponsorship needs and the reusable screening list.
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

## 繁體中文

新版只有四個主要功能：找工作、LinkedIn Easy Apply、自動填表及開啟工作連結。首次使用先在本機確認可重用資料及履歷；找工作及開啟連結不受此限制。自動填表預設停在最後檢查頁，每份申請可另外授權提交。LinkedIn 只填到最後檢查頁，不會按 Submit。缺失資料集中顯示；你手動填寫後可直接繼續，不必逐項指出改了哪些欄位。你按「I submitted it」就立即完成並停止等待。

原始個人資料、文件及舊虛擬環境保留。首次匯入須先預覽、處理衝突並確認；系統會先備份。真實郵箱、Keychain 存取及僱主提交仍須另外設定和授權。模擬測試成功不代表所有招聘網站都已實測。

Detailed partial implementations and remaining acceptance work are listed in [ACCEPTANCE_LIMITS.md](ACCEPTANCE_LIMITS.md).

## Follow-up completion

See [portal contracts](PORTAL_CONTRACTS.md) for implemented AllHires/Apply4Law actions, record reconciliation and submission evidence. Gmail includes asynchronous connection status and explicit provider revocation. Autofill to final review does not require a Gmail connection.

繁中：申請特定資料只會在表格要求時集中提問；Git 只保存空白定義，不保存候選人的答案。網站儲存／下一步及重複工作紀錄已有本機測試；真實網站是否相容仍須按該表格核實。Gmail 功能已寫好，但首次連接仍需你完成 Google 同意程序，普通填表不需要連接。私人資料不會隨程式碼上傳 GitHub。
