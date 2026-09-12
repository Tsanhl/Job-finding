# Gmail desktop OAuth setup

Find Jobs, Open Job Links and Autofill to final review do not need Gmail or OAuth
configuration. Employer account passwords and email verification remain manual
for current function requests.

1. In your own Google Cloud project, enable Gmail API. Configure the OAuth consent screen and your permitted audience/test users.
2. Create an OAuth client of type **Desktop app**. Download its JSON configuration and keep it local. Do not use a Gmail password or put the file in Git.
3. Start the foreground runtime and open Settings. Supply the desktop JSON path and the exact Gmail identity, then use Connect/Reconnect. Complete consent in the system browser.
4. ApplyPilot requests only `gmail.readonly`, binds a loopback callback on an ephemeral port, and uses PKCE and validated state through Google's installed-app library. It checks the selected mailbox before storing a refresh token in native Keychain. [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
5. Use Gmail status to validate the connection. A saved connected flag alone is not proof of current access. Denied/revoked credentials require reconnect.

`gmail.readonly` permits broad mailbox reading. Search/recipient filters are application controls, not a Google-enforced recruitment-only scope. The application requests no sending, modification or deletion permission. [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)

External OAuth applications in Testing generally receive refresh tokens with a seven-day expiry when requesting Gmail scopes. Distribution and restricted-scope verification requirements depend on the app/audience; personal-use and testing exceptions do not remove all warnings or limits. Review Google's current policy before changing the project's audience or publishing status. [Token expiry](https://developers.google.com/identity/protocols/oauth2), [verification guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)

Local Disconnect immediately disables mail access in ApplyPilot. It does not claim that Google revoked the grant. Use the separately selected Revoke Gmail access operation to request Google revocation and remove the stored refresh token, or use Google Account settings. A lost/rejected revocation response stays explicitly unconfirmed.

For employer verification, configure the exact primary mailbox, recipient/alias, sender, tenant, subject and permitted destination host/path. Transactions are persisted before requesting verification. Old, wrong-recipient, replayed, ambiguous or insufficiently authenticated messages cannot authorize navigation. The current strict Gmail DKIM-header matcher may reject legitimate mail when evidence is ambiguous; that is a user handoff, not permission to scan unrelated messages.

Real OAuth consent, native Keychain acceptance, mailbox access and employer verification were not run during development. The delivered tests use simulated provider responses and synthetic messages.

## CLI and connection progress

```bash
.venv-upgrade/bin/python cli.py runtime gmail connect --email YOUR_GMAIL --config /absolute/local/desktop-client.json
.venv-upgrade/bin/python cli.py runtime gmail job --job-id JOB_ID
.venv-upgrade/bin/python cli.py runtime gmail status --email YOUR_GMAIL
.venv-upgrade/bin/python cli.py runtime gmail disconnect --email YOUR_GMAIL
.venv-upgrade/bin/python cli.py runtime gmail revoke --email YOUR_GMAIL
```

Connect returns a job ID immediately and the UI reports its progress. Disconnect invalidates an in-flight connection generation, so a late callback cannot silently reconnect the mailbox. Desktop OAuth remains necessary for optional automatic Gmail verification even when the entire application runs locally; creating the client configuration and granting consent are account setup, not missing adapter code.
