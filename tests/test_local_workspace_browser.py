"""Real Chromium against an isolated local launcher, with synthetic records only."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.async_api import async_playwright

from src.pilot.runtime import client
from src.pilot.store import Store
from src.pilot.workspace import Workspace


def test_merged_dashboard_profile_history_and_daily_settings(tmp_path):
    async def scenario(home):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        store = Store(home / "applypilot.sqlite3")
        w = Workspace(store)
        p = w.profile()
        w.save_profile(
            {
                "full_name": "Synthetic Candidate",
                "location": "Home City",
                "education": [
                    {
                        "id": "record-a",
                        "institution": "Example University",
                        "degree": "BSc",
                    },
                    {
                        "id": "record-b",
                        "institution": "Second University",
                        "degree": "MSc",
                    },
                ],
                "work_experience": [
                    {
                        "employer": "Example Office",
                        "position": "Clerk",
                        "location": "Work City",
                    }
                ],
                "answers": {"has_driving_licence": False},
                "custom": {"preserve": None},
            },
            p["version"],
        )
        w.ingest(
            [
                {
                    "identity": "ui-job",
                    "role": "Graduate Engineer",
                    "employer": "Example Employer",
                    "url": "https://example.com/jobs/1",
                    "requirements": "A postgraduate degree in computing is required. Python skills are essential.",
                    "opening_date": "2026-09-12",
                }
            ]
        )
        w.remember_search(
            {
                "query": "graduate legal",
                "requested": 3,
                "filters": {"countries": ["GB"]},
                "original_prompt": "Find graduate legal work.",
            },
            "discovery",
            None,
        )
        store.close()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src.pilot.desktop",
                "--home",
                str(home),
                "--port",
                str(port),
                "--no-open",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if (home / "dashboard-url.txt").exists():
                    break
                if process.poll() is not None:
                    raise AssertionError("Launcher exited")
                await asyncio.sleep(0.1)
            url = (home / "dashboard-url.txt").read_text()
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1400, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for _ in range(50):
                    try:
                        await page.goto(url)
                        break
                    except Exception:
                        await asyncio.sleep(0.1)
                try:
                    await page.get_by_role(
                        "heading", name="Discover jobs", exact=True
                    ).wait_for(timeout=10000)
                except Exception:
                    raise AssertionError(
                        await page.locator("body").inner_text(timeout=2000)
                    ) from None
                assert (
                    await page.get_by_role(
                        "button", name="Email alerts", exact=True
                    ).count()
                    == 0
                )
                assert (
                    await page.get_by_role(
                        "button", name="Discovery status", exact=False
                    ).count()
                    == 0
                )
                await page.get_by_role(
                    "heading", name="Academic & entry requirements"
                ).wait_for()
                await page.get_by_role("button", name="Save", exact=True).click()
                await page.get_by_role(
                    "button", name="Saved opportunities", exact=False
                ).click()
                await page.get_by_role(
                    "button", name="Remove saved opportunity", exact=True
                ).click()
                await page.get_by_role(
                    "button", name="Remove saved opportunity", exact=True
                ).wait_for(state="hidden")
                await page.get_by_role(
                    "button", name="Search history", exact=False
                ).click()
                await page.get_by_role(
                    "button", name="Use this search", exact=True
                ).click()
                assert (
                    await page.locator('input[name="query"]').input_value()
                    == "graduate legal"
                )
                output = Path("output/unified-validation")
                output.mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(output / "discovery-redesign-synthetic.png"),
                    full_page=True,
                )
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(
                    path=str(output / "discovery-mobile-synthetic.png"), full_page=True
                )
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                await page.set_viewport_size({"width": 1400, "height": 1000})
                await page.get_by_role(
                    "button", name="My Information", exact=False
                ).click()
                await page.get_by_role(
                    "heading", name="My Information", exact=True
                ).wait_for()
                await page.locator('input[name="full_name"]').fill("Synthetic Updated")
                await page.locator('input[name="full_name"]').press("Tab")
                await (
                    page.locator("#save-status")
                    .filter(has_text="Saved locally")
                    .wait_for()
                )
                automatic = await asyncio.to_thread(
                    client, {"op": "workspace_profile"}, home
                )
                assert automatic["payload"]["full_name"] == "Synthetic Updated"

                def profile_response(response):
                    return (
                        response.request.method == "POST"
                        and (response.request.post_data_json or {}).get("op")
                        == "workspace_save_profile"
                    )

                async with page.expect_response(profile_response):
                    await (
                        page.locator('[data-record="education"]')
                        .first.get_by_role("button", name="Remove record", exact=True)
                        .click()
                    )
                remaining = page.locator(
                    '[data-record="education"] input[name="degree"]'
                )
                await remaining.fill("MSc Computing")
                async with page.expect_response(profile_response):
                    await remaining.press("Tab")
                records = (
                    await asyncio.to_thread(client, {"op": "workspace_profile"}, home)
                )["payload"]["education"]
                assert len(records) == 1
                assert records[0]["id"] == "record-b"
                assert records[0]["degree"] == "MSc Computing"

                await page.get_by_role(
                    "button", name="Save my information", exact=True
                ).click()
                await page.get_by_text(
                    "Your information is saved locally.", exact=True
                ).wait_for()
                saved = await asyncio.to_thread(
                    client, {"op": "workspace_profile"}, home
                )
                assert saved["payload"]["full_name"] == "Synthetic Updated"
                assert saved["payload"]["location"] == "Home City"
                assert saved["payload"]["work_experience"][0]["location"] == "Work City"
                assert saved["payload"]["custom"]["preserve"] is None
                await page.get_by_role(
                    "button", name="Discover jobs", exact=False
                ).click()
                await page.get_by_role("button", name="I applied", exact=True).click()
                await page.get_by_role("button", name="Not yet", exact=True).click()
                assert not await asyncio.to_thread(
                    client, {"op": "workspace_history"}, home
                )
                await page.get_by_role("button", name="I applied", exact=True).click()
                await page.get_by_role(
                    "button", name="Yes, I submitted it", exact=True
                ).click()
                await page.get_by_role(
                    "button", name="Applied History", exact=False
                ).click()
                await page.get_by_role(
                    "heading", name="Graduate Engineer", exact=True
                ).wait_for()
                await page.get_by_text("Add an assessment manually", exact=True).click()
                await page.locator('input[name="component"]').fill("Numerical test")
                await page.get_by_role(
                    "button", name="Add assessment", exact=True
                ).click()
                await page.get_by_role(
                    "button", name="I completed it", exact=True
                ).click()
                await page.get_by_text("COMPLETED USER REPORTED", exact=True).wait_for()
                await page.reload()
                await page.get_by_role(
                    "button", name="Applied History", exact=False
                ).click()
                await page.get_by_text("COMPLETED USER REPORTED", exact=True).wait_for()
                await page.get_by_role("button", name="Settings", exact=False).click()
                await page.get_by_role(
                    "heading", name="Gmail assessment tracking"
                ).wait_for()
                assert "once every 24 hours" in await page.locator("main").inner_text()
                await page.get_by_role(
                    "heading", name="Automatic recovery", exact=True
                ).wait_for()
                for _ in range(100):
                    recovery = await asyncio.to_thread(
                        client, {"op": "workspace_recovery_status"}, home
                    )
                    if recovery["last_success"]:
                        break
                    await asyncio.sleep(0.05)
                assert recovery["enabled"] and recovery["last_success"]
                assert recovery["off_device_protection"] == "NOT_VERIFIED"
                assert (
                    home / "backups" / "automatic-recovery" / recovery["latest_bundle"]
                ).is_file()
                key = (home / "recovery-keys" / "automatic.key").read_text().strip()
                assert key not in await page.locator("main").inner_text()
                output = Path("output/unified-validation")
                output.mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(output / "settings-synthetic.png"), full_page=True
                )
                assert not errors
                await browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    with TemporaryDirectory(prefix="ap-ui-", dir="/tmp") as directory:
        asyncio.run(scenario(Path(directory)))
