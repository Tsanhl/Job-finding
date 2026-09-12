"""Per-workspace daily encrypted snapshots, owned by the foreground runtime."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time

from .recovery import backup_workspace, verify_bundle, _outside_repository
from .store import encode, uid

DAY = 86400
KEEP = 7


class AutoRecovery:
    def __init__(self, store, *, clock=time.time, backend=None):
        self.store = store
        self.home = store.path.parent
        self.clock = clock
        self.backend = backend
        self.lock = asyncio.Lock()
        self.directory = self.home / "backups" / "automatic-recovery"
        self.key_file = self.home / "recovery-keys" / "automatic.key"

    def config(self):
        row = self.store.one(
            "SELECT payload FROM workspace_settings WHERE key='automatic_recovery'"
        )
        return json.loads(row["payload"]) if row else None

    def save(self, config):
        self.store.db.execute(
            "INSERT INTO workspace_settings VALUES('automatic_recovery',?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
            (encode(config), self.clock()),
        )

    def setup(self):
        old = self.config()
        config = (
            dict(old)
            if old
            else {
                "enabled": True,
                "next_due": 0,
                "last_success": None,
                "state": "READY",
                "failures": 0,
                "bundles": [],
                "secondary_folder": "",
                "key_saved_separately": False,
            }
        )
        try:
            _outside_repository(self.home)
            for directory in (
                self.home / "backups",
                self.directory,
                self.key_file.parent,
            ):
                if directory.is_symlink():
                    raise ValueError("Private recovery directory cannot be a symlink")
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
            if not self.key_file.exists() and old:
                raise ValueError("Existing recovery key is missing; do not replace it")
            if not self.key_file.exists():
                # Publish only a completely written key. A crash never leaves a partial key.
                with tempfile.NamedTemporaryFile(
                    dir=self.key_file.parent, delete=False
                ) as file:
                    temporary = Path(file.name)
                    try:
                        os.fchmod(file.fileno(), 0o600)
                        file.write((secrets.token_urlsafe(48) + "\n").encode())
                        file.flush()
                        os.fsync(file.fileno())
                        os.link(temporary, self.key_file)
                    finally:
                        temporary.unlink(missing_ok=True)
            self._key()
            if config["state"] == "KEY_ACTION_REQUIRED":
                config.update(state="READY", next_due=0)
        except (ValueError, OSError):
            config["state"] = "KEY_ACTION_REQUIRED"
        if not old or config != old:
            self.save(config)
        return self.status()

    def _key(self):
        if (
            self.key_file.is_symlink()
            or not self.key_file.is_file()
            or self.key_file.stat().st_mode & 0o077
        ):
            raise ValueError("Recovery key file is missing or has unsafe permissions")
        value = self.key_file.read_text().strip()
        if len(value) != 64 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in value
        ):
            raise ValueError("Recovery key file is invalid")
        return value

    def status(self):
        config = self.config() or {
            "enabled": False,
            "state": "NOT_INITIALIZED",
            "bundles": [],
        }
        newest = config.get("bundles", [])[-1:]
        return {
            k: config.get(k)
            for k in (
                "enabled",
                "state",
                "last_success",
                "next_due",
                "secondary_folder",
                "key_saved_separately",
            )
        } | {
            "running": self.lock.locked(),
            "interval_seconds": DAY,
            "retained_local_backups": len(config["bundles"]),
            "backup_folder": str(self.directory),
            "key_file": str(self.key_file),
            "latest_bundle": newest[0]["name"] if newest else None,
            "latest_secondary_verified": bool(
                newest
                and newest[0].get("secondary_folder") == config.get("secondary_folder")
                and newest[0].get("secondary_verified")
            ),
            "off_device_protection": "NOT_VERIFIED",
        }

    def configure(self, request):
        self.setup()
        config = self.config()
        enabled = request.get("enabled")
        confirmed = request.get("key_saved_separately", False)
        if type(enabled) is not bool or type(confirmed) is not bool:
            raise ValueError("Choose automatic recovery and key-storage preferences")
        folder = str(request.get("secondary_folder", "")).strip()
        if folder:
            path = Path(folder).expanduser().resolve(strict=True)
            _outside_repository(path)
            if not path.is_dir() or path.is_relative_to(self.home):
                raise ValueError(
                    "Choose an existing backup folder outside the workspace"
                )
            folder = str(path)
        config.update(
            enabled=enabled, secondary_folder=folder, key_saved_separately=confirmed
        )
        if enabled and (
            folder != (self.config() or {}).get("secondary_folder")
            or not config.get("last_success")
        ):
            config["next_due"] = 0
        self.save(config)
        return self.status()

    def _perform(self, folder):
        secret = self._key()
        name = "auto-" + str(time.time_ns()) + "-" + uid() + ".apbundle"
        destination = self.directory / name
        try:
            backup_workspace(
                self.home, destination, secret, backend=self.backend, online=True
            )
            checked = verify_bundle(destination, secret)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            secret = None
        copied = False
        if folder:
            try:
                secondary = Path(folder)
                # Do not recreate a disconnected drive's mount point on the local disk.
                if not secondary.is_dir():
                    raise OSError("Backup folder unavailable")
                with tempfile.NamedTemporaryFile(
                    dir=secondary, prefix=".applypilot-", delete=False
                ) as file:
                    temp = Path(file.name)
                    try:
                        os.fchmod(file.fileno(), 0o600)
                        with destination.open("rb") as source:
                            shutil.copyfileobj(source, file)
                        file.flush()
                        os.fsync(file.fileno())
                        if (
                            hashlib.sha256(temp.read_bytes()).hexdigest()
                            != checked["sha256"]
                        ):
                            raise OSError("Copy verification failed")
                        os.link(temp, secondary / name)
                        copied = True
                    finally:
                        temp.unlink(missing_ok=True)
            except OSError:
                pass  # Local verified backup is retained; status requests attention.
        return {
            "name": name,
            "sha256": checked["sha256"],
            "secondary_folder": folder,
            "secondary_verified": copied,
        }

    async def tick(self, *, force=False):
        if self.lock.locked():
            return {"state": "ALREADY_RUNNING"}
        self.setup()
        config = self.config()
        if config["state"] == "KEY_ACTION_REQUIRED":
            return self.status()
        if not force and (not config["enabled"] or config["next_due"] > self.clock()):
            return self.status()
        async with self.lock:
            cancelled = False
            task = asyncio.create_task(
                asyncio.to_thread(self._perform, config["secondary_folder"])
            )
            try:
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Finish the bounded snapshot before the runtime closes its database.
                    cancelled = True
                    result = await task
                latest = self.config()
                latest["bundles"].append(result)
                obsolete = latest["bundles"][:-KEEP]
                latest["bundles"] = latest["bundles"][-KEEP:]
                latest.update(
                    last_success=self.clock(),
                    next_due=self.clock() + DAY,
                    failures=0,
                    state="SECONDARY_COPY_REQUIRED"
                    if config["secondary_folder"] and not result["secondary_verified"]
                    else "VERIFIED_LOCAL",
                )
                self.save(latest)
                for item in obsolete:
                    name = item["name"]
                    if (
                        Path(name).name == name
                        and name.startswith("auto-")
                        and name.endswith(".apbundle")
                    ):
                        (self.directory / name).unlink(missing_ok=True)
            except Exception:
                latest = self.config()
                failures = latest.get("failures", 0) + 1
                latest.update(
                    state="BACKUP_FAILED",
                    failures=failures,
                    next_due=self.clock() + min(3600, 300 * 2 ** min(failures - 1, 4)),
                )
                self.save(latest)
            if cancelled:
                raise asyncio.CancelledError
        return self.status()

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(60)
