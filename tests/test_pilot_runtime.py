"""Disposable SQLite + real Chromium. No live profiles, mail or public navigation."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from src.pilot.engine import Engine
from src.pilot.models import Result, RunPlan, State
from src.pilot.resources import Cache, Documents
from src.pilot.runtime import Runtime
from src.pilot.store import Store

HTML = """<!doctype html><html><body><form data-stage="review" onsubmit="event.preventDefault();window.submissions=(window.submissions||0)+1">
<label for="name">Full name</label><input id="name" required>
<label for="resume">CV</label><input id="resume" type="file" required style="display:none" accept=".pdf">
<div id="saved"></div><button type="submit" data-action="submit">Submit application</button></form>
<script>
resume.onchange=async()=>{await fetch('/upload',{method:'POST',body:resume.files[0]});saved.innerHTML='<span data-upload-saved data-server-id=server-attachment-1 data-slot=resume>'+resume.files[0].name+'</span>'};
document.querySelector('[data-action=submit]').onclick=(e)=>{e.preventDefault();document.body.innerHTML='<div data-receipt data-reference="R-123" data-application-identity="'+location.pathname.slice(1)+'" data-account="">Received</div>'};
</script></body></html>"""


class Fixture:
    def __init__(self, workers=1):
        self.barrier = threading.Barrier(workers)
        self.overlap = 0
        self.active = 0
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(HTML.encode())

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with fixture.lock:
                    fixture.active += 1
                    fixture.overlap = max(fixture.overlap, fixture.active)
                try:
                    fixture.barrier.wait(timeout=12)
                except threading.BrokenBarrierError:
                    pass
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                with fixture.lock:
                    fixture.active -= 1

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


async def wait_until(predicate, seconds=25):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("workers", [1, 2, 5, 10])
def test_real_browser_concurrency_and_uploads(workers, tmp_path):
    async def scenario():
        fixture = Fixture(workers)
        store = Store(tmp_path / "db.sqlite3")
        try:
            version = store.profile({"full_name": "Synthetic Candidate"})
            cv = tmp_path / "cv.pdf"
            cv.write_bytes(b"%PDF-1.4\n synthetic test only")
            document = Documents(store).register(cv, "cv", approved=True)
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                runtime = Runtime(store, browser, testing=True)
                pump = asyncio.create_task(runtime.pump())
                raw = {
                    "targets": [
                        {
                            "url": fixture.url + f"/role-{i}",
                            "employer": f"Employer {i}",
                            "role": "Test",
                            "identity": f"role-{i}",
                        }
                        for i in range(workers)
                    ],
                    "profile_version": version,
                    "documents": [document],
                    "workers": workers,
                    "permissions": ["fill", "session", "upload"],
                    "approval": "Synthetic test",
                    "expires_at": time.time() + 60,
                }
                await runtime.start(raw)
                await wait_until(lambda: len(runtime.timings) == workers)
                statuses = [r["state"] for r in store.snapshot()["applications"]]
                assert statuses == ["REVIEW_READY"] * workers, store.snapshot()
                assert fixture.overlap == workers
                assert runtime.maximum_active == workers
                assert store.snapshot()["active_workers"] == 0
                for page in runtime.pages.values():
                    assert (
                        await page.locator("#name").input_value()
                        == "Synthetic Candidate"
                    )
                print(
                    json.dumps(
                        {
                            "workers": workers,
                            "measured_browser_upload_overlap": fixture.overlap,
                            "maximum_active": runtime.maximum_active,
                        }
                    )
                )
                runtime.closed = True
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
                await browser.close()
        finally:
            store.close()
            fixture.close()

    asyncio.run(scenario())


def test_auto_submit_and_manual_done(tmp_path):
    async def scenario():
        fixture = Fixture()
        s = Store(tmp_path / "db")
        try:
            v = s.profile({"full_name": "Synthetic Candidate"})
            cv = tmp_path / "cv.pdf"
            cv.write_bytes(b"%PDF-1.4\n synthetic")
            doc = Documents(s).register(cv, "cv", approved=True)
            s.db.execute(
                "INSERT INTO adapter_qualifications VALUES(?,?,?,?)",
                ("fixture", "submit", "SYNTHETIC", "local-browser-test"),
            )
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                r = Runtime(s, browser, testing=True)
                pump = asyncio.create_task(r.pump())
                await r.start(
                    {
                        "workflow": "AUTO_APPLY",
                        "targets": [
                            {
                                "url": fixture.url + "/role",
                                "employer": "Test",
                                "role": "Test",
                                "identity": "role",
                                "eligibility": "eligible",
                            }
                        ],
                        "profile_version": v,
                        "documents": [doc],
                        "permissions": ["fill", "session", "upload", "submit"],
                        "submission_policy": "submit",
                        "approval": "Synthetic",
                        "expires_at": time.time() + 60,
                    }
                )
                await wait_until(lambda: len(r.timings) == 1)
                assert (
                    s.snapshot()["applications"][0]["state"] == "SUBMITTED_CONFIRMED"
                ), s.snapshot()
                assert (
                    s.one("SELECT state FROM submission_attempts")["state"]
                    == "CONFIRMED"
                )
                with pytest.raises(ValueError):
                    await r.start(json.loads(s.one("SELECT plan FROM runs")["plan"]))
                r.closed = True
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
                await browser.close()
            s.recover()
            assert s.snapshot()["applications"][0]["state"] == "SUBMITTED_CONFIRMED"
        finally:
            s.close()
            fixture.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("requested", [1, 2, 5, 10])
def test_open_requested_job_links_in_managed_browser(requested, tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            await context.route(
                "**/*",
                lambda route: route.fulfill(
                    content_type="text/html", body="<title>Synthetic vacancy</title>"
                ),
            )
            runtime = Runtime(store, browser, testing=True)
            opened = await runtime.command(
                {
                    "op": "open_job_links",
                    "urls": [
                        f"https://employer-{index}.example/vacancy/{index}"
                        for index in range(10)
                    ],
                    "requested": requested,
                }
            )
            assert len(opened) == requested
            assert {row["status"] for row in opened} == {"Opened"}
            assert all(row["tab_id"] in runtime.tabs for row in opened)
            await browser.close()
        store.close()

    asyncio.run(scenario())


def test_review_policy_reaches_final_review_after_all_sections(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        version = store.profile({"full_name": "Synthetic Candidate"})
        plan = RunPlan.parse(
            {
                "targets": [
                    {
                        "url": "http://localhost/application",
                        "employer": "Synthetic Employer",
                        "role": "Graduate role",
                        "identity": "synthetic-review",
                    }
                ],
                "profile_version": version,
                "permissions": ["fill", "session"],
                "submission_policy": "review",
                "approval": "Synthetic",
                "expires_at": time.time() + 60,
            }
        )
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            html = """<form>
                <label for=name>Full name</label><input id=name required>
                <button type=button data-action=next onclick=\"document.body.innerHTML=`
                  <form data-stage=review>
                    <label for=confirm>Full name</label><input id=confirm required>
                    <button type=submit data-action=submit>Submit application</button>
                  </form>`\">Next</button>
                </form>"""
            await page.route(
                "http://localhost/**",
                lambda route: route.fulfill(content_type="text/html", body=html),
            )
            await page.goto("http://localhost/application")
            result = await Engine(Documents(store), testing=True).fill(
                page,
                plan,
                plan.targets[0],
                store.load_profile(version),
                lambda: None,
            )
            assert result.state == State.REVIEW_READY
            assert await page.locator("[data-stage=review]").count() == 1
            assert await page.locator("#confirm").input_value() == "Synthetic Candidate"
            await browser.close()
        store.close()

    asyncio.run(scenario())


def test_state_fencing_manual_completion_and_backup(tmp_path):
    s = Store(tmp_path / "db")
    v = s.profile({"full_name": "Synthetic"})
    plan = RunPlan.parse(
        {
            "targets": [
                {
                    "url": "https://example.test/form",
                    "employer": "E",
                    "role": "R",
                    "identity": "r",
                }
            ],
            "profile_version": v,
            "approval": "test",
            "expires_at": time.time() + 60,
        }
    )
    run, apps = s.create_run(plan)
    app = apps[0]
    fence = s.acquire(app, "owner")
    s.guard(app, run, "owner", fence)
    s.user_submitted(app)
    with pytest.raises(PermissionError):
        s.guard(app, run, "owner", fence)
    s.finish(app, "owner", fence, Result(State.NEEDS_INFORMATION))
    submitted = s.snapshot()["applications"][0]
    assert submitted["state"] == "SUBMITTED_USER_REPORTED"
    assert submitted["detail"] == "Done — user reported"
    receipt_count = len(s.rows("SELECT id FROM receipts"))
    s.user_submitted(app)
    assert len(s.rows("SELECT id FROM receipts")) == receipt_count
    backup = tmp_path / "backup"
    s.backup(backup)
    s.close()
    restored = Store(backup)
    assert restored.snapshot()["applications"][0]["state"] == "SUBMITTED_USER_REPORTED"
    restored.close()


@pytest.mark.parametrize("value", [0, -1, 11, True, 1.5, "2"])
def test_invalid_workers(value):
    with pytest.raises(ValueError):
        RunPlan.parse({"workers": value, "targets": [{}]})


def test_fill_permissions_cannot_leak():
    with pytest.raises(ValueError, match="FILL_ONLY"):
        RunPlan.parse(
            {
                "targets": [
                    {
                        "url": "https://example.test/form",
                        "employer": "E",
                        "role": "R",
                        "identity": "r",
                    }
                ],
                "profile_version": "v",
                "permissions": ["fill", "submit"],
                "approval": "test",
                "expires_at": time.time() + 60,
            }
        )


def test_cache_clear_preserves_profiles(tmp_path):
    s = Store(tmp_path / "db")
    v = s.profile({"full_name": "Synthetic"})
    c = Cache(s)
    c.put("key", "draft", {"answer": "Synthetic"})
    assert c.get("key")
    c.clear()
    assert c.get("key") is None
    assert s.load_profile(v)["full_name"] == "Synthetic"
    s.close()
