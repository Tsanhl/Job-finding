import asyncio
import json
import time

import pytest
from playwright.async_api import async_playwright

from src.application_ledger import ApplicationLedger, LedgerCorruptionError
from src.field_manifest import FieldKind, classify_field
from src.pilot.engine import Engine
from src.pilot.mail import EmailTransactions
from src.pilot.models import Result, RunPlan, State
from src.pilot.resources import Documents, legacy_preview
from src.pilot.store import Store


def plan(version, url="http://127.0.0.1/form", **kw):
    return RunPlan.parse(
        {
            "targets": [
                {
                    "url": url,
                    "employer": "E",
                    "role": "R",
                    "identity": "r",
                    "scope": "CURRENT_PAGE",
                }
            ],
            "profile_version": version,
            "approval": "Synthetic",
            "expires_at": time.time() + 120,
            **kw,
        }
    )


@pytest.mark.parametrize(
    "payload", [[], {"applications": {"https://example.test/form": "broken"}}]
)
def test_malformed_history_never_becomes_empty(tmp_path, payload):
    f = tmp_path / "ledger"
    f.write_text(json.dumps(payload))
    with pytest.raises(LedgerCorruptionError):
        ApplicationLedger(f).records()


def test_signature_name_precedence():
    assert (
        classify_field("Full name", section="Electronic signature")
        == FieldKind.SIGNATURE
    )


def test_global_capacity_and_account_locks(tmp_path):
    s = Store(tmp_path / "db")
    other = Store(tmp_path / "db")
    v = s.profile({})
    apps = []
    for n in range(11):
        p = plan(
            v,
            targets=[
                {
                    "url": f"https://example.test/{n}",
                    "employer": "E",
                    "role": "R",
                    "identity": str(n),
                }
            ],
        )
        _, ids = s.create_run(p)
        apps += ids
    for app in apps[:10]:
        assert s.acquire(app, "one") is not None
    assert other.acquire(apps[10], "two") is None
    app = apps[0]
    f = s.one("SELECT fence FROM applications WHERE id=?", (app,))["fence"]
    s.finish(app, "one", f, Result(State.INCOMPLETE))
    assert other.acquire(apps[10], "two", ["account", "context"]) is not None
    assert s.acquire(app, "three", ["account"]) is None
    other.close()
    s.close()


def test_crash_recovery_never_retries_uncertain_submission(tmp_path):
    s = Store(tmp_path / "db")
    v = s.profile({})
    run, apps = s.create_run(plan(v))
    app = apps[0]
    s.acquire(app, "old")
    s.db.execute("UPDATE applications SET state='SUBMITTING' WHERE id=?", (app,))
    s.recover()
    assert s.one("SELECT state FROM applications")["state"] == "SUBMISSION_UNCONFIRMED"
    assert s.acquire(app, "new") is None
    s.close()


def test_profile_history_immutable_and_migration_preserves_sources(tmp_path):
    s = Store(tmp_path / "db")
    old = s.profile({"custom": {"unknown": "preserved"}})
    s.profile({"custom": {"unknown": "new"}}, parent=old)
    assert s.load_profile(old)["custom"]["unknown"] == "preserved"
    with pytest.raises(Exception):
        s.db.execute("UPDATE profile_versions SET payload=? WHERE id=?", ("{}", old))
    source = tmp_path / "profile.json"
    source.write_text('{"custom":{"uncertain":null}}')
    before = source.read_bytes()
    preview = legacy_preview([source])
    assert source.read_bytes() == before
    assert preview["merged"]["custom"]["uncertain"] is None
    s.close()


