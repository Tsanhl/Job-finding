"""Real Gmail adapter exercised with intercepted HTTP; no mailbox or native secrets."""
import asyncio
import base64

import httpx
import pytest

from src.pilot.mail import Gmail, GmailError
from src.pilot.store import Store, encode
from src.pilot.tracking import parse_message
from tests.test_reliability_upgrade import tracker
from tests.test_local_workspace import message


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    yield db
    db.close()


def real_tracker(store, handler):
    gmail = Gmail(store, transport=httpx.MockTransport(handler))
    async def token(row):
        return "synthetic-token"
    gmail._token = token
    return tracker(store, gmail, [1000])


@pytest.mark.parametrize("code,reason,category,connected", [
    (403, "userRateLimitExceeded", "rate_limit", "CONNECTED"),
    (403, "rateLimitExceeded", "rate_limit", "CONNECTED"),
    (429, "", "rate_limit", "CONNECTED"),
    (503, "", "temporary", "CONNECTED"),
    (403, "insufficientPermissions", "permission", "CONNECTED"),
    (401, "authError", "authentication", "RECONNECT"),
])
def test_real_adapter_errors_schedule_without_losing_response(store, code, reason, category, connected):
    def handler(request):
        return httpx.Response(code, json={"error": {"errors": [{"reason": reason}]}},
                              headers={"Retry-After": "7200"})
    t = real_tracker(store, handler)
    with pytest.raises(GmailError) as caught:
        asyncio.run(t.sync("candidate@example.test"))
    assert caught.value.category == category
    assert caught.value.response.headers["Retry-After"] == "7200"
    assert store.one("SELECT status FROM mail_connections")["status"] == connected
    tracking = store.one("SELECT * FROM mail_tracking")
    assert tracking["lease_until"] == 0
    if category in {"rate_limit", "temporary"}:
        assert tracking["status"] == "RETRY_PENDING"
        assert tracking["next_due"] == 8200


@pytest.mark.parametrize("missing_format", ["metadata", "full"])
def test_missing_message_does_not_block_valid_message(store, missing_format):
    def handler(request):
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "1"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gone"}, {"id": "valid"}]})
        id = path.rsplit("/", 1)[-1]
        if id == "gone" and request.url.params["format"] == missing_format:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=message(id=id))
    t = real_tracker(store, handler)
    result = asyncio.run(t.sync("candidate@example.test"))
    assert result["processed"] == 2
    assert store.one("SELECT classification FROM recruitment_messages WHERE message_id='gone'")["classification"] == "UNAVAILABLE"
    assert store.one("SELECT classification FROM recruitment_messages WHERE message_id='valid'")["classification"] == "ASSESSMENT"
    assert store.one("SELECT sync_mode FROM mail_tracking")["sync_mode"] == "history"


def test_html_body_sanitized_and_deadline_preserved():
    m = message()
    m["payload"]["mimeType"] = "text/html"
    m["payload"]["body"]["data"] = base64.urlsafe_b64encode(
        b"<style>hidden</style><script>secret()</script><p>Online assessment invitation</p><p>Deadline: 14 September 2026</p>"
    ).decode()
    evidence = parse_message(m)
    assert evidence["deadline"] == "Deadline: 14 September 2026"
    assert "secret" not in evidence["excerpt"]
    assert "hidden" not in evidence["excerpt"]


def test_generic_subject_candidate_fetches_body_in_history(store):
    formats = []
    def handler(request):
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"historyId": "2", "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]})
        fmt = request.url.params["format"]
        formats.append(fmt)
        m = message(subject="Your next step")
        if fmt == "metadata":
            m["snippet"] = ""
            m["payload"].pop("body")
        return httpx.Response(200, json=m)
    t = real_tracker(store, handler)
    store.db.execute("UPDATE mail_tracking SET sync_mode='history',history_id='1'")
    asyncio.run(t.sync("candidate@example.test"))
    assert formats == ["metadata", "full"]
    assert store.one("SELECT classification FROM recruitment_messages")["classification"] == "ASSESSMENT"


