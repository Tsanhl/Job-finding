# Unified local workspace validation

Validated on 12 September 2026 using the existing `.venv-upgrade` environment.

| Check | Actual result |
|---|---|
| Preserved ApplyPilot baseline | 170 tests passed, six subtests passed before editing |
| Final local synthetic CI | 253 tests passed, six subtests passed; see the latest test report for timings |
| Browser concurrency | Measured overlap of 1, 2, 5 and 10 for the corresponding requested workers |
| Merged UI | Real Chromium exercised profile save, preserved nested/custom facts, application reporting, assessment completion, reload and daily Gmail settings |
| JobSignal adapters | Original synthetic collector/programme fixtures ported and passing |
| Gmail | Injected provider/secret fixtures verified daily timing, manual sync, ambiguity, deduplication, partial failure and encrypted evidence; no live mailbox or native secret access |
| Codex | Installed 0.153.2 app-server completed live public discovery; local Greenhouse API verification returned seven vacancy candidates. Synthetic tests verify that structured filters reach Codex and candidate profile fields do not |
| Local security | Host, Origin, session and CSRF tests pass; dependency consistency check passes |
| Privacy | Tracked files, staged contents, reachable Git blobs and commit messages passed the privacy gate; candidate values stay outside Git |
| Live local cutover | Existing profile payload/version, document count and application count matched the consistent pre-change backup |
| Desktop cleanup | Original JobSignal directory moved into private Application Support archives; all 6,603 files/symlinks verified unchanged |

Command: `.venv-upgrade/bin/python scripts/run_local_ci.py`. Detailed results and synthetic screenshots are in ignored `output/upgrade-validation` and `output/unified-validation` directories. The original source bundles and consistent database backup are outside the repository under Application Support.

The local MCP tools were registered with Codex. A tool connection reload may be required before they appear in a conversation. No chat history, profile values or mailbox content was submitted to a model during this change.

Gmail's implemented cadence is 24 hours, with Sync now and catch-up while awake. Tracking remains disabled until the owner connects a mailbox and enables it. Assessment completion notices remain evidence to review against the correct component. Employer and LinkedIn acceptance and mailbox consent remain untested live. Live public discovery is reported separately from synthetic tests.

JobSignal's original demo-owner records remain in the verified archive. Settings offers a source-owner/count preview and explicit one-time import; its profile data becomes an unconfirmed proposal instead of overwriting the local owner's existing facts. The archived hosted-login, PostgreSQL and outgoing-delivery tests are not claimed as tests of the new local product.

## Live public discovery follow-up

A live public-source refresh returned two programme announcements. Bright Network returned HTTP 403; no attempt was made to bypass that restriction. The dashboard now records unavailable sources separately from pages with no extractable structured vacancies.

Codex searches use the existing supported sign-in. Greenhouse-hosted individual vacancy pages are mapped to the documented public GET job endpoint, including European hosted-board links. No application POST or account action is involved. Returned vacancy IDs are checked; publication dates are not treated as opening dates. Other rendered ATS pages may still require registered collectors and can return no extracted jobs.

Local matching now accepts legacy country-keyed work-right records without converting current work permission into unrestricted permission. Geographic uncertainty remains visible; results with an unknown country may be retained when the search permits unknowns. Candidate results are not assertions of eligibility or current application status.

The stdio MCP server completed an actual initialization and tools-list check. Registration alone does not load tools into an already-running Codex conversation; a connection reload is still required there. Gmail remained disconnected throughout this validation.

References: [Codex App Server](https://learn.chatgpt.com/docs/app-server), [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board.html).

## Dashboard and history amendment

The sidebar now offers Discover jobs, Search history, My Information, Saved opportunities, Applied History and Settings. Technical discovery registration and runtime-health panels are removed from the dashboard. Applied History includes autofill progress, grouped questions and resume controls; filling alone never marks an application submitted. Recording submission asks for explicit user confirmation and preserves notes and completion across restart.

Migration 006 adds private search requests and explicitly saved context to the existing database. Failed searches retain their criteria too. Profile fields and application notes save after changes; repeated-record identities survive removal and later edits. Local tool calls can retain the original search or autofill prompt. Existing chat history is not automatically imported, and saved context supplies no new execution authority.

Job cards separate dates, location, pay, academic criteria and skills. Source excerpts preserve wording and suppress programme-training descriptions where recognised. Missing source dates and qualifications remain unverified; older truncated records may require a new discovery run for fuller extraction. Verified expiry hides active and saved listings without deleting application history. A bin button removes a saved association.

Real Chromium verifies desktop/mobile layout, search reuse, profile autosave, repeated-record preservation, saved removal and submission confirmation. The full suite uses isolated records and intercepted pages. The actual local dashboard was opened read-only and existing profiles, documents and applications matched the pre-change backup. No employer, mailbox or live application operation was performed for this amendment; Gmail remains disabled until connected and enabled by its owner.

## Four reliability fixes

Validated against the complete local suite: 244 tests and six subtests passed. Saved-job regression data contains 1,205 opportunities, including 205 older saved jobs; keyset pagination preserves their visibility. Reminder evidence deduplicates against an application-scoped assessment identity while preserving completed states, distinct rounds and historical ambiguity. Gmail tests cover multi-page continuation across tracker restart, final history catch-up, transient retry and expired cursors.

Complete encrypted recovery restores a separate synthetic workspace with approved documents, a new evidence-key mapping, decryptable messages and unchanged uncertain submission attempts/checkpoints. Runs pause, grants revoke and accounts require reconnection. Wrong passwords, modified bundles, missing documents/keys, active-runtime locks and existing destinations are rejected. Existing local profile/document/application rows matched the pre-change database backup after migration 007; Gmail remains disconnected. No native Keychain export/import, mailbox access, employer interaction or live restoration was performed. See [RECOVERY.md](RECOVERY.md).

## Automatic per-workspace recovery

The final suite passed 253 tests and six subtests. Tests cover independent locally generated keys, owner-only permissions, restart preservation, verified online snapshots, daily/sleep cadence, disabled scheduling, additional-copy verification without key export, local retention, missing keys, failure preservation, graceful shutdown and fresh keys after restoration. The actual Chromium launcher verifies first-run automatic backup and Settings without displaying key contents. An exposed unlock-link creation race was fixed by atomically publishing the complete link.

The existing local workspace generated and verified its first automatic encrypted backup. Its previous manual bundle/key pair remains present. No additional destination was configured, so off-device disaster protection remains unverified. No user key or backup is part of Git; the implementation creates them only in private local storage. Native Keychain/mailbox acceptance remains outside this validation.
