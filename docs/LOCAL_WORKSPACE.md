# Unified local workspace

## Launch

Double-click `Open ApplyPilot.command`, or run:

```sh
.venv-upgrade/bin/python -m src.pilot.desktop
```

The JobSignal-designed interface opens at `http://127.0.0.1:8502`. The launcher opens a private unlock link; its token is a URL fragment and is not sent to HTTP access logs. Use the launcher again after a restart. Keep its process running while using the application. The old Streamlit entry point remains a compatibility interface, not the default launcher.

The launcher starts the existing singleton runtime if necessary. A stale running runtime must be stopped before upgrading; it is never silently restarted during an application. Profiles, history, public-source discovery and Gmail tracking do not require a managed browser. To open or fill employer tabs, run `.venv-upgrade/bin/python scripts/open_browser.py` first. This preserves the existing browser ownership and session boundaries.

## Functions

| Section | Behaviour |
|---|---|
| Discover jobs | Bounded public searches, source requirements, dates and pay, locally persisted results and profile checks. Optional Codex search provides candidate URLs that are fetched again before listing. |
| Search portfolios | Multiple named career directions, structured filters and notes; matching reads the common confirmed profile. Free-text notes are retained, not silently treated as enforced filters. |
| My Information | Contact fields, repeated education/work/right-to-work/reference/project records, the existing 66-question catalogue, custom JSON and an approved-document file picker. Unknown and Boolean No remain distinct. |
| Saved opportunities | A shortlist that is independent of application status. |
| Autofill | The existing shared engine, grouped gaps, manual-edit detection, retained forms and pause/resume/stop. Each target defaults to final review. Explicit Submit still requires existing adapter qualification and current authority. LinkedIn remains review-only. |
| Applied History | Manual entry, I applied, persistent application snapshots, notes, recruitment stages, separate assessment components and immediate user-reported completion. |
| Discovery status | Career-site roots, imported public JobSignal source definitions and runtime health. |
| Settings | Gmail connection, daily tracking, import preview and consistent backups. No outgoing Email alerts page. |

**Newly opened** means an explicitly published application opening date within the last seven days. A posting date is not an opening date. **Link opened** records a user opening a job card. **Applied** requires user-reported or confirmed submission. Unsave, listing expiry and new assessment messages cannot erase or restart an application.

## Gmail: once a day

Connect a Google Desktop OAuth client in Settings, complete system-browser consent, then enable tracking for that mailbox. The first run covers the chosen bounded lookback (default 90 days). Subsequent runs are due 24 hours after the last successful run. **Sync now** can be used between automatic checks. Manual and scheduled runs share a lease and deduplication keys.

The Mac must be awake and the worker running. A missed interval is caught up after resuming; it is not replayed repeatedly. Closing a browser tab does not stop the runtime. Closing the launcher does. No startup service or Codex scheduled task is installed automatically.

Metadata is checked before likely recruitment message bodies are fetched. Message evidence is encrypted using a Keychain-managed key. Matching requires the selected recipient and a unique employer/role match; ambiguous messages remain for user review. Invitations create separate assessments. Receipts, completion notices, interviews and outcome messages remain attributable evidence for review. A generic completion notice cannot automatically complete every component or verify a submission.

Deadline wording is preserved; ambiguous relative dates require a portal check. The tracker never opens assessment links, starts tests, answers assessments, sends mail, or marks messages read. Read-only Gmail scope covers mailbox access; recruitment filtering is an application rule, not a Google-enforced narrow scope. No real mailbox is used by automated tests.

## Codex integration

Dashboard discovery uses the installed `codex app-server` JSON-RPC interface. It sends only the explicitly approved query, location and result count, uses an ephemeral read-only thread, disables shell/connected tools and does not pass the candidate profile, documents or mailbox contents. Additional capability requests are declined. Output is limited to ten candidate source URLs and verified by the public-source pipeline. The deadline is three minutes; errors and cancellation retain already committed records. Supported sign-in and model access are required; subscription access is not promised to transfer to a separate API endpoint.

To let Codex use narrow local-record tools, register the MCP entry point using absolute paths to this checkout and its Python environment:

```sh
codex mcp add applypilot-local -- /absolute/path/to/Job/.venv-upgrade/bin/python /absolute/path/to/Job/scripts/local_mcp.py
```

Restart/reload the Codex tool connection after registration. This does not import all chat history. Available tools search jobs, poll discovery, retrieve selected profile fields, save explicitly requested facts with a profile-version check, find named applications, record user-reported application submission and mark a specific assessment complete. They expose no SQL, shell, raw mailbox or arbitrary-file endpoint. Employer passwords are never requested or saved.

Official integration references: [Codex app-server](https://learn.chatgpt.com/docs/app-server), [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [Gmail synchronization](https://developers.google.com/workspace/gmail/api/guides/sync).

## Storage and recovery

The authoritative database remains `~/Library/Application Support/ApplyPilot/applypilot.sqlite3`. Migration 005 adds workspace settings, portfolios, the opportunity index, durable application history, assessment components, protected recruitment evidence, daily sync state and background-operation state. New interfaces call the existing Unix-socket runtime; no second writable JobSignal database is started.

The explicit active profile chooses one owner. Multiple legacy profile lineages require selection. Saves use immutable versions and reject stale updates. Imported profiles remain proposals rather than overwriting confirmed owner facts. All private documents, exports, credentials, evidence and source-project backups remain outside Git.

Settings supports a read-only JobSignal import preview. Select the source owner, review counts and demo status, then explicitly import. A consistent destination backup is created before writes; the fingerprint must match the preview. Repeating an unchanged import is a no-op. Historical applied labels become user-reported records, never verified receipts. Source files remain unchanged.

Existing backup/restore commands remain available:

```sh
.venv-upgrade/bin/python cli.py runtime backup /private/path/new-backup.sqlite3
# Stop the runtime before an explicitly approved restore.
.venv-upgrade/bin/python cli.py runtime restore /private/path/backup.sqlite3 --approve-live-restore
```

The separate JobSignal source directory can be archived outside Desktop after validation. Its Git history, uncommitted files, private data and original environment must move together. Restoring that archived project to its original location restores its original absolute environment paths. The unified app uses its own existing upgrade environment.

## Validation and release boundaries

Run `.venv-upgrade/bin/python scripts/run_local_ci.py`. Added tests cover the original JobSignal collector/programme fixtures, profile preservation, distinction between opening and applying, terminal application state, separate assessments, daily synchronization, encrypted evidence, ambiguous messages, partial failures, import/backup idempotency, Codex disclosure, local authentication and an actual Chromium dashboard flow.

The former JobSignal public deployment, PostgreSQL, SMTP, Discord, demo-account and hosted login services are not part of this local product. Their original source/tests are preserved in the source archive; the relevant public collectors and programme tests are carried into this repository. This is not a claim that every former hosted-product test runs against the new local schema.

Live employer, LinkedIn, native secret and mailbox acceptance remain separate from synthetic validation. A Codex protocol handshake confirms the installed interface, not an end-to-end search entitlement. No private data is pushed to GitHub by launching or using the application.
