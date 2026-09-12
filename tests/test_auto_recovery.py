import asyncio
import threading
from pathlib import Path

import pytest

from src.pilot.auto_recovery import AutoRecovery, DAY
from src.pilot.recovery import verify_bundle
from src.pilot.store import Store
from src.pilot.workspace import Workspace


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "user" / "applypilot.sqlite3")
    ws = Workspace(store)
    profile = ws.profile()
    ws.save_profile({"full_name": "Synthetic Local Owner"}, profile["version"])
    now = [1000]
    recovery = AutoRecovery(store, clock=lambda: now[0])
    yield recovery, now
    store.close()


def test_unique_per_workspace_key_and_restart_preservation(service, tmp_path):
    recovery, _ = service
    first = recovery.setup()
    original = recovery.key_file.read_bytes()
    assert first["enabled"] and recovery.key_file.stat().st_mode & 0o777 == 0o600
    assert recovery.key_file.parent.stat().st_mode & 0o777 == 0o700
    AutoRecovery(recovery.store).setup()
    assert recovery.key_file.read_bytes() == original
    other = Store(tmp_path / "another-user" / "applypilot.sqlite3")
    try:
        second = AutoRecovery(other)
        second.setup()
        assert second.key_file.read_bytes() != original
    finally:
        other.close()
    assert original.decode().strip() not in str(recovery.status())
    assert original.decode().strip() not in str(recovery.config())


def test_daily_snapshot_verified_and_catchup_after_sleep(service):
    recovery, now = service

    async def scenario():
        await recovery.tick()
        status = recovery.status()
        assert status["state"] == "VERIFIED_LOCAL" and status["last_success"] == 1000
        first = recovery.directory / status["latest_bundle"]
        assert b"Synthetic Local Owner" not in first.read_bytes()
        assert verify_bundle(first, recovery.key_file.read_text().strip())["verified"]
        now[0] += 60
        await recovery.tick()
        assert recovery.status()["latest_bundle"] == first.name
        now[0] += 3 * DAY
        await recovery.tick()
        assert recovery.status()["retained_local_backups"] == 2
        assert recovery.status()["next_due"] == now[0] + DAY

    asyncio.run(scenario())


def test_missing_key_does_not_silently_rotate_or_delete_backups(service):
    recovery, now = service
    asyncio.run(recovery.tick())
    old_key = recovery.key_file.read_bytes()
    backup = recovery.directory / recovery.status()["latest_bundle"]
    recovery.key_file.unlink()
    now[0] += DAY
    asyncio.run(recovery.tick())
    assert recovery.status()["state"] == "KEY_ACTION_REQUIRED"
    assert not recovery.key_file.exists() and backup.exists()
    recovery.key_file.write_bytes(old_key)
    recovery.key_file.chmod(0o600)
    asyncio.run(recovery.tick())
    assert recovery.status()["state"] == "VERIFIED_LOCAL"


def test_disabled_schedule_and_verified_secondary_copy_without_key(service, tmp_path):
    recovery, now = service
    secondary = tmp_path / "secondary"
    secondary.mkdir()
    recovery.configure(
        {
            "enabled": False,
            "secondary_folder": str(secondary),
            "key_saved_separately": True,
        }
    )
    asyncio.run(recovery.tick())
    assert not list(recovery.directory.glob("*.apbundle"))
    asyncio.run(recovery.tick(force=True))
    status = recovery.status()
    assert status["latest_secondary_verified"]
    assert status["off_device_protection"] == "NOT_VERIFIED"
    assert (secondary / status["latest_bundle"]).read_bytes() == (
        recovery.directory / status["latest_bundle"]
    ).read_bytes()
    assert not list(secondary.glob("*.key"))
    assert len(list(secondary.iterdir())) == 1
    now[0] += DAY
    asyncio.run(recovery.tick())
    assert recovery.status()["retained_local_backups"] == 1


def test_retention_removes_only_managed_local_bundles_after_success(service):
    recovery, now = service
    recovery.setup()
    manual = recovery.directory / "manual.apbundle"
    manual.write_bytes(b"manual snapshot")

    async def scenario():
        for i in range(9):
            now[0] = 1000 + DAY * i
            await recovery.tick()

    asyncio.run(scenario())
    assert recovery.status()["retained_local_backups"] == 7
    assert len(list(recovery.directory.glob("auto-*.apbundle"))) == 7
    assert manual.read_bytes() == b"manual snapshot"


def test_failure_retains_previous_verified_backup_and_retries(service, monkeypatch):
    recovery, now = service
    asyncio.run(recovery.tick())
    previous = recovery.status()["latest_bundle"]
    now[0] += DAY

    def fail(*args, **kwargs):
        raise OSError("Synthetic failure with private details")

    monkeypatch.setattr("src.pilot.auto_recovery.backup_workspace", fail)
    asyncio.run(recovery.tick())
    status = recovery.status()
    assert status["state"] == "BACKUP_FAILED"
    assert (
        status["latest_bundle"] == previous and (recovery.directory / previous).exists()
    )
    assert status["next_due"] == now[0] + 300
    assert "private details" not in str(status)


def test_unavailable_secondary_copy_preserves_local_bundle(service, tmp_path):
    recovery, _ = service
    secondary = tmp_path / "removable"
    secondary.mkdir()
    recovery.configure({"enabled": True, "secondary_folder": str(secondary)})
    secondary.rmdir()
    asyncio.run(recovery.tick())
    assert recovery.status()["state"] == "SECONDARY_COPY_REQUIRED"
    assert not secondary.exists()
    assert (recovery.directory / recovery.status()["latest_bundle"]).is_file()


def test_shutdown_waits_for_inflight_snapshot_before_returning(service, monkeypatch):
    recovery, _ = service
    started = threading.Event()
    release = threading.Event()

    def slow(folder):
        started.set()
        release.wait(timeout=5)
        return {
            "name": "auto-synthetic.apbundle",
            "sha256": "synthetic",
            "secondary_folder": "",
            "secondary_verified": False,
        }

    monkeypatch.setattr(recovery, "_perform", slow)

    async def scenario():
        task = asyncio.create_task(recovery.tick())
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recovery.status()["last_success"] == 1000

    asyncio.run(scenario())


def test_restored_workspace_initializes_fresh_automatic_key(service, tmp_path):
    from src.pilot.recovery import restore_workspace

    recovery, _ = service
    asyncio.run(recovery.tick())
    original = recovery.key_file.read_text().strip()
    bundle = recovery.directory / recovery.status()["latest_bundle"]
    destination = tmp_path / "restored-user"
    restore_workspace(bundle, destination, original)
    store = Store(destination / "applypilot.sqlite3")
    try:
        restored = AutoRecovery(store)
        restored.setup()
        assert restored.status()["state"] == "READY"
        assert restored.key_file.read_text().strip() != original
        assert restored.status()["retained_local_backups"] == 0
    finally:
        store.close()
    assert bundle.exists()