def test_review_queue_filters_before_limit_and_pages(store):
    t = real_tracker(store, lambda _: httpx.Response(200, json={}))
    for i in range(650):
        kind = "ASSESSMENT" if i < 110 else "OTHER"
        resolved = int(i >= 100)
        store.db.execute("INSERT INTO recruitment_messages VALUES(?,?,?,?,?,?,?,?)",
                         (str(i), "candidate@example.test", str(i), None, kind,
                          encode({"subject": "Synthetic invitation"}), i, resolved))
    first = t.message_page(limit=60)
    second = t.message_page(limit=60, cursor=first["next_cursor"])
    assert len(first["items"]) == 60 and len(second["items"]) == 40
    assert {m["id"] for m in first["items"] + second["items"]} == {str(i) for i in range(100)}
    assert len(t.message_page("linked")["items"]) == 10


def test_stable_identity_reminder_preserves_completion_and_round(store):
    t = real_tracker(store, lambda _: httpx.Response(200, json={}))
    app = t.workspace.history()[0]["id"]
    def apply(id, subject, round):
        m = message(id=id, subject=subject, body=f"Assessment ID: coding-1 Round {round}")
        m["payload"]["headers"].append({"name": "From", "value": "tests@example.test"})
        evidence = parse_message(m)
        t.apply_evidence({"id": id, "classification": "ASSESSMENT"}, evidence, app)
    apply("a", "Online assessment invitation", 1)
    first = store.one("SELECT * FROM assessments")
    t.workspace.complete_assessment(first["id"], 1)
    apply("b", "Action required: Complete your online assessment", 1)
    assert len(store.rows("SELECT * FROM assessments")) == 1
    assert store.one("SELECT status FROM assessments")["status"] == "COMPLETED_USER_REPORTED"
    assert len(store.rows("SELECT * FROM assessment_evidence")) == 2
    apply("c", "Coding test second round", 2)
    assert len(store.rows("SELECT * FROM assessments")) == 2


def test_uncertain_reminder_requires_confirmation_and_alias_then_reuses(store):
    t = real_tracker(store, lambda _: httpx.Response(200, json={}))
    app = t.workspace.history()[0]["id"]
    first = t.workspace.assessment(app, "Online assessment invitation")["id"]
    t.workspace.complete_assessment(first, 1)
    evidence = parse_message(message(subject="Action required: Complete your online assessment"))
    for id in ["reminder1", "reminder2"]:
        store.db.execute("INSERT INTO recruitment_messages VALUES(?,?,?,?,?,?,?,0)",
                         (id, "candidate@example.test", id, None, "ASSESSMENT", encode(evidence), 1))
    assert t.apply_evidence({"id": "reminder1", "classification": "ASSESSMENT"}, evidence, app) is False
    assert t.resolve("reminder1", app, first)["linked"]
    t.apply_evidence({"id": "reminder2", "classification": "ASSESSMENT"}, evidence, app)
    assert len(store.rows("SELECT * FROM assessments")) == 1
    assert store.one("SELECT status FROM assessments")["status"] == "COMPLETED_USER_REPORTED"
    assert store.one("SELECT resolved FROM recruitment_messages WHERE id='reminder2'")["resolved"]


def test_unrelated_message_error_is_not_silently_skipped(store):
    def handler(request):
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "failed"}]})
        return httpx.Response(503, json={})
    t = real_tracker(store, handler)
    with pytest.raises(GmailError):
        asyncio.run(t.sync("candidate@example.test"))
    assert not store.rows("SELECT * FROM recruitment_messages")
    row = store.one("SELECT * FROM mail_tracking")
    assert row["sync_mode"] == "initial" and row["status"] == "RETRY_PENDING"


def test_history_does_not_fetch_unrelated_mail_body(store):
    formats = []
    def handler(request):
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"historyId": "2", "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]})
        formats.append(request.url.params["format"])
        m = message(subject="Weekly grocery specials", body="")
        m["snippet"] = "Fruit and vegetables"
        return httpx.Response(200, json=m)
    t = real_tracker(store, handler)
    store.db.execute("UPDATE mail_tracking SET sync_mode='history',history_id='1'")
    asyncio.run(t.sync("candidate@example.test"))
    assert formats == ["metadata"]
    row = store.one("SELECT * FROM recruitment_messages")
    assert row["classification"] == "OTHER"
    assert "grocery" not in row["protected_payload"]
    assert not t.messages()
