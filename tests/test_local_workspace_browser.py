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
                "education": [{"institution": "Example University", "degree": "BSc"}],
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
                    "requirements": "A degree",
                    "opening_date": "2026-09-12",
                }
            ]
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
                await page.get_by_role(
                    "heading", name="Discover jobs", exact=True
                ).wait_for()
                assert (
                    await page.get_by_role(
                        "button", name="Email alerts", exact=True
                    ).count()
                    == 0
                )
                await page.get_by_role(
                    "button", name="My Information", exact=False
                ).click()
                await page.get_by_role(
                    "heading", name="My Information", exact=True
                ).wait_for()
                await page.locator('input[name="full_name"]').fill("Synthetic Updated")
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
