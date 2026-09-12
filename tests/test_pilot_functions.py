"""Function API, onboarding, discovery and LinkedIn runtime acceptance tests."""

import asyncio
import time

import httpx
from playwright.async_api import async_playwright

from src.pilot.discovery import Discovery, format_job
from src.pilot.drafting import redact
from src.pilot.models import FinalAction, FunctionKind, RunPlan
from src.pilot.onboarding import setup_status
from src.pilot.question_catalog import load as load_questions
from src.pilot.resources import Documents
from src.pilot.runtime import Runtime
from src.pilot.store import Store
from tests.test_pilot_runtime import wait_until


def complete_profile():
    profile = {
        "full_name": "Synthetic Candidate",
        "email": "candidate@example.test",
        "phone": "+44 7000 000000",
        "location": "London",
        "education": {"institution": "Example University", "degree": "BSc"},
        "work_experience": [
            {"employer": "Example Employer", "position": "Assistant"}
        ],
        "answers": {
            "authorized_to_work": "Yes",
            "require_sponsorship": "No",
        },
    }
    for item in load_questions()["questions"]:
        path = item.get("profile_path")
        if not path:
            continue
        current = profile
        parts = path.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = (
            item.get("choices", ["No"])[0]
            if item["answer_type"] != "yes_no"
            else "No"
        )
    return profile


def approved_cv(store, tmp_path):
    path = tmp_path / "synthetic-cv.pdf"
    path.write_bytes(b"%PDF-1.4\nSynthetic fixture")
    return Documents(store).register(path, "cv", approved=True)


def test_function_plan_has_target_scoped_final_actions():
    plan = RunPlan.parse(
        {
            "request_schema": 2,
            "function": "AUTOFILL",
            "targets": [
                {
                    "url": "https://one.example.test/apply",
                    "employer": "One",
                    "role": "Graduate",
                    "identity": "one",
                    "final_action": "REVIEW",
                },
                {
                    "url": "https://two.example.test/apply",
                    "employer": "Two",
                    "role": "Graduate",
                    "identity": "two",
                    "final_action": "SUBMIT",
                    "eligibility": "eligible",
                },
            ],
            "profile_version": "profile",
            "permissions": ["fill", "session", "submit"],
            "approval": "Synthetic",
            "expires_at": time.time() + 60,
        }
    )
    assert plan.function == FunctionKind.AUTOFILL
    assert plan.targets[0].final_action == FinalAction.REVIEW
    assert not plan.will_submit(plan.targets[0])
    assert plan.will_submit(plan.targets[1])
    legacy = RunPlan.parse(
        {
            "workflow": "FILL_ONLY",
            "targets": [
                {
                    "url": "https://legacy.example.test/apply",
                    "employer": "Legacy",
                    "role": "Role",
                    "identity": "legacy",
                }
            ],
            "profile_version": "profile",
            "approval": "Legacy compatibility",
            "expires_at": time.time() + 60,
        }
    )
    assert legacy.request_schema == 1
    assert not legacy.will_submit(legacy.targets[0])


def test_first_run_readiness_requires_profile_and_approved_cv(tmp_path):
    store = Store(tmp_path / "db")
    try:
        assert setup_status(store, Documents(store))["missing"] == [
            "reusable profile",
            "approved CV",
        ]
        version = store.profile(complete_profile())
        assert "approved CV" in setup_status(store, Documents(store), version)[
            "missing"
        ]
        approved_cv(store, tmp_path)
        assert setup_status(store, Documents(store), version)["ready"]
    finally:
        store.close()


def test_external_drafting_redacts_direct_identifiers():
    value = redact(
        {
            "full_name": "Private Candidate",
            "work_experience": [
                {
                    "employer": "Example Employer",
                    "summary": "Contact private.person@invalid.org or +44 7123 456789",
                }
            ],
        }
    )
    assert value["full_name"] == "[redacted]"
    assert value["work_experience"][0]["employer"] == "Example Employer"
    assert "private.person" not in value["work_experience"][0]["summary"]
    assert "7123" not in value["work_experience"][0]["summary"]


