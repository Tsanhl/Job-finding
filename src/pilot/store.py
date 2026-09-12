"""Transactional state, immutable candidate versions and global execution fencing."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import apsw

from .models import DONE, RunPlan, State


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def default_home():
    return Path.home() / "Library/Application Support/ApplyPilot"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = apsw.Connection(str(self.path))
        os.chmod(self.path, 0o600)
        self.db.set_busy_timeout(3000)
        self.db.execute("PRAGMA foreign_keys=ON")
        if tuple(map(int, apsw.sqlitelibversion().split("."))) < (3, 53, 4):
            raise RuntimeError(
                "SQLite 3.53.4 or newer required; use upgrade environment"
            )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.set_row_trace(
            lambda c, row: dict(zip([x[0] for x in c.get_description()], row))
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL, applied REAL NOT NULL)"
        )
        for p in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            found = self.one(
                "SELECT * FROM schema_migrations WHERE version=?", (p.name,)
            )
            if found:
                if found["sha256"] != sha:
                    raise RuntimeError("Installed migration checksum mismatch")
                continue
            with self.tx():
                self.db.execute(p.read_text())
                self.db.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?)",
                    (p.name, sha, time.time()),
                )

    def close(self):
        self.db.close()

    def rows(self, sql, args=()):
        return list(self.db.execute(sql, args))

    def one(self, sql, args=()):
        return next(iter(self.db.execute(sql, args)), None)

    @contextmanager
    def tx(self):
        nested = not self.db.get_autocommit()
        savepoint = "nested_" + uid()
        self.db.execute("SAVEPOINT " + savepoint if nested else "BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK TO " + savepoint if nested else "ROLLBACK")
            if nested:
                self.db.execute("RELEASE " + savepoint)
            raise
        else:
            self.db.execute("RELEASE " + savepoint if nested else "COMMIT")

    def event(self, app, kind, payload):
        self.db.execute(
            "INSERT INTO events(application_id,kind,payload,created) VALUES(?,?,?,?)",
            (app, kind, encode(payload), time.time()),
        )

    def profile(self, payload, *, parent=None, provenance="user-confirmed"):
        if not isinstance(payload, dict):
            raise TypeError("Profile must be an object")
        old = (
            self.one("SELECT * FROM profile_versions WHERE id=?", (parent,))
            if parent
            else None
        )
        if parent and not old:
            raise ValueError("Unknown profile parent")
        payload = json.loads(encode(payload))
        previous = json.loads(old["payload"]) if old else {}
        identity_keys = (
            "employer",
            "title",
            "position",
            "institution",
            "school",
            "degree",
            "start",
            "end",
        )
        for kind in (
            "work_experience",
            "education",
            "references",
            "school_qualifications",
        ):
            records = payload.get(kind)
            if not isinstance(records, list):
                continue
            old_records = previous.get(kind, [])
            if not isinstance(old_records, list):
                old_records = []
            used = set()
            for record in records:
                if not isinstance(record, dict):
                    continue
                if not record.get("id"):
                    matches = [
                        r
                        for r in old_records
                        if isinstance(r, dict)
                        and all(r.get(k) == record.get(k) for k in identity_keys)
                    ]
                    record["id"] = (
                        matches[0].get("id", uid()) if len(matches) == 1 else uid()
                    )
                if record["id"] in used:
                    raise ValueError("Repeated profile record IDs must be unique")
                used.add(record["id"])
        profile_id = old["profile_id"] if old else uid()
        version = uid()
        with self.tx():
            self.db.execute(
                "INSERT OR IGNORE INTO profiles VALUES(?,?)", (profile_id, time.time())
            )
            self.db.execute(
                "INSERT INTO profile_versions VALUES(?,?,?,?,?,?)",
                (version, profile_id, parent, encode(payload), provenance, time.time()),
            )
            for kind, records in payload.items():
                if isinstance(records, list):
                    for index, record in enumerate(records):
                        key = (
                            str(record.get("id", digest([kind, index, record])))
                            if isinstance(record, dict)
                            else digest([kind, index, record])
                        )
                        self.db.execute(
                            "INSERT INTO profile_records VALUES(?,?,?,?,?)",
                            (uid(), version, kind, key, encode(record)),
                        )
        return version

    def load_profile(self, version):
        row = self.one("SELECT * FROM profile_versions WHERE id=?", (version,))
        if not row:
            raise ValueError("Unknown profile version")
        return json.loads(row["payload"])

    def create_run(self, plan: RunPlan):
        profile = self.one(
            "SELECT * FROM profile_versions WHERE id=?", (plan.profile_version,)
        )
        if not profile:
            raise ValueError("Unknown profile version")
        run = uid()
        apps = []
        with self.tx():
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (run, encode(plan.json()), "ACTIVE", time.time()),
            )
            self.db.execute(
                "INSERT INTO grants VALUES(?,?,?,?,?)",
                (uid(), run, plan.expires_at, 0, plan.approval),
            )
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,?)", (uid(), run, plan.workers)
            )
            for t in plan.targets:
                employer = digest([t.employer, t.account])
                vacancy = digest([t.identity])
                app = digest([vacancy, profile["profile_id"]])
                self.db.execute(
                    "INSERT OR IGNORE INTO employers VALUES(?,?,?)",
                    (employer, t.employer, t.account),
                )
                existing = self.one("SELECT * FROM vacancies WHERE id=?", (vacancy,))
                if existing and (
                    existing["employer_id"] != employer or existing["role"] != t.role
                ):
                    raise ValueError(
                        "Application identity conflict; review fallback identity"
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO vacancies(id,employer_id,role,identity,url,eligibility) VALUES(?,?,?,?,?,?)",
                    (vacancy, employer, t.role, t.identity, t.url, t.eligibility),
                )
                existing_app = self.one("SELECT * FROM applications WHERE id=?", (app,))
                if existing_app and existing_app["account"] != t.account:
                    raise ValueError("Existing application account mismatch")
                if existing_app and existing_app["state"] in {str(s) for s in DONE} | {
                    "SUBMITTING",
                    "SUBMISSION_UNCONFIRMED",
                    "FILLING",
                }:
                    raise ValueError(
                        "Application already completed, owned or awaiting reconciliation"
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO applications(id,vacancy_id,profile_id,account,state,updated) VALUES(?,?,?,?,?,?)",
                    (
                        app,
                        vacancy,
                        profile["profile_id"],
                        t.account,
                        "QUEUED",
                        time.time(),
                    ),
                )
                self.db.execute(
                    "INSERT INTO run_applications VALUES(?,?,?,?)",
                    (run, app, plan.profile_version, encode(asdict(t))),
                )
                for doc in plan.documents:
                    if not self.one(
                        "SELECT id FROM documents WHERE id=? AND approved=1", (doc,)
                    ):
                        raise ValueError("Unknown or unapproved document")
                    self.db.execute(
                        "INSERT OR IGNORE INTO application_documents VALUES(?,?)",
                        (app, doc),
                    )
                self.db.execute(
                    "INSERT INTO tasks(id,run_id,application_id,kind,status) VALUES(?,?,?,?,?)",
                    (uid(), run, app, "INSPECT_FORM", "QUEUED"),
                )
                self.event(app, "run-authorised", {"run": run})
                apps.append(app)
        return run, apps

    def add_target(self, run, plan: RunPlan, target):
        """Append a bounded discovered target to an existing authorised run."""

        stored = self.one("SELECT plan,status FROM runs WHERE id=?", (run,))
        profile = self.one(
            "SELECT * FROM profile_versions WHERE id=?", (plan.profile_version,)
        )
        if not stored or stored["status"] != "ACTIVE" or not profile:
            raise ValueError("LinkedIn batch run is unavailable")
        employer = digest([target.employer, target.account])
        vacancy = digest([target.identity])
        app = digest([vacancy, profile["profile_id"]])
        with self.tx():
            self.db.execute(
                "INSERT OR IGNORE INTO employers VALUES(?,?,?)",
                (employer, target.employer, target.account),
            )
            existing = self.one("SELECT * FROM vacancies WHERE id=?", (vacancy,))
            if existing and (
                existing["employer_id"] != employer or existing["role"] != target.role
            ):
                raise ValueError("Application identity conflict")
            self.db.execute(
                "INSERT OR IGNORE INTO vacancies(id,employer_id,role,identity,url,eligibility) VALUES(?,?,?,?,?,?)",
                (
                    vacancy,
                    employer,
                    target.role,
                    target.identity,
                    target.url,
                    target.eligibility,
                ),
            )
            existing_app = self.one("SELECT * FROM applications WHERE id=?", (app,))
            if existing_app:
                raise ValueError("Application already exists")
            self.db.execute(
                "INSERT OR IGNORE INTO applications(id,vacancy_id,profile_id,account,state,updated) VALUES(?,?,?,?,?,?)",
                (
                    app,
                    vacancy,
                    profile["profile_id"],
                    target.account,
                    "QUEUED",
                    time.time(),
                ),
            )
            self.db.execute(
                "INSERT INTO run_applications VALUES(?,?,?,?)",
                (run, app, plan.profile_version, encode(asdict(target))),
            )
            for doc in plan.documents:
                if not self.one(
                    "SELECT id FROM documents WHERE id=? AND approved=1", (doc,)
                ):
                    raise ValueError("Unknown or unapproved document")
                self.db.execute(
                    "INSERT OR IGNORE INTO application_documents VALUES(?,?)",
                    (app, doc),
                )
            self.db.execute(
                "INSERT INTO tasks(id,run_id,application_id,kind,status) VALUES(?,?,?,?,?)",
                (uid(), run, app, "INSPECT_FORM", "QUEUED"),
            )
            self.event(app, "run-authorised", {"run": run})
        return app

    def acquire(self, app, owner, account=""):
        with self.tx():
            row = self.one("SELECT * FROM applications WHERE id=?", (app,))
            if (
                not row
                or row["owner"]
                or row["state"]
                in {str(s) for s in DONE} | {"SUBMISSION_UNCONFIRMED", "SUBMITTING"}
            ):
                return None
            accounts = (
                [account] if isinstance(account, str) and account else (account or [])
            )
            if any(
                self.one("SELECT * FROM account_locks WHERE account=?", (key,))
                for key in accounts
            ):
                return None
            used = {r["slot"] for r in self.rows("SELECT slot FROM reservations")}
            slot = next((n for n in range(1, 11) if n not in used), None)
            if slot is None:
                return None
            fence = row["fence"] + 1
            expires = time.time() + 30
            self.db.execute(
                "INSERT INTO reservations VALUES(?,?,?,?,?)",
                (slot, app, owner, fence, expires),
            )
            self.db.execute(
                "UPDATE applications SET owner=?,fence=?,lease_until=?,state=?,updated=? WHERE id=?",
                (owner, fence, expires, "FILLING", time.time(), app),
            )
            for key in accounts:
                self.db.execute(
                    "INSERT INTO account_locks VALUES(?,?,?,?)",
                    (key, app, owner, fence),
                )
            self.event(app, "acquired", {"fence": fence, "slot": slot})
        return fence

    def guard(self, app, run, owner, fence):
        row = self.one(
            "SELECT a.*,r.status AS run_status,g.expires,g.revoked FROM applications a JOIN run_applications ra ON ra.application_id=a.id JOIN runs r ON r.id=ra.run_id JOIN grants g ON g.run_id=r.id WHERE a.id=? AND r.id=?",
            (app, run),
        )
        if (
            not row
            or row["owner"] != owner
            or row["fence"] != fence
            or (row["lease_until"] or 0) <= time.time()
        ):
            raise PermissionError("Execution ownership expired")
        if (
            row["run_status"] != "ACTIVE"
            or row["revoked"]
            or row["expires"] <= time.time()
            or row["state"] in {str(s) for s in DONE} | {"CANCELLED"}
        ):
            raise PermissionError("Execution stopped or approval expired")

    def heartbeat(self, app, owner, fence):
        with self.tx():
            self.db.execute(
                "UPDATE applications SET lease_until=? WHERE id=? AND owner=? AND fence=?",
                (time.time() + 30, app, owner, fence),
            )
            self.db.execute(
                "UPDATE reservations SET expires=? WHERE application_id=? AND owner=? AND fence=?",
                (time.time() + 30, app, owner, fence),
            )

    def checkpoint(self, app, run, owner, fence, fields, documents):
        with self.tx():
            self.guard(app, run, owner, fence)
            self.db.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)",
                (
                    app,
                    encode(
                        {"state": "FILLING", "fields": fields, "documents": documents}
                    ),
                    time.time(),
                ),
            )
            self.db.execute(
                "UPDATE applications SET updated=?,detail='Saving verified filling progress' WHERE id=?",
                (time.time(), app),
            )

    def finish(self, app, owner, fence, result):
        with self.tx():
            row = self.one("SELECT * FROM applications WHERE id=?", (app,))
            if not row or row["owner"] != owner or row["fence"] != fence:
                return
            state = result.state
            if (
                row["state"] in {"SUBMITTING", "SUBMISSION_UNCONFIRMED"}
                and state not in DONE
            ):
                state = State.SUBMISSION_UNCONFIRMED
            if row["state"] in {str(s) for s in DONE}:
                state = row["state"]
            self.db.execute(
                "UPDATE applications SET state=?,detail=?,owner=NULL,lease_until=NULL,updated=? WHERE id=?",
                (str(state), result.detail, time.time(), app),
            )
            self.db.execute(
                "DELETE FROM reservations WHERE application_id=? AND owner=? AND fence=?",
                (app, owner, fence),
            )
            self.db.execute(
                "DELETE FROM account_locks WHERE application_id=? AND owner=? AND fence=?",
                (app, owner, fence),
            )
            previous = self.one(
                "SELECT payload FROM checkpoints WHERE application_id=?", (app,)
            )
            if previous:
                saved = json.loads(previous["payload"])
                result.fields = result.fields or saved.get("fields", [])
                result.documents = result.documents or saved.get("documents", [])
            result.state = State(state)
            self.db.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)",
                (app, encode(asdict(result)), time.time()),
            )
            self.db.execute(
                "INSERT INTO validations VALUES(?,?,?,?)",
                (uid(), app, encode(asdict(result)), time.time()),
            )
            self.db.execute(
                "UPDATE missing_questions SET resolved=1 WHERE application_id=?", (app,)
            )
            self.db.execute(
                "INSERT INTO field_manifests VALUES(?,?,?,?)",
                (uid(), app, encode(result.fields), time.time()),
            )
            for doc in result.documents:
                if doc.get("saved"):
                    self.db.execute(
                        "INSERT INTO upload_evidence VALUES(?,?,?,?,?)",
                        (uid(), app, doc["document_id"], encode(doc), time.time()),
                    )
            for q in result.blockers:
                key = digest(
                    {
                        k: q.get(k, "")
                        for k in (
                            "question",
                            "record",
                            "country",
                            "employer",
                            "role",
                            "identity",
                            "kind",
                        )
                    }
                )
                self.db.execute(
                    "INSERT INTO missing_questions VALUES(?,?,?,?,0) ON CONFLICT(application_id,question_key) DO UPDATE SET payload=excluded.payload,resolved=0",
                    (uid(), app, key, encode(q)),
                )
            self.event(
                app,
                "checkpoint",
                {"state": str(state), "blocker_count": len(result.blockers)},
            )

    def user_submitted(self, app):
        with self.tx():
            row = self.one("SELECT * FROM applications WHERE id=?", (app,))
            if not row:
                raise ValueError("Unknown application")
            if row["state"] in {
                "SUBMITTED_CONFIRMED",
                "SUBMITTED_USER_REPORTED",
            }:
                return
            self.db.execute(
                "UPDATE applications SET state=?,detail=?,fence=fence+1,owner=NULL,lease_until=NULL,updated=? WHERE id=?",
                (
                    "SUBMITTED_USER_REPORTED",
                    "Done — user reported",
                    time.time(),
                    app,
                ),
            )
            self.db.execute("DELETE FROM reservations WHERE application_id=?", (app,))
            self.db.execute("DELETE FROM account_locks WHERE application_id=?", (app,))
            self.db.execute(
                "UPDATE tasks SET status='DONE' WHERE application_id=?", (app,)
            )
            self.db.execute(
                "UPDATE missing_questions SET resolved=1 WHERE application_id=?", (app,)
            )
            self.db.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                (
                    uid(),
                    None,
                    app,
                    "user",
                    "user-confirmed",
                    encode({"statement": "I submitted it"}),
                    time.time(),
                ),
            )
            self.event(app, "user-reported-submission", {})

    def recover(self):
        # Called only after acquiring the exclusive runtime OS lock. Never infer
        # that a remote side effect failed merely because a lease expired.
        with self.tx():
            for row in self.rows("SELECT * FROM applications WHERE owner IS NOT NULL"):
                state = (
                    "SUBMISSION_UNCONFIRMED"
                    if row["state"] == "SUBMITTING"
                    else "INCOMPLETE"
                )
                self.db.execute(
                    "UPDATE applications SET state=?,owner=NULL,lease_until=NULL,fence=fence+1,detail=? WHERE id=?",
                    (
                        state,
                        "Runtime restarted; reconcile the selected form before resuming",
                        row["id"],
                    ),
                )
                self.event(row["id"], "recovery-required", {})
            self.db.execute("DELETE FROM reservations")
            self.db.execute("DELETE FROM account_locks")
            self.db.execute("UPDATE tasks SET status='PARKED' WHERE status='RUNNING'")
            self.db.execute(
                "UPDATE submission_attempts SET state='UNCONFIRMED' WHERE state='INTENT'"
            )

    def snapshot(self):
        apps = self.rows(
            "SELECT a.*,v.role,e.name AS employer FROM applications a JOIN vacancies v ON v.id=a.vacancy_id JOIN employers e ON e.id=v.employer_id ORDER BY a.updated DESC"
        )
        grouped = {}
        for row in self.rows("SELECT * FROM missing_questions WHERE resolved=0"):
            q = json.loads(row["payload"])
            key = row["question_key"]
            grouped.setdefault(key, {**q, "key": key, "applications": []})[
                "applications"
            ].append(row["application_id"])
        return {
            "applications": apps,
            "questions": list(grouped.values()),
            "active_workers": len(self.rows("SELECT slot FROM reservations")),
            "retained_contexts": len(
                self.rows("SELECT DISTINCT context_id FROM sessions WHERE retained=1")
            ),
            "runs": self.rows("SELECT * FROM runs ORDER BY created DESC"),
        }

    def backup(self, destination):
        dest = Path(destination).expanduser().resolve()
        if dest.exists():
            raise ValueError("Backup destination already exists")
        conn = apsw.Connection(str(dest))
        try:
            with conn.backup("main", self.db, "main") as backup:
                backup.step(-1)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("Backup integrity failure")
        finally:
            conn.close()
        os.chmod(dest, 0o600)
        return str(dest)

    def doctor(self):
        return {
            "sqlite": apsw.sqlitelibversion(),
            "journal": self.one("PRAGMA journal_mode"),
            "foreign_keys": self.one("PRAGMA foreign_keys"),
            "synchronous": self.one("PRAGMA synchronous"),
            "integrity": self.one("PRAGMA quick_check"),
            "migrations": self.rows("SELECT version FROM schema_migrations"),
        }