def test_browser_safe_navigation_optional_fields_and_user_edits(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic"})
        engine = Engine(Documents(s))
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    body="<html></html>", content_type="text/html"
                ),
            )
            await page.goto("http://127.0.0.1/form")
            await page.set_content(
                """<form onsubmit="event.preventDefault();window.submits=(window.submits||0)+1"><label for="full">Full name</label><input id="full" required><fieldset><legend>Electronic signature</legend><label for="sig">Full name</label><input id="sig" required></fieldset><label for="optional">Gender</label><input id="optional"><button>Next</button></form>"""
            )
            result = await engine.fill(
                page, plan(v), plan(v).targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.NEEDS_INFORMATION
            assert await page.locator("#full").input_value() == "Synthetic"
            assert await page.locator("#sig").input_value() == ""
            assert not await page.evaluate("window.submits||0")
            assert len(result.blockers) == 1
            await page.locator("#sig").fill("Synthetic")
            result = await engine.fill(
                page, plan(v), plan(v).targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED
            assert not await page.evaluate("window.submits||0")
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_email_recipient_sender_time_and_replay(tmp_path):
    s = Store(tmp_path / "db")
    v = s.profile({})
    run, apps = s.create_run(plan(v))
    email = EmailTransactions(s, None)
    p = plan(v, workflow="AUTO_APPLY", permissions=["fill", "mail"])
    id = email.begin(
        apps[0],
        "candidate@example.test",
        "candidate@example.test",
        "tenant",
        "activation",
        {
            "senders": ["auth@employer.test"],
            "hosts": ["employer.test"],
            "paths": ["/activate"],
            "subject": "Activate",
        },
        p,
    )
    tx = s.one("SELECT * FROM email_transactions WHERE id=?", (id,))
    message = {
        "internalDate": str(int(time.time() * 1000)),
        "payload": {
            "headers": [
                {"name": "From", "value": "auth@employer.test"},
                {"name": "To", "value": "candidate@example.test"},
                {"name": "Subject", "value": "Activate your account"},
                {
                    "name": "Authentication-Results",
                    "value": "mx.google.com; dkim=pass header.d=employer.test;",
                },
            ]
        },
    }
    assert email.matches(tx, message)
    message["payload"]["headers"][1]["value"] = "someoneelse@example.test"
    assert not email.matches(tx, message)
    message["payload"]["headers"][1]["value"] = "candidate@example.test"
    message["internalDate"] = "0"
    assert not email.matches(tx, message)
    s.close()


def test_workday_submission_contract_requires_identity_and_receipt(tmp_path):
    async def scenario():
        from src.pilot.submission import SubmitService

        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic"})
        p = plan(
            v,
            url="https://tenant.myworkdayjobs.com/job/REQ-123",
            workflow="AUTO_APPLY",
            targets=[
                {
                    "url": "https://tenant.myworkdayjobs.com/job/REQ-123",
                    "employer": "E",
                    "role": "Graduate",
                    "identity": "REQ-123",
                    "account": "candidate@example.test",
                    "eligibility": "eligible",
                }
            ],
            permissions=["fill", "session", "submit"],
            submission_policy="submit",
        )
        run, apps = s.create_run(p)
        app = apps[0]
        fence = s.acquire(app, "test")
        s.db.execute(
            "INSERT INTO adapter_qualifications VALUES(?,?,?,?)",
            ("workday", "submit", "SYNTHETIC", "local contract test"),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.route(
                "**/*", lambda route: route.fulfill(content_type="text/html", body="")
            )
            await page.goto(p.targets[0].url)
            await page.set_content(
                """<main data-automation-id="reviewPage"><h1>Graduate</h1><span>candidate@example.test</span><button type="button" data-automation-id="bottom-navigation-next-button" onclick="document.getElementById('receipt').innerHTML='<p data-automation-id=applicationSubmitted>Application submitted</p><p data-automation-id=applicationId>APP-987</p>'">Submit</button><div id="receipt"></div></main>"""
            )
            result = await SubmitService(s, Documents(s), testing=True).submit(
                page,
                p,
                p.targets[0],
                app,
                run,
                Result(State.REVIEW_READY),
                lambda: s.guard(app, run, "test", fence),
            )
            assert result.state == State.SUBMITTED_CONFIRMED
            assert result.detail == "APP-987"
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_repeatable_records_saved_and_not_duplicated(tmp_path):
    async def scenario():
        from src.pilot.adapters import FIXTURE
        from src.pilot.records import Records

        s = Store(tmp_path / "db")
        v = s.profile({})
        p = plan(v)
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            page = await browser.new_page()
            await page.set_content("""<section data-record-section="work_experience"><button type="button" data-action="add-record">Add</button><div id="records"></div><div id="editor"></div></section><script>
            document.querySelector('[data-action=add-record]').onclick=()=>{editor.innerHTML='<div data-record-editor><input name="record_id"><input name="employer"><input name="title"><button type="button" data-action="save-record">Save</button></div>';document.querySelector('[data-action=save-record]').onclick=()=>{const row=document.createElement('div');row.dataset.savedRecord='true';row.dataset.recordId=document.querySelector('[name=record_id]').value;row.dataset.values=JSON.stringify({employer:document.querySelector('[name=employer]').value,title:document.querySelector('[name=title]').value});records.append(row);editor.innerHTML=''}};
            </script>""")
            profile = {
                "work_experience": [
                    {"id": "one", "employer": "Employer A", "title": "Intern"},
                    {"id": "two", "employer": "Employer B", "title": "Assistant"},
                ]
            }
            for _ in range(2):
                assert not await Records(FIXTURE).reconcile(
                    page, profile, lambda: None, p
                )
            assert await page.locator("[data-saved-record]").count() == 2
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_restore_and_cache_preserve_private_sentinels(tmp_path):
    from src.pilot.maintenance import restore

    sentinel = tmp_path / "private-original.pdf"
    sentinel.write_bytes(b"unchanged private sentinel")
    home = tmp_path / "runtime"
    s = Store(home / "applypilot.sqlite3")
    v = s.profile({"full_name": "Synthetic"})
    backup = tmp_path / "backup.db"
    s.backup(backup)
    s.close()
    restore(backup, home, confirmed=True)
    recovered = Store(home / "applypilot.sqlite3")
    assert recovered.load_profile(v)["full_name"] == "Synthetic"
    recovered.close()
    assert sentinel.read_bytes() == b"unchanged private sentinel"
    assert list(home.glob("before-restore-*"))


def test_country_scoped_rights_custom_widget_and_radio_groups(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile(
            {
                "address": {"country": "United Kingdom"},
                "work_rights": {"GB": {"require_sponsorship": "No"}},
                "answers": {"require_sponsorship": "Yes"},
            }
        )
        p = plan(
            v,
            targets=[
                {
                    "url": "http://127.0.0.1/form",
                    "employer": "E",
                    "role": "R",
                    "identity": "r",
                    "scope": "CURRENT_PAGE",
                    "country": "GB",
                }
            ],
        )
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            page = await browser.new_page()
            await page.route(
                "**/*", lambda route: route.fulfill(content_type="text/html", body="")
            )
            await page.goto(p.targets[0].url)
            await page.set_content(
                """<form><label for="country">Country</label><button type="button" role="combobox" id="country" aria-required="true" onclick="document.getElementById('choices').hidden=false">Select</button><div id="choices" role="listbox" hidden><div role="option" onclick="country.setAttribute('aria-valuetext',this.innerText);choices.hidden=true">United Kingdom</div></div><fieldset><legend>Will you require sponsorship?</legend><label for="yes">Yes</label><input id="yes" type="radio" name="sponsorship" value="Yes" required><label for="no">No</label><input id="no" type="radio" name="sponsorship" value="No"></fieldset></form>"""
            )
            result = await Engine(Documents(s)).fill(
                page, p, p.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED, result
            assert await page.locator("#no").is_checked()
            assert (
                await page.locator("#country").get_attribute("aria-valuetext")
                == "United Kingdom"
            )
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_referee_is_not_applicant_and_record_ids_persist(tmp_path):
    assert classify_field("Email", section="Referee details") == FieldKind.REFEREE
    s = Store(tmp_path / "db")
    v = s.profile({"work_experience": [{"employer": "E", "title": "Intern"}]})
    first = s.load_profile(v)["work_experience"][0]["id"]
    updated = s.profile(
        {
            "work_experience": [
                {
                    "employer": "E",
                    "title": "Intern",
                    "responsibilities": "Updated confirmed detail",
                }
            ]
        },
        parent=v,
    )
    assert s.load_profile(updated)["work_experience"][0]["id"] == first
    s.close()


@pytest.mark.parametrize("expires", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_authority_is_rejected(expires):
    with pytest.raises(ValueError):
        plan("synthetic", expires_at=expires)


@pytest.mark.parametrize(
    "action",
    [
        "document.querySelector('form').requestSubmit()",
        "document.querySelector('form').submit()",
        "fetch('/submit',{method:'POST',body:'application'})",
    ],
)
def test_fill_boundary_rejects_javascript_submission_and_restores_manual_action(
    tmp_path, action
):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic"})
        p = plan(
            v,
            targets=[
                {
                    "url": "http://127.0.0.1/form",
                    "employer": "E",
                    "role": "R",
                    "identity": "r",
                }
            ],
        )
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            page = await browser.new_page()
            writes = []

            async def route(r):
                if r.request.method == "POST":
                    writes.append(r.request.url)
                await r.fulfill(content_type="text/html", body="")

            await page.route("**/*", route)
            await page.goto(p.targets[0].url)
            await page.set_content(
                '<form onsubmit="event.preventDefault();window.submissions=(window.submissions||0)+1"><label for="name">Full name</label><input id="name" required><button type="button" data-action="next">Next</button></form>'
            )
            await page.locator("button").evaluate(
                "(el,action)=>el.onclick=()=>{eval(action)}", action
            )
            result = await Engine(Documents(s), testing=True).fill(
                page, p, p.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.INCOMPLETE
            assert not writes
            assert not await page.evaluate("window.submissions||0")
            await page.evaluate("document.querySelector('form').requestSubmit()")
            assert await page.evaluate("window.submissions") == 1
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_approved_application_answer_resolves_existing_profile_conflict(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile(
            {
                "full_name": "Old Name",
                "application_answers": {"r": {"name": "New Name"}},
            }
        )
        p = plan(v)
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            page = await browser.new_page()
            await page.route(
                "**/*",
                lambda r: r.fulfill(
                    content_type="text/html",
                    body='<form><label for="name">Full name</label><input id="name" value="New Name" required></form>',
                ),
            )
            await page.goto(p.targets[0].url)
            result = await Engine(Documents(s)).fill(
                page, p, p.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED
            assert not result.blockers
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_upload_firewall_accepts_only_approved_file_bytes():
    from hashlib import sha256
    from types import SimpleNamespace

    from src.pilot.fill_boundary import document_upload

    body = b"%PDF-synthetic"
    request = SimpleNamespace(
        url="https://portal.test/files",
        method="POST",
        headers={"content-type": "application/pdf"},
        post_data_buffer=body,
    )
    assert document_upload(request, "portal.test", {sha256(body).hexdigest()})
    assert not document_upload(request, "portal.test", {"different"})
    request.url = "https://portal.test/submit"
    assert not document_upload(request, "portal.test", {sha256(body).hexdigest()})


def test_partial_checkpoint_survives_interrupted_slice(tmp_path):
    s = Store(tmp_path / "db")
    v = s.profile({})
    run, apps = s.create_run(plan(v))
    app = apps[0]
    fence = s.acquire(app, "owner", "")
    s.checkpoint(
        app,
        run,
        "owner",
        fence,
        [{"field_id": "name", "observed_value": "Synthetic"}],
        [],
    )
    s.finish(app, "owner", fence, Result(State.INCOMPLETE, detail="Interrupted"))
    assert (
        json.loads(
            s.one("SELECT payload FROM checkpoints WHERE application_id=?", (app,))[
                "payload"
            ]
        )["fields"][0]["observed_value"]
        == "Synthetic"
    )
    s.close()


def test_same_origin_frame_and_dom_replacement(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic", "email": "candidate@example.test"})
        p = plan(v)
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            page = await browser.new_page()

            async def route(r):
                body = (
                    '<form><label for="email">Email</label><input id="email" required></form>'
                    if r.request.url.endswith("/child")
                    else '<form><label for="name">Full name</label><input id="name" required oninput="const copy=this.cloneNode();copy.value=this.value;copy.removeAttribute(\'oninput\');this.replaceWith(copy)"></form><iframe src="/child"></iframe>'
                )
                await r.fulfill(content_type="text/html", body=body)

            await page.route("**/*", route)
            await page.goto(p.targets[0].url)
            await page.frame_locator("iframe").locator("#email").wait_for()
            result = await Engine(Documents(s)).fill(
                page, p, p.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED, result
            assert await page.locator("#name").input_value() == "Synthetic"
            assert (
                await page.frame_locator("iframe").locator("#email").input_value()
                == "candidate@example.test"
            )
            await browser.close()
        s.close()

    asyncio.run(scenario())


def test_failed_import_rolls_back_profile_and_provenance(tmp_path):
    from src.pilot.resources import import_legacy

    s = Store(tmp_path / "db")
    source = tmp_path / "source.json"
    source.write_text('{"custom": "preserve"}')
    preview = legacy_preview([source])
    preview["history"] = [
        {
            "employer": "E",
            "role": "R",
            "identity": "r",
            "url": "https://example.test",
            "account": ["malformed"],
        }
    ]
    with pytest.raises(TypeError):
        import_legacy(s, preview, confirmed=True)
    assert not s.rows("SELECT * FROM profiles")
    assert not s.rows("SELECT * FROM legacy_imports")
    assert source.read_text() == '{"custom": "preserve"}'
    s.close()


def test_qualification_evidence_changes_invalidate_availability(tmp_path):
    from src.pilot.qualification import record, source_digest, valid

    s = Store(tmp_path / "db")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "exit_code": 0,
                "source_digest": source_digest(),
                "qualified_workflows": ["workday:submit"],
            }
        )
    )
    record(s, "workday", "SYNTHETIC", evidence)
    row = s.one("SELECT * FROM adapter_qualifications")
    assert valid(row)
    evidence.write_text("{}")
    assert not valid(row)
    with pytest.raises(ValueError):
        record(s, "workday", "UNATTENDED", evidence)
    s.close()
