"""Real Chromium provider-contract tests, with all HTTPS requests intercepted."""

import asyncio
from html import escape
from urllib.parse import parse_qs

import pytest
from playwright.async_api import async_playwright

from src.pilot.engine import Engine
from src.pilot.models import State
from src.pilot.resources import Documents
from src.pilot.store import Store
from src.pilot.submission import SubmitService
from tests.test_pilot_regressions import plan


@pytest.mark.parametrize("provider", ["allhires", "apply4law"])
def test_family_save_review_submit_and_receipt(provider, tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic Candidate"})
        target = {
            "url": f"https://law.{provider}.com/Apply.aspx?application=R1",
            "employer": "Law",
            "role": "Graduate",
            "identity": "vacancy-R1",
            "portal_application_id": "R1",
            "account": "candidate@example.test",
            "eligibility": "eligible",
        }
        request = plan(
            v,
            targets=[target],
            workflow="AUTO_APPLY",
            permissions=["session", "fill", "submit"],
            submission_policy="submit",
        )
        run, apps = s.create_run(request)
        app = apps[0]
        fence = s.acquire(app, "test", "")
        s.db.execute(
            "INSERT INTO adapter_qualifications VALUES(?,?,?,?)",
            (provider, "submit", "SYNTHETIC", "fixture"),
        )

        def guard():
            s.guard(app, run, "test", fence)

        state = {"stage": "edit", "name": "", "saves": 0, "submits": 0}

        def html():
            prefix = '<html><body><p>candidate@example.test</p><h1>Graduate</h1><form method="post"><input type="hidden" name="ApplicationID" value="R1">'
            if state["stage"] == "edit":
                return (
                    prefix
                    + '<label for="full_name">Full name</label><input id="full_name" name="full_name" required><input type="submit" id="ctl00_btnSaveContinue" name="ctl00$btnSaveContinue" value="Save and continue"></form></body></html>'
                )
            if state["stage"] == "review":
                return (
                    prefix
                    + '<div id="ctl00_ApplicationReview">'
                    + escape(state["name"])
                    + '</div><input type="submit" id="ctl00_btnSubmitApplication" name="ctl00$btnSubmitApplication" value="Submit application"></form></body></html>'
                )
            return (
                prefix
                + '<div id="ctl00_ApplicationSubmitted">Application submitted</div><div id="ctl00_ApplicationReference">RECEIPT-R1</div></form></body></html>'
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            async def route(r):
                assert r.request.url.startswith(target["url"])
                if r.request.method == "POST":
                    body = parse_qs(r.request.post_data)
                    if "ctl00$btnSaveContinue" in body or body.get("__EVENTTARGET") == [
                        "ctl00$btnSaveContinue"
                    ]:
                        state.update(stage="review", name=body["full_name"][0])
                        state["saves"] += 1
                    elif "ctl00$btnSubmitApplication" in body:
                        state["stage"] = "receipt"
                        state["submits"] += 1
                    else:
                        raise AssertionError("Unexpected POST action")
                await r.fulfill(content_type="text/html", body=html())

            await page.route("**/*", route)
            await page.goto(target["url"])
            from src.pilot.adapters import choose

            audit = await choose(page.url).audit(page)
            assert (
                audit["non_final_navigation_recognised"]
                and state["saves"] == state["submits"] == 0
            )
            if provider == "apply4law":
                await page.locator("#ctl00_btnSaveContinue").evaluate(
                    """button=>{button.type='button';button.onclick=()=>{const field=document.createElement('input');field.type='hidden';field.name='__EVENTTARGET';field.value=button.name;button.form.appendChild(field);button.form.submit()}}"""
                )
            result = await Engine(Documents(s)).fill(
                page, request, request.targets[0], s.load_profile(v), guard
            )
            assert result.state == State.REVIEW_READY, result
            assert state["saves"] == 1 and state["submits"] == 0
            result = await SubmitService(s, Documents(s), testing=True).submit(
                page, request, request.targets[0], app, run, result, guard
            )
            assert result.state == State.SUBMITTED_CONFIRMED, result
            assert state["submits"] == 1
            s.finish(app, "test", fence, result)
            s.close()
            s2 = Store(tmp_path / "db")
            s2.recover()
            assert (
                s2.one("SELECT state FROM applications")["state"]
                == "SUBMITTED_CONFIRMED"
            )
            s2.close()
            await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["allhires", "apply4law"])
def test_family_repeated_employment_save_and_dedup(provider, tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile(
            {
                "full_name": "Synthetic",
                "work_experience": [
                    {
                        "employer": "One",
                        "title": "Intern",
                        "start": "2024-06",
                        "end": "2024-08",
                    },
                    {
                        "employer": "Two",
                        "title": "Assistant",
                        "start": "2025-06",
                        "end": "2025-08",
                    },
                ],
            }
        )
        request = plan(v, url=f"https://law.{provider}.com/Employment.aspx")
        saved = []
        editing = False
        writes = []

        def html():
            body = '<form method="post"><label for="name">Full name</label><input id="name"><section id="ctl00_EmploymentHistory">'
            for row in saved:
                body += (
                    '<article class="employment-record"><dl>'
                    + "".join(
                        "<dt>" + k + "</dt><dd>" + escape(val) + "</dd>"
                        for k, val in row.items()
                    )
                    + "</dl></article>"
                )
            if editing:
                body += (
                    '<fieldset class="record-editor">'
                    + "".join(
                        '<label for="'
                        + key
                        + '">'
                        + key
                        + '</label><input required id="'
                        + key
                        + '" name="'
                        + key
                        + '">'
                        for key in ("Employer", "JobTitle", "StartDate", "EndDate")
                    )
                    + '<input type="submit" id="ctl00_btnSaveEmployment" name="ctl00$btnSaveEmployment" value="Save employment"></fieldset>'
                )
            else:
                body += '<input type="submit" id="ctl00_btnAddEmployment" name="ctl00$btnAddEmployment" value="Add employment">'
            return body + "</section></form>"

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            async def route(r):
                nonlocal editing
                if r.request.method == "POST":
                    data = parse_qs(r.request.post_data)
                    writes.append(data)
                    if "ctl00$btnAddEmployment" in data:
                        editing = True
                    elif "ctl00$btnSaveEmployment" in data:
                        saved.append(
                            {
                                k: data[k][0]
                                for k in (
                                    "Employer",
                                    "JobTitle",
                                    "StartDate",
                                    "EndDate",
                                )
                            }
                        )
                        editing = False
                    else:
                        raise AssertionError("Unexpected record action")
                await r.fulfill(content_type="text/html", body=html())

            await page.route("**/*", route)
            await page.goto(request.targets[0].url)
            engine = Engine(Documents(s))
            result = await engine.fill(
                page, request, request.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED, result
            assert len(saved) == 2 and len(writes) == 4
            result = await engine.fill(
                page, request, request.targets[0], s.load_profile(v), lambda: None
            )
            assert result.state == State.PAGE_FILLED, result
            assert len(saved) == 2 and len(writes) == 4
            await browser.close()
        s.close()

    asyncio.run(scenario())
