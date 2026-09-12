import asyncio
import time
from datetime import datetime, timezone
import pytest
from src.pilot.store import Store
from src.pilot.workspace import Workspace
from src.pilot.runtime import Runtime
from src.pilot.models import RunPlan
from src.pilot.job_presentation import expired, requirement_sections
from src.pilot.mcp_server import invoke


def test_expiry_hides_active_cards_without_losing_application_records(tmp_path):
    store = Store(tmp_path / "db")
    try:
        ws = Workspace(store)
        j = {
            "identity": "expired",
            "employer": "Example",
            "role": "Graduate",
            "url": "https://example.com/jobs/1",
            "deadline": "2000-01-01",
        }
        ws.ingest([j])
        app = ws.applied(j)
        ws.command(
            {
                "op": "workspace_job_action",
                "identity": "expired",
                "action": "saved",
                "saved": True,
            }
        )
        assert ws.opportunities() == [] and ws.opportunities("saved") == []
        assert ws.history()[0]["id"] == app["application_id"]
        assert store.one("SELECT COUNT(*) AS n FROM opportunity_index")["n"] == 1
        now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
        assert not expired({"deadline": "2026-09-12"}, now)
        assert expired({"deadline": "2026-09-12T11:00:00Z"}, now)
        assert not expired({"deadline": "17 September"}, now)
        assert not expired({"deadline": "2026-09-12T11:00:00"}, now)
    finally:
        store.close()


def test_advert_sections_preserve_actual_qualification_and_skill_wording():
    r = requirement_sections(
        "<h3>Requirements</h3><ul><li>You must hold a postgraduate degree in computing.</li><li>Python and SQL skills are essential.</li><li>You must be available to travel.</li></ul>"
    )
    assert r["academic"] == ["You must hold a postgraduate degree in computing."]
    assert r["skills"] == ["Python and SQL skills are essential."]
    assert r["other"] == ["You must be available to travel."]
    assert requirement_sections("Join our friendly team.") == {
        "academic": [],
        "skills": [],
        "other": [],
    }
    inline = requirement_sections(
        "<p>You must hold a <strong>postgraduate degree</strong> in computing.</p>"
        "<p>Excellent communication skills.</p>"
        "<p>We offer training in Python.</p>"
        "<p>Rotations are planned based on individual skills.</p>"
        "<p>This is a bespoke programme arranged to help our new joiners improve crucial skills.</p>"
    )
    assert inline["academic"] == ["You must hold a postgraduate degree in computing."]
    assert inline["skills"] == ["Excellent communication skills."]


def test_failed_search_preserves_original_prompt_filters_and_repeat_requests(
    tmp_path, monkeypatch
):
    async def fail(*args):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("src.pilot.local_discovery.find", fail)

    async def scenario():
        store = Store(tmp_path / "db")
        try:
            runtime = Runtime(store, None, testing=True)
            for _ in range(2):
                request = {
                    "op": "workspace_find",
                    "query": "graduate law",
                    "requested": 3,
                    "filters": {"countries": ["GB"]},
                    "original_prompt": "Find three graduate legal roles.",
                }
                started = await runtime.command(request)
                await runtime.background[started["job_id"]]
            rows = runtime.workspace.search_history()
            assert len(rows) == 2 and all(r["state"] == "FAILED" for r in rows)
            assert rows[0]["payload"]["original_prompt"] == request["original_prompt"]
            assert rows[0]["payload"]["filters"] == request["filters"]
        finally:
            store.close()
        reopened = Store(tmp_path / "db")
        try:
            assert len(Workspace(reopened).search_history()) == 2
        finally:
            reopened.close()

    asyncio.run(scenario())


def test_filled_application_history_confirmation_and_restart(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        try:
            ws = Workspace(store)
            v = ws.profile()["version"]
            plan = RunPlan.parse(
                {
                    "targets": [
                        {
                            "url": "https://example.com/form",
                            "employer": "Example",
                            "role": "Graduate",
                            "identity": "grad-27",
                        }
                    ],
                    "profile_version": v,
                    "approval": "Synthetic request",
                    "expires_at": time.time() + 60,
                }
            )
            _, apps = store.create_run(plan)
            app = apps[0]
            store.db.execute(
                "UPDATE applications SET state='PAGE_FILLED' WHERE id=?", (app,)
            )
            assert ws.history()[0]["state"] == "PAGE_FILLED"
            ws.command(
                {
                    "op": "workspace_history_update",
                    "application_id": app,
                    "outcome": "Application in progress",
                    "notes": "Keep this note",
                    "revision": None,
                }
            )
            with pytest.raises(ValueError):
                invoke(
                    "confirm_application_submitted",
                    {"application_id": app, "user_reported": False},
                    None,
                )
            runtime = Runtime(store, None, testing=True)
            await runtime.command({"op": "submitted", "application_id": app})
            row = ws.history()[0]
            assert row["state"] == "SUBMITTED_USER_REPORTED" and row["applied"]
            assert (
                row["notes"] == "Keep this note"
                and row["outcome"] == "Awaiting response"
            )
        finally:
            store.close()
        store = Store(tmp_path / "db")
        try:
            assert Workspace(store).history()[0]["state"] == "SUBMITTED_USER_REPORTED"
        finally:
            store.close()

    asyncio.run(scenario())


def test_saved_context_needs_user_request_and_rejects_credentials(tmp_path):
    store = Store(tmp_path / "db")
    try:
        ws = Workspace(store)
        with pytest.raises(ValueError):
            ws.command({"op": "workspace_save_context", "content": "Something"})
        with pytest.raises(ValueError):
            ws.command(
                {
                    "op": "workspace_save_context",
                    "content": "password: synthetic-secret",
                    "user_requested_save": True,
                }
            )
        ws.command(
            {
                "op": "workspace_save_context",
                "content": "Prefer graduate legal roles",
                "user_requested_save": True,
            }
        )
        assert len(ws.command({"op": "workspace_context", "query": "legal"})) == 1
        assert ws.profile()["payload"] == {}
    finally:
        store.close()


def test_autofill_reads_list_and_legacy_country_rights_without_conflict():
    from types import SimpleNamespace
    from src.pilot.facts import resolve
    from src.field_manifest import FieldKind

    field = SimpleNamespace(
        question="Are you legally authorized to work?",
        field_id="work-permission",
        kind=FieldKind.ELIGIBILITY,
    )
    target = SimpleNamespace(country="GB", employer="Example")
    record = {"country": "GB", "authorized_to_work": True}
    assert resolve(field, {"work_rights": [record]}, target) == "True"
    assert resolve(field, {"work_rights": {"GB": record}}, target) == "True"
    assert (
        resolve(
            field,
            {"work_rights": [record, {**record, "authorized_to_work": False}]},
            target,
        )
        == ""
    )