def test_priority_discovery_normalizes_and_persists_sources(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        sources = [
            "https://www.brightnetwork.co.uk/graduate-jobs/software/",
            "https://tenant.myworkdayjobs.com/en-US/careers",
            "https://law.allhires.com/jobs",
            "https://firm.apply4law.com/vacancies",
        ]

        def response(request):
            host = request.url.host
            employer = host.split(".")[0].title()
            payload = {
                "@type": "JobPosting",
                "title": "Graduate Software Engineer",
                "hiringOrganization": {"name": employer},
                "url": str(request.url) + "/job/1",
                "identifier": {"value": host},
                "datePosted": "2026-09-01",
                "validThrough": "2026-10-01",
                "qualifications": "Degree and Python",
                "jobLocation": {
                    "address": {
                        "addressLocality": "London",
                        "addressCountry": "GB",
                    }
                },
                "employmentType": "FULL_TIME",
            }
            return httpx.Response(200, json=payload)

        try:
            result = await Discovery(
                store,
                testing=True,
                transport=httpx.MockTransport(response),
            ).find(
                {
                    "query": "graduate software",
                    "location": "London",
                    "requested": 4,
                    "include_builtin": False,
                    "sources": sources,
                }
            )
            assert len(result["jobs"]) == 4
            assert {row["provider"] for row in result["jobs"]} == {
                "bright_network",
                "workday",
                "allhires",
                "apply4law",
            }
            assert all(row["deadline"] == "2026-10-01" for row in result["jobs"])
            assert result["area"] == "graduate software"
            assert result["returned"] == 4
            assert all("CHECK PORTAL" in row["summary"] for row in result["jobs"])
            assert all(
                "Requirements: Degree and Python" in row["summary"]
                for row in result["jobs"]
            )
            assert store.one(
                "SELECT COUNT(*) AS n FROM discovery_results WHERE run_id=?",
                (result["run_id"],),
            )["n"] == 4
            registered = Discovery(store, testing=True).register_source(
                sources[1], "Example Workday"
            )
            assert registered["provider"] == "workday"
        finally:
            store.close()

    asyncio.run(scenario())


def test_discovery_crawls_same_site_details_and_filters_requested_area(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        root = "https://careers.example.test/openings"

        def response(request):
            if request.url.path == "/openings":
                return httpx.Response(
                    200,
                    text=(
                        '<a href="/jobs/technology">Technology role</a>'
                        '<a href="/jobs/procurement">Procurement role</a>'
                    ),
                )
            area = request.url.path.rsplit("/", 1)[-1]
            payload = {
                "@type": "JobPosting",
                "title": area.title() + " Graduate",
                "hiringOrganization": {"name": "Example Employer"},
                "url": str(request.url),
                "identifier": {"value": area},
                "validThrough": "2026-09-14",
                "requirements": (
                    "Python and a technology-related degree"
                    if area == "technology"
                    else "Commercial sourcing experience"
                ),
                "jobLocation": {"address": {"addressLocality": "London"}},
                "employmentType": "FULL_TIME",
            }
            return httpx.Response(200, json=payload)

        try:
            result = await Discovery(
                store,
                testing=True,
                transport=httpx.MockTransport(response),
            ).find(
                {
                    "query": "technology graduate jobs",
                    "location": "London",
                    "requested": 5,
                    "include_builtin": False,
                    "sources": [root],
                }
            )
            assert result["returned"] == 1
            job = result["jobs"][0]
            assert job["role"] == "Technology Graduate"
            assert job["deadline"] == "2026-09-14"
            assert job["requirements"] == "Python and a technology-related degree"
            assert job["summary"] == (
                "Example Employer — Technology Graduate — Deadline 14 Sep 2026 — "
                "CHECK PORTAL — Portal: https://careers.example.test/jobs/technology — "
                "Location: London — Type: FULL_TIME — Pay: Unknown — "
                "Requirements: Python and a technology-related degree"
            )
            assert "[https://careers.example.test/jobs/technology]" in format_job(
                job, markdown=True
            )
        finally:
            store.close()

    asyncio.run(scenario())


def test_linkedin_reaches_requested_review_count_without_submit(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        version = store.profile(complete_profile())
        document = approved_cv(store, tmp_path)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()

            async def route(handler):
                url = handler.request.url
                if "/jobs/search/" in url:
                    links = "".join(
                        f'<a href="https://www.linkedin.com/jobs/view/{index}">Job {index}</a>'
                        for index in range(1, 7)
                    )
                    await handler.fulfill(content_type="text/html", body=links)
                    return
                identity = url.rstrip("/").split("/")[-1]
                if identity == "1":
                    await handler.fulfill(
                        content_type="text/html",
                        body="<h1>Unsupported role</h1><p>No Easy Apply control</p>",
                    )
                    return
                body = f"""<!doctype html><html><body>
                <h1>Graduate Engineer {identity}</h1>
                <a class='job-details-jobs-unified-top-card__company-name'>Employer {identity}</a>
                <button type='button' aria-label='Easy Apply' onclick='openModal()'>Easy Apply</button>
                <div id='modal' role='dialog'></div>
                <script>
                function openModal() {{ modal.innerHTML = `<form><label for="full_name">Full name</label><input id="full_name" required><button type="button" aria-label="Continue to next step" onclick="reviewStep()">Next</button></form>`; }}
                function reviewStep() {{ modal.innerHTML = `<form><label for="confirm_name">Full name</label><input id="confirm_name" required><button type="button" aria-label="Submit application" onclick="window.submissions=(window.submissions||0)+1">Submit application</button></form>`; }}
                </script></body></html>"""
                await handler.fulfill(content_type="text/html", body=body)

            await context.route("**/*", route)
            await context.new_page()
            runtime = Runtime(store, browser, testing=True)
            pump = asyncio.create_task(runtime.pump())
            result = await runtime.command(
                {
                    "op": "linkedin_easy_apply",
                    "keywords": "graduate engineer",
                    "location": "London",
                    "requested": 2,
                    "profile_version": version,
                    "documents": [document],
                }
            )
            batch_id = result["batch_id"]
            try:
                await wait_until(
                    lambda: runtime.linkedin.status(batch_id)["review_ready"] == 2,
                    seconds=15,
                )
            except TimeoutError as exc:
                raise AssertionError(
                    {
                        "batch": runtime.linkedin.status(batch_id),
                        "applications": store.snapshot()["applications"],
                        "candidates": store.rows(
                            "SELECT * FROM linkedin_candidates WHERE batch_id=?",
                            (batch_id,),
                        ),
                    }
                ) from exc
            status = runtime.linkedin.status(batch_id)
            assert status["requested"] == 2
            assert status["attempt_limit"] == 6
            assert status["review_ready"] == 2
            assert status["attempted"] == 3
            assert status["skipped"] == 0
            assert status["unsupported"] == 1
            for page in runtime.pages.values():
                assert await page.evaluate("window.submissions || 0") == 0
            runtime.closed = True
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await browser.close()
        store.close()

    asyncio.run(scenario())
