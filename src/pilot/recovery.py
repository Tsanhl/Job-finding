"""Offline encrypted workspace bundles; restore into a new directory only.

Includes registered documents and the evidence decryption key, never browser
sessions, passwords or OAuth credentials. No passphrase appears in CLI arguments.
"""

from contextlib import contextmanager
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

import apsw
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .secrets import NativeSecrets
from .store import Store, digest, encode, uid

MAGIC = b"APPLYPILOT-RECOVERY-1\n"
MAX_BYTES = 128 * 1024 * 1024


def _cipher(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("Use a recovery passphrase of at least 12 characters")
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _outside_repository(path):
    if path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("Private recovery data must be outside the repository")


@contextmanager
def _offline(home):
    lock = (home / "runtime.lock").open("a+")
    try:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Stop the foreground runtime before workspace recovery"
            ) from None
        yield
    finally:
        lock.close()


def _validate_db(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or list(
        db.execute("PRAGMA foreign_key_check")
    ):
        raise ValueError("Recovery database integrity failed")


def backup_workspace(home, destination, passphrase, *, backend=None):
    home = Path(home).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    _outside_repository(destination)
    if destination.exists():
        raise ValueError("Recovery destination already exists")
    salt = os.urandom(16)
    cipher = _cipher(passphrase, salt)
    backend = backend or NativeSecrets()
    with (
        _offline(home),
        tempfile.TemporaryDirectory(prefix=".recovery-", dir=home) as scratch,
    ):
        snapshot = Path(scratch) / "database.sqlite3"
        src = apsw.Connection(
            str(home / "applypilot.sqlite3"), flags=apsw.SQLITE_OPEN_READONLY
        )
        target = apsw.Connection(str(snapshot))
        try:
            _validate_db(src)
            with target.backup("main", src, "main") as operation:
                operation.step(-1)
            _validate_db(target)
        finally:
            target.close()
            src.close()
        snapshot.chmod(0o600)
        db = Store(snapshot)  # Only the disposable snapshot is migrated.
        try:
            setting = db.one(
                "SELECT payload FROM workspace_settings WHERE key='evidence_vault'"
            )
            ref = (
                json.loads(setting["payload"])["ref"]
                if setting
                else "ApplyPilot:Evidence:"
                + digest(str(home / "applypilot.sqlite3"))[:24]
            )
            messages = db.rows("SELECT protected_payload FROM recruitment_messages")
            key = backend._read(ref, "local-owner") if messages else None
            if messages:
                if not key:
                    raise ValueError(
                        "Cannot back up encrypted evidence without its key"
                    )
                for row in messages:
                    Fernet(key.encode()).decrypt(row["protected_payload"].encode())
            files, documents = {}, []
            size = snapshot.stat().st_size
            for doc in db.rows("SELECT id,path,sha256,size FROM documents"):
                path = Path(doc["path"])
                if (
                    not path.is_file()
                    or path.stat().st_size != doc["size"]
                    or doc["size"] > 25 * 1024 * 1024
                ):
                    raise ValueError(
                        "A registered document is missing or changed; resolve it before backup"
                    )
                size += doc["size"]
                if size > MAX_BYTES:
                    raise ValueError("Recovery bundle exceeds the 128 MiB limit")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != doc["sha256"]:
                    raise ValueError("A registered document hash changed")
                name = "documents/" + digest(doc["id"]) + path.suffix.lower()
                files[name] = content
                documents.append(
                    {"id": doc["id"], "file": name, "sha256": doc["sha256"]}
                )
            db.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()
        data = snapshot.read_bytes()
        if len(data) + sum(map(len, files.values())) > MAX_BYTES:
            raise ValueError("Recovery bundle exceeds the 128 MiB limit")
        manifest = {
            "version": 1,
            "database_sha256": hashlib.sha256(data).hexdigest(),
            "documents": documents,
            "evidence_key": key,
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", encode(manifest))
            archive.writestr("database.sqlite3", data)
            for name, content in files.items():
                archive.writestr(name, content)
        encrypted = MAGIC + salt + cipher.encrypt(stream.getvalue())
        # Publish a complete encrypted file atomically, never overwrite a backup.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".bundle-", delete=False
        ) as out:
            temporary = Path(out.name)
            try:
                os.fchmod(out.fileno(), 0o600)
                out.write(encrypted)
                out.flush()
                os.fsync(out.fileno())
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    return {
        "saved": str(destination),
        "documents": len(documents),
        "encrypted_evidence": len(messages),
    }


def restore_workspace(bundle, home, passphrase, *, backend=None):
    home = Path(home).expanduser().resolve()
    _outside_repository(home)
    if home.exists():
        raise ValueError(
            "Restore into a new directory; existing workspaces are never overwritten"
        )
    source = Path(bundle).expanduser().resolve(strict=True)
    if source.stat().st_size > MAX_BYTES * 2:
        raise ValueError("Recovery file exceeds size limit")
    raw = source.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("Not an encrypted workspace recovery bundle")
    salt = raw[len(MAGIC) : len(MAGIC) + 16]
    try:
        payload = _cipher(passphrase, salt).decrypt(raw[len(MAGIC) + 16 :])
    except InvalidToken:
        raise ValueError(
            "Recovery passphrase is incorrect or bundle was modified"
        ) from None
    backend = backend or NativeSecrets()
    installed_ref = None
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=home.parent) as scratch:
        stage = Path(scratch) / "workspace"
        stage.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
                if (
                    len(names) != len(set(names))
                    or sum(x.file_size for x in archive.infolist())
                    > MAX_BYTES + 1024 * 1024
                ):
                    raise ValueError("Invalid recovery archive")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest["version"] != 1:
                    raise ValueError("Unsupported recovery version")
                data = archive.read("database.sqlite3")
                if hashlib.sha256(data).hexdigest() != manifest["database_sha256"]:
                    raise ValueError("Recovery database hash mismatch")
                database = stage / "applypilot.sqlite3"
                database.write_bytes(data)
                database.chmod(0o600)
                raw_db = apsw.Connection(str(database))
                try:
                    _validate_db(raw_db)
                finally:
                    raw_db.close()
                store = Store(database)
                try:
                    docs = {r["id"]: r for r in store.rows("SELECT * FROM documents")}
                    if len(manifest["documents"]) != len(docs) or {
                        d["id"] for d in manifest["documents"]
                    } != set(docs):
                        raise ValueError("Recovery document manifest mismatch")
                    (stage / "documents").mkdir(mode=0o700)
                    for doc in manifest["documents"]:
                        name = doc["file"]
                        path = Path(name)
                        if (
                            path.parts[:1] != ("documents",)
                            or len(path.parts) != 2
                            or path.name in (".", "..")
                        ):
                            raise ValueError("Invalid recovery document path")
                        content = archive.read(name)
                        if (
                            hashlib.sha256(content).hexdigest() != doc["sha256"]
                            or doc["sha256"] != docs[doc["id"]]["sha256"]
                            or len(content) != docs[doc["id"]]["size"]
                        ):
                            raise ValueError("Recovery document hash mismatch")
                        (stage / path).write_bytes(content)
                        (stage / path).chmod(0o600)
                        store.db.execute(
                            "UPDATE documents SET path=? WHERE id=?",
                            (str(home / path), doc["id"]),
                        )
                    key = manifest.get("evidence_key")
                    for row in store.rows(
                        "SELECT protected_payload FROM recruitment_messages"
                    ):
                        if not key:
                            raise ValueError("Recovery evidence key is missing")
                        Fernet(key.encode()).decrypt(row["protected_payload"].encode())
                    if key:
                        new_ref = "ApplyPilot:Evidence:" + uid()
                        backend.put(new_ref, "local-owner", key, replace=False)
                        installed_ref = new_ref
                    ref = installed_ref or "ApplyPilot:Evidence:" + uid()
                    store.db.execute(
                        "INSERT INTO workspace_settings VALUES('evidence_vault',?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
                        (encode({"ref": ref}), time.time()),
                    )
                    # Keep uncertain attempts and checkpoints, but require fresh user authority.
                    store.recover()
                    store.db.execute(
                        "UPDATE runs SET status='PAUSED' WHERE status='ACTIVE'"
                    )
                    store.db.execute("UPDATE grants SET revoked=1")
                    store.db.execute("DELETE FROM sessions")
                    store.db.execute(
                        "UPDATE mail_connections SET status='RECONNECT' WHERE status='CONNECTED'"
                    )
                    store.db.execute("UPDATE portal_accounts SET status='RECONNECT'")
                    store.db.execute(
                        "UPDATE mail_tracking SET enabled=0,lease_until=0,status='DISABLED'"
                    )
                    store.db.execute(
                        "UPDATE workspace_jobs SET state='CANCELLED' WHERE state='RUNNING'"
                    )
                    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    store.close()
            if home.exists():
                raise ValueError("Restore destination appeared during verification")
            stage.rename(home)
        except BaseException:
            if installed_ref:
                backend.delete(installed_ref, "local-owner")
            raise
    return {
        "restored": str(home),
        "documents": len(docs),
        "requires_reconnect": True,
        "runs_paused": True,
    }
