# Unified local workspace validation

Validated on 12 September 2026 using the existing `.venv-upgrade` environment.

| Check | Actual result |
|---|---|
| Preserved ApplyPilot baseline | 170 tests passed, six subtests passed before editing |
| Final local synthetic CI | 222 tests passed, six subtests passed; 47.78 seconds |
| Browser concurrency | Measured overlap of 1, 2, 5 and 10 for the corresponding requested workers |
| Merged UI | Real Chromium exercised profile save, preserved nested/custom facts, application reporting, assessment completion, reload and daily Gmail settings |
| JobSignal adapters | Original synthetic collector/programme fixtures ported and passing |
| Gmail | Injected provider/secret fixtures verified daily timing, manual sync, ambiguity, deduplication, partial failure and encrypted evidence; no live mailbox or native secret access |
| Codex | Installed 0.153.2 app-server initialization and config protocol passed; request/disclosure behaviour tested with a synthetic app-server; no live model search performed |
| Local security | Host, Origin, session and CSRF tests pass; dependency consistency check passes |
| Privacy | Tracked files, staged contents, reachable Git blobs and commit messages passed the privacy gate; candidate values stay outside Git |
| Live local cutover | Existing profile payload/version, document count and application count matched the consistent pre-change backup |
| Desktop cleanup | Original JobSignal directory moved into private Application Support archives; all 6,603 files/symlinks verified unchanged |

Command: `.venv-upgrade/bin/python scripts/run_local_ci.py`. Detailed results and synthetic screenshots are in ignored `output/upgrade-validation` and `output/unified-validation` directories. The original source bundles and consistent database backup are outside the repository under Application Support.

The local MCP tools were registered with Codex. A tool connection reload may be required before they appear in a conversation. No chat history, profile values or mailbox content was submitted to a model during this change.

Gmail's implemented cadence is 24 hours, with Sync now and catch-up while awake. Tracking remains disabled until the owner connects a mailbox and enables it. Assessment completion notices remain evidence to review against the correct component. Employer and LinkedIn acceptance, mailbox consent and a live Codex discovery request are not represented as synthetic-test successes.

JobSignal's original demo-owner records remain in the verified archive. Settings offers a source-owner/count preview and explicit one-time import; its profile data becomes an unconfirmed proposal instead of overwriting the local owner's existing facts. The archived hosted-login, PostgreSQL and outgoing-delivery tests are not claimed as tests of the new local product.
