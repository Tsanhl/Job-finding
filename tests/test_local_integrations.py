import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import pytest

from src.pilot.codex_bridge import discover
from src.pilot.jobsignal_import import preview, apply
from src.pilot.mcp_server import invoke
from src.pilot.runtime import Runtime
from src.pilot.store import Store
from src.pilot.tracking import EvidenceVault, MailTracking, DAY
from src.pilot.workspace import Workspace
from tests.test_local_workspace import PlainTestVault, job, message


def test_codex_sends_only_explicit_criteria_and_disables_other_tools(tmp_path):
    calls = []

    class Server:
        pending = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def rpc(self, method, params):
            calls.append((method, params))
            if method == "config/read":
                return {
                    "config": {
                        "mcp_servers": {"mail": {}},
                        "plugins": {"private_plugin": {}},
                    }
                }
            if method == "thread/start":
                return {"thread": {"id": "synthetic"}}
            if method == "turn/start":
                self.pending = [
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "text": '{"urls":["https://example.com/jobs"]}',
                            }
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {"turn": {"status": "completed"}},
                    },
                ]
                return {}

    result = asyncio.run(
        discover(
            {
                "query": "graduate engineering",
                "requested": 2,
                "profile": {"email": "private@example.test"},
            },
            tmp_path,
            Server,
        )
    )
    assert result["urls"] == ["https://example.com/jobs"]
    thread = next(p for m, p in calls if m == "thread/start")
    assert thread["config"]["mcp_servers.mail.enabled"] is False
    assert thread["config"]["plugins.private_plugin.enabled"] is False
    assert thread["sandbox"] == "read-only" and thread["ephemeral"]
    assert "private@example.test" not in json.dumps(calls)


def test_mcp_selected_fields_and_confirmation_boundaries():
    def send(request, home):
        assert request["op"] == "workspace_profile"
        return {
            "version": "v1",
            "payload": {
                "education": [{"institution": "Example"}],
                "email": "private@example.test",
            },
        }

    result = invoke("profile_fields", {"fields": ["education"]}, None, send)
    assert "email" not in result["fields"]
    with pytest.raises(ValueError):
        invoke("save_profile_fields", {"user_requested_save": False}, None, send)
    with pytest.raises(ValueError):
        invoke(
            "save_profile_fields",
            {
                "user_requested_save": True,
                "fields": {"_history_reconciliation_required": False},
            },
            None,
            send,
        )
    with pytest.raises(ValueError):
        invoke("mark_applied", {"user_reported": False}, None, send)


def test_evidence_encrypted_with_injected_secret_backend(tmp_path):
    class Backend:
        values = {}

        def _read(self, s, a):
            return self.values.get((s, a))

        def put(self, s, a, v, replace=False):
            self.values[s, a] = v

    store = Store(tmp_path / "test.sqlite3")
    try:
        vault = EvidenceVault(store, Backend())
        payload = {
            "subject": "Private invitation",
            "url": "https://example.test/assessment?token=private",
        }
        protected = vault.seal(payload)
        assert "Private" not in protected and "token" not in protected
        assert vault.open(protected) == payload
    finally:
        store.close()


def test_import_preview_backup_idempotency_and_no_profile_overwrite(tmp_path):
    source = tmp_path / "source.sqlite3"
    db = sqlite3.connect(source)
    db.executescript(
        "CREATE TABLE users(id,subject,display_name);CREATE TABLE profiles(user_id,body);CREATE TABLE searches(id,user_id,body);CREATE TABLE saved_jobs(user_id,job_id,state,note,updated_at);CREATE TABLE jobs(id,payload);"
    )
    db.execute(
        "INSERT INTO users VALUES(?,?,?)", ("old", "demo:synthetic", "Synthetic demo")
    )
    db.execute(
        "INSERT INTO profiles VALUES(?,?)", ("old", '{"display_name":"Synthetic demo"}')
    )
    db.execute(
        "INSERT INTO searches VALUES(?,?,?)",
        (
            "search",
            "old",
            '{"name":"Engineering","keywords":["engineering"],"countries":[]}',
        ),
    )
    db.execute(
        "INSERT INTO jobs VALUES(?,?)",
        (
            "job",
            json.dumps(
                {
                    "title": "Graduate Engineer",
                    "employer": "Example",
                    "source_url": "https://example.com/jobs/1",
                }
            ),
        ),
    )
    db.execute(
        "INSERT INTO saved_jobs VALUES(?,?,?,?,?)",
        ("old", "job", "applied", "Private historical note", 1000),
    )
    db.commit()
    db.close()
    store = Store(tmp_path / "destination.sqlite3")
    try:
        runtime = Runtime(store, None, testing=True)
        ws = runtime.workspace
        p = ws.profile()
        ws.save_profile({"full_name": "Existing Synthetic Owner"}, p["version"])
        check = preview(source, "old")
        assert check["demo"] and check["applied_count"] == 1
        with pytest.raises(ValueError):
            apply(
                runtime,
                {
                    "path": str(source),
                    "owner": "old",
                    "fingerprint": check["fingerprint"],
                    "confirmed": False,
                },
            )
        request = {
            "path": str(source),
            "owner": "old",
            "fingerprint": check["fingerprint"],
            "confirmed": True,
        }
        assert apply(runtime, request)["state"] == "IMPORTED"
        assert apply(runtime, request)["state"] == "ALREADY_IMPORTED"
        assert ws.profile()["payload"]["full_name"] == "Existing Synthetic Owner"
        assert ws.history()[0]["state"] == "SUBMITTED_USER_REPORTED"
        assert list((tmp_path / "backups").glob("*.sqlite3"))
        assert (
            sqlite3.connect(source)
            .execute("SELECT count(*) FROM saved_jobs")
            .fetchone()[0]
            == 1
        )
    finally:
        store.close()


def test_mail_cursor_failure_does_not_advance_past_unprocessed_message(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    ws = Workspace(store)

    class Fake:
        fail = True

        async def request(self, c, path, params=None):
            if path == "/profile":
                return {"historyId": "100"}
            if path == "/messages":
                return {"messages": [{"id": "m1"}, {"id": "m2"}]}
            if path.endswith("/m2") and self.fail:
                raise ConnectionError("synthetic loss")
            return message(id=path.rsplit("/", 1)[-1])

    try:
        ws.applied(job())
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
        fake = Fake()
        tracking = MailTracking(store, ws, fake, PlainTestVault(), clock=lambda: 1000)
        tracking.configure({"connection_id": "candidate@example.test", "enabled": True})

        async def run():
            with pytest.raises(ConnectionError):
                await tracking.sync("candidate@example.test")
            row = store.one("SELECT * FROM mail_tracking")
            assert row["history_id"] is None and row["lease_until"] == 0
            assert len(store.rows("SELECT * FROM recruitment_messages")) == 1
            fake.fail = False
            await tracking.sync("candidate@example.test")
            assert len(store.rows("SELECT * FROM recruitment_messages")) == 2
            assert (
                store.one("SELECT history_id FROM mail_tracking")["history_id"] == "100"
            )

        asyncio.run(run())
    finally:
        store.close()
