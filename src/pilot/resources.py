"""Versioned approved files, bounded derived cache and non-destructive intake."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
from collections import OrderedDict
from pathlib import Path

from .store import digest, encode, uid


class Documents:
    def __init__(self, store):
        self.store = store

    def register(
        self, path, kind, *, approved=False, profile_id=None, applicability=None
    ):
        p = Path(path).expanduser().resolve(strict=True)
        if not p.is_file() or p.suffix.lower() not in {
            ".pdf",
            ".docx",
            ".doc",
            ".txt",
            ".rtf",
        }:
            raise ValueError("Unsupported document file")
        if p.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Document exceeds local 25 MiB limit")
        content = p.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if p.suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("File is not a PDF")
        if p.suffix.lower() == ".docx" and not content.startswith(b"PK"):
            raise ValueError("File is not a DOCX")
        existing = self.store.one(
            "SELECT * FROM documents WHERE path=? AND sha256=?", (str(p), sha)
        )
        if existing:
            if approved and not existing["approved"]:
                self.store.db.execute(
                    "UPDATE documents SET approved=1 WHERE id=?", (existing["id"],)
                )
            return existing["id"]
        row = self.store.one(
            "SELECT MAX(version) AS n FROM documents WHERE path=?", (str(p),)
        )
        doc = uid()
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc,
                    kind,
                    str(p),
                    sha,
                    (row["n"] or 0) + 1,
                    int(approved),
                    profile_id,
                    encode(applicability or {}),
                    mimetypes.guess_type(p)[0] or "application/octet-stream",
                    len(content),
                    time.time(),
                ),
            )
        return doc

    def resolve(self, doc_id, target, profile_id=None):
        doc = self.store.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not doc or not doc["approved"]:
            raise ValueError("Document approval required")
        if doc["profile_id"] and profile_id != doc["profile_id"]:
            raise ValueError("Document profile mismatch")
        scope = json.loads(doc["applicability"])
        for k in ("employer", "role", "identity"):
            if scope.get(k) and scope[k] != getattr(target, k):
                raise ValueError("Document is approved for a different application")
        p = Path(doc["path"])
        if (
            not p.is_file()
            or hashlib.sha256(p.read_bytes()).hexdigest() != doc["sha256"]
        ):
            raise ValueError("Document changed; approve a new version")
        return doc


class Cache:
    """Only derived extraction/research/drafts. No authority or secrets."""

    def __init__(self, store, memory_limit=64, disk_limit=32 * 1024 * 1024):
        self.store = store
        self.memory = OrderedDict()
        self.limit = memory_limit
        self.disk_limit = disk_limit
        self.pending = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        item = self.memory.get(key)
        if item and item[0] > time.time():
            self.hits += 1
            self.memory.move_to_end(key)
            return item[1]
        row = self.store.one(
            "SELECT * FROM cache_entries WHERE key=? AND expires>?", (key, time.time())
        )
        if row:
            value = json.loads(row["payload"])
            self.memory[key] = (row["expires"], value)
            while len(self.memory) > self.limit:
                self.memory.popitem(last=False)
            self.hits += 1
            return value
        self.misses += 1
        return None

    def put(self, key, kind, value, ttl=3600):
        if kind not in {"extraction", "research", "draft"}:
            raise ValueError("Not disposable cache content")
        payload = encode(value)
        size = len(payload.encode())
        expires = time.time() + ttl
        if size > self.disk_limit:
            return
        with self.store.tx():
            self.store.db.execute(
                "INSERT OR REPLACE INTO cache_entries VALUES(?,?,?,?,?,?)",
                (key, kind, payload, expires, size, time.time()),
            )
            self.store.db.execute(
                "DELETE FROM cache_entries WHERE expires<=?", (time.time(),)
            )
            while (
                self.store.one("SELECT COALESCE(SUM(size),0) AS n FROM cache_entries")[
                    "n"
                ]
                > self.disk_limit
            ):
                self.store.db.execute(
                    "DELETE FROM cache_entries WHERE key=(SELECT key FROM cache_entries ORDER BY accessed LIMIT 1)"
                )
        self.memory[key] = (expires, value)
        while len(self.memory) > self.limit:
            self.memory.popitem(last=False)

    async def derive(self, key, kind, producer, ttl=3600):
        value = self.get(key)
        if value is not None:
            return value
        if key in self.pending:
            return await asyncio.shield(self.pending[key])

        async def produce():
            value = await producer()
            self.put(key, kind, value, ttl)
            return value

        task = asyncio.create_task(produce())
        self.pending[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            self.pending.pop(key, None)

    def clear(self):
        self.memory.clear()
        self.store.db.execute("DELETE FROM cache_entries")


def legacy_preview(paths):
    """Read-only: preserve source payloads and report conflicts, never guess provenance."""
    sources = []
    merged = {}
    conflicts = []
    for path in paths:
        p = Path(path).expanduser().resolve()
        raw = json.loads(p.read_text())
        if not isinstance(raw, dict):
            raise ValueError("Legacy source root must be an object")
        sources.append(
            {
                "path": str(p),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "payload": raw,
            }
        )
        for k, v in raw.items():
            if k in merged and merged[k] != v:
                conflicts.append(k)
            else:
                merged[k] = v
    return {
        "sources": sources,
        "merged": merged,
        "conflicts": sorted(set(conflicts)),
        "history": [],
        "history_review_required": any(
            "applications" in item["payload"]
            or any(k.endswith("_application_2026") for k in item["payload"])
            for item in sources
        ),
        "confirmation_required": True,
    }


def import_legacy(store, preview, *, confirmed=False):
    with store.tx():
        return _import_legacy(store, preview, confirmed=confirmed)


def _import_legacy(store, preview, *, confirmed=False):
    if not confirmed:
        raise PermissionError("Approve the migration dry run first")
    if preview["conflicts"]:
        raise ValueError("Resolve source conflicts before importing")
    for source in preview["sources"]:
        if (
            hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
            != source["sha256"]
        ):
            raise ValueError("Source changed after dry run")
    for item in preview.get("history", []):
        if not all(item.get(k) for k in ("employer", "role", "identity", "url")):
            raise ValueError(
                "Historical records require reviewed employer/role/identity/URL"
            )
    payload = {
        **preview["merged"],
        "_confirmed_fields": preview.get("confirmed_fields", []),
        "_history_reconciliation_required": bool(
            preview.get("history_review_required")
            and not preview.get("history_reviewed")
        ),
    }
    version = store.profile(
        payload,
        provenance="legacy-import: historical provenance preserved; not blanket confirmation",
    )
    with store.tx():
        for source in preview["sources"]:
            store.db.execute(
                "INSERT OR IGNORE INTO legacy_imports VALUES(?,?,?,?)",
                (uid(), source["sha256"], encode(source), time.time()),
            )
    profile_id = store.one(
        "SELECT profile_id FROM profile_versions WHERE id=?", (version,)
    )["profile_id"]
    with store.tx():
        for item in preview.get("history", []):
            employer = digest([item["employer"], item.get("account", "")])
            vacancy = digest([item["identity"]])
            app = digest([vacancy, profile_id])
            store.db.execute(
                "INSERT OR IGNORE INTO employers VALUES(?,?,?)",
                (employer, item["employer"], item.get("account", "")),
            )
            store.db.execute(
                "INSERT OR IGNORE INTO vacancies(id,employer_id,role,identity,url,metadata) VALUES(?,?,?,?,?,?)",
                (
                    vacancy,
                    employer,
                    item["role"],
                    item["identity"],
                    item["url"],
                    encode(item),
                ),
            )
            # Old applied labels have historical uncertainty, never verified receipts.
            state = (
                "SUBMISSION_UNCONFIRMED"
                if item.get("status") in {"applied", "submitted", "completed_manually"}
                else "INCOMPLETE"
            )
            store.db.execute(
                "INSERT OR IGNORE INTO applications(id,vacancy_id,profile_id,account,state,updated,detail) VALUES(?,?,?,?,?,?,?)",
                (
                    app,
                    vacancy,
                    profile_id,
                    item.get("account", ""),
                    state,
                    time.time(),
                    "Imported historical record; original evidence retained",
                ),
            )
            store.event(app, "historical-import", {"original": item})
    return version
