"""Explicit offline restore. Never copy an active database or discard its files."""

import fcntl
import os
import time
from pathlib import Path

from .store import Store


def restore(backup, home, *, confirmed=False):
    if not confirmed:
        raise PermissionError("Explicit approval required for live restore")
    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (home / "runtime.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Stop the foreground runtime before restoring")
    source = Path(backup).expanduser().resolve(strict=True)
    live = home / "applypilot.sqlite3"
    if source == live:
        lock.close()
        raise ValueError("Backup must differ from live database")
    stamp = str(time.time_ns())
    prepared = home / ("restore-" + stamp + ".sqlite3")
    try:
        # Read-only source connection; no migration or journal mutation of backup.
        import apsw

        src = apsw.Connection(str(source), flags=apsw.SQLITE_OPEN_READONLY)
        target = apsw.Connection(str(prepared))
        try:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity failed")
            with target.backup("main", src, "main") as job:
                job.step(-1)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restore verification failed")
        finally:
            target.close()
            src.close()
        os.chmod(prepared, 0o600)
        if live.exists():
            current = Store(live)
            try:
                current.backup(home / ("before-restore-" + stamp + ".sqlite3"))
            finally:
                current.close()
            # Preserve the original, including any surviving journal companions.
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(live) + suffix)
                if path.exists():
                    path.rename(home / ("original-" + stamp + ".sqlite3" + suffix))
        prepared.rename(live)
        return {
            "restored": str(live),
            "rollback_backup_prefix": "before-restore-" + stamp,
        }
    finally:
        lock.close()
