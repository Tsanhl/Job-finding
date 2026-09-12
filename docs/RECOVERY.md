# Complete local workspace recovery

A SQLite backup alone does not contain registered document bytes or the native Keychain key needed to decrypt mail evidence. The new encrypted workspace bundle contains all three. The existing database-only backup/restore commands remain available for their narrower purpose.

## Automatic setup for each local user

On first launch, the foreground runtime creates a unique random recovery key for that workspace under `recovery-keys/automatic.key` in its private Application Support directory. The repository contains only the generator, never a shared key or a user's backups. Key files have owner-only permissions and are never returned by the dashboard API or copied to an additional backup folder. Existing manual backup/key pairs are unchanged; automatic backups use their own key. A restored workspace starts a fresh automatic-backup history and key on its first launch; keep the source bundle/key pair for access to the older backups.

An initial encrypted snapshot is created and verified automatically. Subsequent snapshots are due every 24 hours while the runtime is running and the computer is awake, with a single catch-up after sleep. This installs no operating-system startup service. SQLite's consistent backup API runs on a separate connection; registered document bytes must match the captured database hashes. The existing CLI backup remains offline by default.

Settings → Automatic recovery shows the last verified backup, next due time, key-file location and local backup folder. Users can disable scheduling, run Back up now, and choose an existing additional backup folder on an external drive or in their backup service. Additional copies contain only the encrypted bundle, never the key. A verified copy means the bytes match; the application cannot prove the folder is physically off-device or that a cloud service uploaded it. Users must keep the key separately and arrange that independent copy. A local backup and local key alone do not protect against losing the computer.

The latest seven automatically created local bundles are kept. Retention runs only after a replacement bundle is verified and never removes manual bundles. Additional copies are not automatically pruned; manage their retention at the destination. Backup failures preserve previous snapshots and retry with bounded backoff. An unavailable additional folder is not recreated: the local bundle is kept, Settings flags the failed copy, and the next daily run or Back up now retries it. Missing, corrupt or insecurely permissioned keys require owner action; the application never silently generates a replacement for an established workspace.

To restore an automatic `.apbundle`, use the restore command below and enter the value from that workspace's `automatic.key` at the private terminal prompt. Save the key in a separate password manager or another secure location before you need recovery. Do not paste it into Codex, publish it or commit it to Git.

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
