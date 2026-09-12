# Complete local workspace recovery

A SQLite backup alone does not contain registered document bytes or the native Keychain key needed to decrypt mail evidence. The new encrypted workspace bundle contains all three. The existing database-only backup/restore commands remain available for their narrower purpose.

## Create a complete backup

Stop the foreground ApplyPilot launcher first. The backup command acquires the same runtime lock and refuses to run against an active runtime.

```sh
.venv-upgrade/bin/python cli.py runtime backup-workspace "$HOME/ApplyPilot-backup.apbundle"
```

Enter and confirm a recovery passphrase in the terminal prompt. It must be at least 12 characters; use a long, unique passphrase and keep it separately. It is not accepted as a command-line argument, recorded in logs or saved by the app. Losing it makes this bundle unrecoverable.

The command takes a consistent, integrity-checked SQLite snapshot and checks every registered document against its saved size and hash. Missing/changed files and a missing evidence key stop the backup; a partial bundle is not published. Any schema upgrade happens only in the disposable snapshot, not the source database. Native Keychain access is needed only when there is encrypted mail evidence to export. The key is included inside the encrypted bundle, never as a plaintext sidecar.

Version 1 uses Scrypt (N=32768, r=8, p=1) and Fernet authenticated encryption with a fresh salt. The bounded, uncompressed archive supports at most 128 MiB of database/document content. Output is owner-readable only and cannot overwrite an existing backup. Bundles are rejected inside this repository, ignored by Git and blocked by the privacy gate if tracked.

## Restore into a separate workspace

Choose a destination directory that does not exist. Existing workspaces are never overwritten by this command.

```sh
.venv-upgrade/bin/python cli.py runtime --home "$HOME/Library/Application Support/ApplyPilot-restored" restore-workspace "$HOME/ApplyPilot-backup.apbundle"
```

Enter the recovery passphrase through the terminal prompt. Restore verifies authenticated encryption, database integrity/foreign keys, document coverage and hashes, and decryptability of every saved mail-evidence record before publishing the restored directory. Documents receive paths under the new workspace. A new native key reference is stored in the restored database; it no longer depends on the source database path. If validation fails, no usable restored directory is published.

Launch the restored copy explicitly:

```sh
.venv-upgrade/bin/python -m src.pilot.desktop --home "$HOME/Library/Application Support/ApplyPilot-restored" --port 8503
```

Inspect My Information, approved documents, Applied History, checkpoints and Gmail evidence. All previously active runs are paused and their grants revoked. Uncertain submissions and their attempts remain uncertain and continue to block blind replay. Browser tab bindings are discarded because the original browser session cannot be reconstructed from a database.

Account passwords, OAuth credentials and browser sessions are **not** included. Gmail tracking is disabled after restore and accounts require reconnection. Historical encrypted mail evidence remains readable using the restored evidence key. Authorize new application work only after reconciling the relevant portal state.

Keep the original workspace and the bundle until this independent copy has been verified. Returning to the original launcher/home is the rollback path; recovery does not delete or edit the original workspace.

## Evidence and limits

Synthetic tests use disposable stores and an in-memory secret backend. They cover separate-directory restoration, approved document hashes/paths, evidence key remapping and decryption, preserved checkpoints/uncertain attempts, revoked grants, wrong passphrases, modified bundles, missing documents/keys, existing destinations and runtime-lock refusal. They do not exercise live native Keychain permissions, a real Gmail mailbox, or employer submission. Those require separate supervised acceptance.
