"""Four edge-case regressions; private stores and secrets are synthetic only."""

import asyncio
import fcntl
import json
import time
from pathlib import Path

import httpx
import pytest

from src.pilot.store import Store, digest, encode
from src.pilot.workspace import Workspace, AssessmentIdentityError
from src.pilot.tracking import (
    MailTracking,
    EvidenceVault,
    CONTINUATION,
    DAY,
    RETRY_BASE,
)
from src.pilot.resources import Documents
from src.pilot.models import RunPlan
from src.pilot.recovery import backup_workspace, restore_workspace
from tests.test_local_workspace import job, message, PlainTestVault


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def _read(self, service, account):
        return self.values.get((service, account))

    def put(self, service, account, value, replace=False):
        assert replace or (service, account) not in self.values
        self.values[service, account] = value

    def delete(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "applypilot.sqlite3")
    yield db
    db.close()


def test_saved_jobs_filter_before_limit_and_keyset_pagination(store):
    ws = Workspace(store)
    with store.tx():
        for i in range(1205):
            store.db.execute(
                "INSERT INTO opportunity_index(identity,payload,first_seen,checked,saved) VALUES(?,?,?,?,?)",
                (
                    f"job-{i:04}",
                    encode(
                        job(identity=f"job-{i:04}", url=f"https://example.com/jobs/{i}")
                    ),
                    1,
                    i,
                    int(i < 205),
                ),
            )
    assert len(ws.opportunities("saved")) == 205
    cursor = None
    seen = []
    while True:
        page = ws.opportunity_page("saved", limit=80, cursor=cursor)
        seen.extend(x["identity"] for x in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 205
    assert seen[-1] == "job-0000"
    # Newer expired records cannot consume the limit and hide older live ones.
    store.db.execute(
        "UPDATE opportunity_index SET payload=json_set(payload,'$.deadline','2000-01-01') WHERE saved=0"
    )
    assert len(ws.opportunity_page("all", limit=100)["items"]) == 100
    with pytest.raises(ValueError):
        ws.opportunity_page("saved", limit=True)


def test_assessment_reminders_attach_evidence_and_keep_completion(store):
    ws = Workspace(store)
    app = ws.applied(job())["application_id"]
    first = ws.assessment(app, "Numerical test – round 1", "Friday", "message-1")["id"]
    ws.complete_assessment(first, 1)
    repeat = ws.assessment(
        app, "Reminder: Numerical test – round 1", "Monday", "message-2"
    )["id"]
    assert repeat == first
    assert (
        ws.assessment(app, "Reminder: Numerical test – round 1", "Monday", "message-2")[
            "id"
        ]
        == first
    )
    other = ws.assessment(app, "Numerical test – round 2", evidence="message-3")["id"]
    assert other != first
    a = next(x for x in ws.history()[0]["assessments"] if x["id"] == first)
    assert a["status"] == "COMPLETED_USER_REPORTED" and a["deadline"] == "Friday"
    assert len(a["evidence"]) == 2 and a["deadline_conflict"]
    assert len(store.rows("SELECT * FROM assessment_keys")) == 2


def test_legacy_assessment_identity_is_adopted_without_erasing_duplicates(store):
    ws = Workspace(store)
    app = ws.applied(job())["application_id"]
    store.db.execute(
        "INSERT INTO assessments(id,application_id,component,status,created) VALUES('old',?,'Coding test','COMPLETED_USER_REPORTED',1)",
        (app,),
    )
    assert (
        ws.assessment(app, "Reminder: Coding test", evidence="reminder")["id"] == "old"
    )
    store.db.execute(
        "INSERT INTO assessments(id,application_id,component,status,created) VALUES('old2',?,'Video interview','TO_DO',1)",
        (app,),
    )
    store.db.execute(
        "INSERT INTO assessments(id,application_id,component,status,created) VALUES('old3',?,'Video interview','TO_DO',1)",
        (app,),
    )
    with pytest.raises(AssessmentIdentityError):
        ws.assessment(app, "Video interview", evidence="reminder-2")
    assert len(store.rows("SELECT * FROM assessments")) == 3


def tracker(store, fake, now):
    ws = Workspace(store)
    ws.applied(job())
    store.db.execute(
        "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
        ("candidate@example.test", "candidate@example.test", "[]", "fake", "CONNECTED"),
    )
    t = MailTracking(store, ws, fake, PlainTestVault(), clock=lambda: now[0])
    t.configure({"connection_id": "candidate@example.test", "enabled": True})
    return t


def test_gmail_backlog_continues_across_restart_and_daily_only_after_catchup(store):
    now = [1000]
    calls = []

    class Gmail:
        async def request(self, c, path, params=None):
            calls.append((path, params))
            if path == "/profile":
                return {"historyId": "100"}
            if path == "/messages":
                return (
                    {"messages": [{"id": "m2"}]}
                    if params.get("pageToken")
                    else {"messages": [{"id": "m1"}], "nextPageToken": "second"}
                )
            if path == "/history":
                return {"historyId": "101"}
            return message(id=path.rsplit("/", 1)[-1])

    t = tracker(store, Gmail(), now)

    async def scenario():
        await t.tick()
        row = store.one("SELECT * FROM mail_tracking")
        assert row["page_token"] == "second" and row["last_success"] is None
        assert (
            row["last_page_success"] == 1000 and row["next_due"] == 1000 + CONTINUATION
        )
        restarted = MailTracking(
            store, t.workspace, t.gmail, PlainTestVault(), clock=lambda: now[0]
        )
        now[0] += CONTINUATION
        await restarted.tick()
        assert store.one("SELECT status FROM mail_tracking")["status"] == "BACKLOG"
        now[0] += CONTINUATION
        await restarted.tick()
        row = store.one("SELECT * FROM mail_tracking")
        assert row["status"] == "UP_TO_DATE" and row["history_id"] == "101"
        assert row["last_success"] == now[0] and row["next_due"] == now[0] + DAY
        count = len(calls)
        now[0] += 300
        await restarted.tick()
        assert len(calls) == count
        assert len(store.rows("SELECT * FROM assessments")) == 1
        assert len(store.rows("SELECT * FROM assessment_evidence")) == 2

    asyncio.run(scenario())


def test_gmail_transient_error_keeps_cursor_and_retries_without_waiting_day(store):
    now = [1000]

    class Gmail:
        async def request(self, c, path, params=None):
            raise httpx.HTTPStatusError(
                "unavailable",
                request=httpx.Request("GET", "https://example.test/"),
                response=httpx.Response(503, headers={"Retry-After": "120"}),
            )

    t = tracker(store, Gmail(), now)
    store.db.execute("UPDATE mail_tracking SET page_token='pending'")
    asyncio.run(t.tick())
    row = store.one("SELECT * FROM mail_tracking")
    assert row["page_token"] == "pending" and row["status"] == "RETRY_PENDING"
    assert row["next_due"] == 1120 and row["last_success"] is None
    now[0] = 1120
    asyncio.run(t.tick())
    assert store.one("SELECT failures FROM mail_tracking")["failures"] == 2


def test_expired_history_cursor_schedules_automatic_reconciliation(store):
    now = [1000]

    class Gmail:
        async def request(self, c, path, params=None):
            raise httpx.HTTPStatusError(
                "expired",
                request=httpx.Request("GET", "https://example.test/"),
                response=httpx.Response(404),
            )

    t = tracker(store, Gmail(), now)
    store.db.execute("UPDATE mail_tracking SET sync_mode='history',history_id='old'")
    asyncio.run(t.tick())
    row = store.one("SELECT * FROM mail_tracking")
    assert row["history_id"] is None and row["sync_mode"] == "initial"
    assert row["next_due"] == 1030 and row["status"] == "CURSOR_EXPIRED"


def test_complete_recovery_relocates_documents_key_and_uncertain_submission(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    store = Store(home / "applypilot.sqlite3")
    secrets = MemorySecrets()
    try:
        ws = Workspace(store)
        version = ws.profile()["version"]
        document = home / "resume.txt"
        document.write_text("Synthetic resume content")
        doc = Documents(store).register(document, "cv", approved=True)
        plan = RunPlan.parse(
            {
                "targets": [job()],
                "profile_version": version,
                "documents": [doc],
                "approval": "Synthetic approval",
                "expires_at": time.time() + 300,
            }
        )
        run, apps = store.create_run(plan)
        app = apps[0]
        store.db.execute(
            "UPDATE applications SET state='SUBMISSION_UNCONFIRMED',owner='old-worker' WHERE id=?",
            (app,),
        )
        store.db.execute(
            "INSERT INTO submission_attempts VALUES('attempt',?,?,'UNCONFIRMED','{}',1)",
            (app, run),
        )
        store.db.execute(
            "INSERT INTO checkpoints VALUES(?,?,?)",
            (app, '{"fields":["saved-field"]}', 1),
        )
        store.db.execute(
            "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
            (
                "candidate@example.test",
                "candidate@example.test",
                "[]",
                "fake",
                "CONNECTED",
            ),
        )
        vault = EvidenceVault(store, secrets)
        evidence = {
            "subject": "Synthetic invitation",
            "excerpt": "Private synthetic evidence",
            "deadline": "Friday",
        }
        sealed = vault.seal(evidence)
        store.db.execute(
            "INSERT INTO recruitment_messages VALUES('m','candidate@example.test','msg',?,'ASSESSMENT',?,1,1)",
            (app, sealed),
        )
        old_ref = vault.ref
    finally:
        store.close()
    bundle = tmp_path / "recovery.apbundle"
    password = "synthetic recovery phrase only"
    backup_workspace(home, bundle, password, backend=secrets)
    assert b"Synthetic resume" not in bundle.read_bytes()
    assert bundle.stat().st_mode & 0o777 == 0o600
    target = tmp_path / "restored"
    new_secrets = MemorySecrets()
    result = restore_workspace(bundle, target, password, backend=new_secrets)
    assert result["runs_paused"] and result["documents"] == 1
    restored = Store(target / "applypilot.sqlite3")
    try:
        vault = EvidenceVault(restored, new_secrets)
        assert vault.ref != old_ref
        assert (
            vault.open(
                restored.one("SELECT protected_payload FROM recruitment_messages")[
                    "protected_payload"
                ]
            )
            == evidence
        )
        row = restored.one("SELECT * FROM documents")
        assert Path(row["path"]).parent == target / "documents"
        assert Path(row["path"]).read_text() == "Synthetic resume content"
        assert row["approved"] == 1 and row["id"] == doc
        assert (
            restored.one("SELECT state FROM applications")["state"]
            == "SUBMISSION_UNCONFIRMED"
        )
        assert (
            restored.one("SELECT state FROM submission_attempts")["state"]
            == "UNCONFIRMED"
        )
        assert (
            restored.one("SELECT payload FROM checkpoints")["payload"]
            == '{"fields":["saved-field"]}'
        )
        assert restored.one("SELECT status FROM runs")["status"] == "PAUSED"
        assert restored.one("SELECT revoked FROM grants")["revoked"] == 1
        assert (
            restored.one("SELECT status FROM mail_connections")["status"] == "RECONNECT"
        )
    finally:
        restored.close()
    with pytest.raises(ValueError, match="new directory"):
        restore_workspace(bundle, target, password, backend=new_secrets)
    bad = tmp_path / "wrong-password"
    with pytest.raises(ValueError, match="incorrect"):
        restore_workspace(bundle, bad, "incorrect recovery phrase", backend=new_secrets)
    assert not bad.exists()
    tampered = tmp_path / "tampered.apbundle"
    data = bytearray(bundle.read_bytes())
    data[-5] ^= 1
    tampered.write_bytes(data)
    with pytest.raises(ValueError):
        restore_workspace(tampered, bad, password, backend=new_secrets)
    assert not bad.exists()


def test_backup_missing_document_or_lost_key_fails_without_bundle(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    store = Store(home / "applypilot.sqlite3")
    file = home / "doc.txt"
    file.write_text("Synthetic content")
    Documents(store).register(file, "cv", approved=True)
    store.close()
    file.unlink()
    dest = tmp_path / "bundle"
    with pytest.raises(ValueError, match="missing or changed"):
        backup_workspace(
            home, dest, "synthetic recovery passphrase", backend=MemorySecrets()
        )
    assert not dest.exists()


def test_evidence_missing_key_never_generates_replacement(store):
    backend = MemorySecrets()
    vault = EvidenceVault(store, backend)
    with pytest.raises(ValueError, match="unavailable"):
        vault.open("ciphertext")
    assert backend.values == {}


def test_recovery_refuses_running_source(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    store = Store(home / "applypilot.sqlite3")
    store.close()
    with (home / "runtime.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Stop the foreground"):
            backup_workspace(
                home,
                tmp_path / "bundle",
                "synthetic recovery passphrase",
                backend=MemorySecrets(),
            )


def test_backup_requires_existing_key_for_ciphertext(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    store = Store(home / "applypilot.sqlite3")
    backend = MemorySecrets()
    try:
        store.db.execute(
            "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
            (
                "candidate@example.test",
                "candidate@example.test",
                "[]",
                "fake",
                "CONNECTED",
            ),
        )
        ciphertext = EvidenceVault(store, backend).seal(
            {"subject": "Synthetic evidence"}
        )
        store.db.execute(
            "INSERT INTO recruitment_messages VALUES('m','candidate@example.test','msg',NULL,'ASSESSMENT',?,1,0)",
            (ciphertext,),
        )
    finally:
        store.close()
    with pytest.raises(ValueError, match="without its key"):
        backup_workspace(
            home,
            tmp_path / "missing-key-bundle",
            "synthetic recovery passphrase",
            backend=MemorySecrets(),
        )
    assert not (tmp_path / "missing-key-bundle").exists()


def test_ambiguous_reminder_can_link_to_selected_component(store):
    ws = Workspace(store)
    app = ws.applied(job())["application_id"]
    for id in ("a", "b"):
        store.db.execute(
            "INSERT INTO assessments(id,application_id,component,status,created) VALUES(?,?,'Coding test','TO_DO',1)",
            (id, app),
        )
    store.db.execute(
        "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
        ("candidate@example.test", "candidate@example.test", "[]", "fake", "CONNECTED"),
    )
    evidence = {"subject": "Coding test", "deadline": "Friday", "excerpt": "Synthetic"}
    store.db.execute(
        "INSERT INTO recruitment_messages VALUES('m','candidate@example.test','msg',NULL,'ASSESSMENT',?,1,0)",
        (json.dumps(evidence),),
    )
    tracking = MailTracking(store, ws, vault=PlainTestVault())
    assert tracking.resolve("m", app)["needs_assessment"]
    assert store.one("SELECT resolved FROM recruitment_messages")["resolved"] == 0
    assert tracking.resolve("m", app, "b")["linked"]
    assert (
        store.one("SELECT assessment_id FROM assessment_evidence")["assessment_id"]
        == "b"
    )
    assert len(store.rows("SELECT * FROM assessments")) == 2
