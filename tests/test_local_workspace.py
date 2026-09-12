import asyncio
import base64
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.pilot.dashboard import create_app
from src.pilot.runtime import Runtime
from src.pilot.store import Store
from src.pilot.tracking import MailTracking, DAY, parse_message
from src.pilot.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / "private.sqlite3")
    ws = Workspace(store)
    yield ws
    store.close()


def job(**kw):
    return {
        "identity": "example-2027-1",
        "role": "Graduate Software Engineer",
        "employer": "Example Employer",
        "url": "https://example.com/jobs/1",
        **kw,
    }


def test_expired_listing_unsave_and_restart_preserve_applied_and_assessment(workspace):
    w = workspace
    w.ingest([job()])
    app = w.applied(job())["application_id"]
    s = w.assessment(app, "Numerical test", "Three working days — check portal")
    w.complete_assessment(s["id"], 1)
    w.command(
        {
            "op": "workspace_job_action",
            "identity": job()["identity"],
            "action": "saved",
            "saved": False,
        }
    )
    w.store.db.execute("DELETE FROM opportunity_index")
    w.store.recover()
    rows = Workspace(w.store).history()
    assert rows[0]["state"] == "SUBMITTED_USER_REPORTED"
    assert rows[0]["assessments"][0]["status"] == "COMPLETED_USER_REPORTED"
    assert rows[0]["snapshot"]["role"] == job()["role"]
    assert w.applied(job())["application_id"] == app
    assert len(w.history()) == 1


def test_opened_newly_opened_and_applied_are_independent(workspace):
    today = datetime.now(timezone.utc).date().isoformat()
    workspace.ingest(
        [
            job(opening_date=today),
            job(identity="other", url="https://example.com/jobs/2", posted=today),
        ]
    )
    workspace.command(
        {
            "op": "workspace_job_action",
            "identity": job()["identity"],
            "action": "opened",
        }
    )
    rows = workspace.opportunities("new")
    assert len(rows) == 1 and rows[0]["opened"] and not rows[0]["applied"]
    assert workspace.history() == []


def test_profile_repeated_false_unknown_and_stale_edit(workspace):
    p = workspace.profile()
    payload = {
        "answers": {"driving_licence": False},
        "work_experience_none_confirmed": True,
        "custom": {"unknown": None},
        "education": [
            {"institution": "Example One", "degree": "BSc"},
            {"institution": "Example Two", "degree": "MSc"},
        ],
    }
    saved = workspace.save_profile(payload, p["version"])
    assert saved["payload"]["answers"]["driving_licence"] is False
    assert len({r["id"] for r in saved["payload"]["education"]}) == 2
    with pytest.raises(ValueError, match="changed"):
        workspace.save_profile({}, p["version"])
    assert workspace.profile()["payload"]["custom"]["unknown"] is None
    with pytest.raises(ValueError, match="credentials"):
        workspace.save_profile({"password": "never-store"}, saved["version"])


def test_ambiguous_legacy_owner_is_not_implicitly_selected(workspace):
    workspace.store.profile({"full_name": "Synthetic One"})
    chosen = workspace.store.profile({"full_name": "Synthetic Two"})
    assert workspace.profile()["selection_required"]
    workspace.select_profile(chosen)
    assert workspace.profile()["payload"]["full_name"] == "Synthetic Two"


def test_user_report_fences_running_work_and_dedupes_url_alias(workspace):
    from src.pilot.models import RunPlan

    p = workspace.profile()
    plan = RunPlan.parse(
        {
            "function": "AUTOFILL",
            "targets": [job()],
            "profile_version": p["version"],
            "permissions": ["fill", "session"],
            "expires_at": time.time() + 300,
            "approval": "synthetic test",
        }
    )
    _, apps = workspace.store.create_run(plan)
    app = apps[0]
    fence = workspace.store.acquire(app, "worker")
    assert fence
    result = workspace.applied(
        job(identity="another-alias", url="https://example.com/jobs/1?utm_source=demo")
    )
    assert result["application_id"] == app
    assert not workspace.store.rows("SELECT * FROM reservations")
    assert (
        workspace.store.one("SELECT fence FROM applications WHERE id=?", (app,))[
            "fence"
        ]
        > fence
    )


def test_assessment_components_and_completion_revision(workspace):
    app = workspace.applied(job())["application_id"]
    first = workspace.assessment(app, "Numerical test")
    workspace.assessment(app, "Video interview")
    with pytest.raises(ValueError, match="changed"):
        workspace.complete_assessment(first["id"], 99)
    workspace.complete_assessment(first["id"], 1)
    assert {r["status"] for r in workspace.history()[0]["assessments"]} == {
        "TO_DO",
        "COMPLETED_USER_REPORTED",
    }


def test_daily_config_does_not_connect_or_read_mail(workspace):
    workspace.store.db.execute(
        "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
        ("candidate@example.test", "candidate@example.test", "[]", "fake", "CONNECTED"),
    )
    tracking = MailTracking(workspace.store, workspace, clock=lambda: 1000)
    assert (
        tracking.configure(
            {"connection_id": "candidate@example.test", "enabled": True}
        )["interval_seconds"]
        == DAY
    )
    assert tracking.status()[0]["next_due"] == 1000
    with pytest.raises(ValueError):
        tracking.configure(
            {
                "connection_id": "candidate@example.test",
                "enabled": True,
                "lookback_days": 0,
            }
        )


class PlainTestVault:
    def seal(self, payload):
        return json.dumps(payload)

    def open(self, payload):
        return json.loads(payload)


def message(
    id="m1",
    body="Example Employer invites you to an assessment for Graduate Software Engineer. Complete within three working days.",
    subject="Assessment invitation",
):
    return {
        "id": id,
        "internalDate": "1000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "To", "value": "candidate@example.test"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def test_daily_mail_lease_idempotency_manual_sync_and_sleep(workspace):
    now = [1000]
    calls = []

    class FakeGmail:
        async def request(self, connection, path, params=None):
            calls.append(path)
            if path == "/profile":
                return {"historyId": "10"}
            if path == "/messages":
                return {"messages": [{"id": "m1"}]}
            if path == "/history":
                return {
                    "historyId": "11",
                    "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                }
            return message()

    workspace.applied(job())
    workspace.store.db.execute(
        "INSERT INTO mail_connections VALUES(?,?,?,?,?)",
        ("candidate@example.test", "candidate@example.test", "[]", "fake", "CONNECTED"),
    )
    tracker = MailTracking(
        workspace.store, workspace, FakeGmail(), PlainTestVault(), clock=lambda: now[0]
    )
    tracker.configure({"connection_id": "candidate@example.test", "enabled": True})

    async def run():
        await tracker.tick()
        assert len(workspace.history()[0]["assessments"]) == 1
        # Finish the initial scan by catching up from its captured history anchor.
        now[0] += 30
        await tracker.tick()
        assert tracker.status()[0]["status"] == "UP_TO_DATE"
        count = len(calls)
        now[0] += 300
        await tracker.tick()
        assert len(calls) == count
        # A manual sync is allowed before tomorrow and does not duplicate an invitation.
        await tracker.sync("candidate@example.test")
        assert len(workspace.history()[0]["assessments"]) == 1
        count = len(calls)
        now[0] += 4 * DAY
        await tracker.tick()
        assert len(calls) == count + 1
        assert tracker.status()[0]["next_due"] == now[0] + DAY

    asyncio.run(run())


def test_ambiguous_mail_goes_to_review_and_cannot_change_completed_component(workspace):
    workspace.applied(job())
    workspace.applied(job(identity="second", url="https://example.com/jobs/2"))
    tracker = MailTracking(workspace.store, workspace, vault=PlainTestVault())
    assert tracker.match(parse_message(message()), "candidate@example.test") is None
    assert (
        parse_message(message(body="You have not completed your assessment."))["kind"]
        == "ASSESSMENT"
    )


def test_loopback_auth_origin_csrf_and_no_public_profile(workspace):
    runtime = Runtime(workspace.store, None, testing=True)

    def send(request, home):
        return asyncio.run(runtime.command(request))

    app = create_app(
        workspace.store.path.parent,
        token="synthetic-launch-token",
        port=8502,
        call=send,
    )
    with TestClient(app, base_url="http://127.0.0.1:8502") as c:
        assert (
            c.post("/api/command", json={"op": "workspace_profile"}).status_code == 403
        )
        c.headers["Origin"] = "http://127.0.0.1:8502"
        assert (
            c.post("/api/command", json={"op": "workspace_profile"}).status_code == 401
        )
        auth = c.post("/api/unlock", json={"token": "synthetic-launch-token"}).json()
        assert (
            c.post("/api/command", json={"op": "workspace_profile"}).status_code == 403
        )
        c.headers["X-CSRF-Token"] = auth["csrf"]
        assert (
            c.post("/api/command", json={"op": "workspace_profile"}).status_code == 200
        )
        c.headers["Origin"] = "https://evil.example"
        assert (
            c.post("/api/command", json={"op": "workspace_profile"}).status_code == 403
        )
        assert (
            c.get("/api/session", headers={"Host": "evil.example"}).status_code == 403
        )


def test_runtime_profile_and_history_without_browser(workspace):
    runtime = Runtime(workspace.store, None, testing=True)

    async def run():
        assert (await runtime.command({"op": "capabilities"}))[
            "gmail_interval_seconds"
        ] == DAY
        assert (await runtime.command({"op": "workspace_profile"}))["version"]
        assert await runtime.command({"op": "workspace_history"}) == []
        assert not (await runtime.command({"op": "doctor"}))["browser_connected"]

    asyncio.run(run())
