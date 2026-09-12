"""Actual foreground process and simultaneous dashboard/CLI socket clients."""

import asyncio
import json
import signal
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.async_api import async_playwright

from src.pilot.runtime import client

ROOT = Path(__file__).parents[1]


def test_foreground_singleton_shared_clients_and_restart(tmp_path):
    async def scenario(home):
        profile = tmp_path / "chromium"
        process = None
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                args=[
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                ],
            )
            try:
                await context.route(
                    "https://synthetic.invalid/**",
                    lambda r: r.fulfill(
                        content_type="text/html",
                        body='<form><label for="name">Full name</label><input id="name" required></form>',
                    ),
                )
                for number in range(2):
                    page = await context.new_page()
                    await page.goto(f"https://synthetic.invalid/application-{number}")
                port = (profile / "DevToolsActivePort").read_text().splitlines()[0]
                command = [
                    sys.executable,
                    "cli.py",
                    "runtime",
                    "--home",
                    str(home),
                    "start",
                    "--cdp",
                    "http://127.0.0.1:" + port,
                ]

                async def launch():
                    proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=ROOT,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    async with asyncio.timeout(15):
                        while not (home / "runtime.sock").exists():
                            if proc.returncode is not None:
                                raise AssertionError(
                                    (await proc.communicate())[0].decode()
                                )
                            await asyncio.sleep(0.05)
                    return proc

                process = await launch()
                assert (home / "runtime.sock").stat().st_mode & 0o777 == 0o600
                duplicate = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=ROOT,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                output = (await asyncio.wait_for(duplicate.communicate(), 10))[0]
                assert duplicate.returncode != 0 and b"already owns" in output

                async def request(op, **values):
                    return await asyncio.to_thread(client, {"op": op, **values}, home)

                version = (
                    await request(
                        "save_profile", payload={"full_name": "Synthetic Candidate"}
                    )
                )["version"]
                tabs = {r["url"]: r for r in await request("tabs")}
                plans = []
                for number in range(2):
                    url = f"https://synthetic.invalid/application-{number}"
                    plans.append(
                        {
                            "targets": [
                                {
                                    "url": url,
                                    "tab_id": tabs[url]["tab_id"],
                                    "identity": f"application-{number}",
                                    "employer": "Synthetic",
                                    "role": "Test",
                                    "scope": "CURRENT_PAGE",
                                }
                            ],
                            "profile_version": version,
                            "approval": "isolated fixture",
                            "expires_at": time.time() + 120,
                        }
                    )
                plan_file = tmp_path / "plan.json"
                plan_file.write_text(json.dumps(plans[1]))

                async def cli_start():
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "cli.py",
                        "runtime",
                        "--home",
                        str(home),
                        "run",
                        "--plan",
                        str(plan_file),
                        cwd=ROOT,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    result = (await proc.communicate())[0]
                    assert proc.returncode == 0, result.decode()
                    return json.loads(result)

                ui_result, cli_result = await asyncio.gather(
                    request("start", plan=plans[0]), cli_start()
                )
                async with asyncio.timeout(20):
                    while True:
                        status = await request("status")
                        if len(status["applications"]) == 2 and all(
                            a["state"] == "PAGE_FILLED" for a in status["applications"]
                        ):
                            break
                        await asyncio.sleep(0.05)
                assert (
                    status["maximum_active"] == 1
                )  # selected shared context serializes
                assert ui_result["run_id"] != cli_result["run_id"]
                app = ui_result["applications"][0]
                await request("submitted", application_id=app)
                process.send_signal(signal.SIGINT)
                await asyncio.wait_for(process.communicate(), 10)
                process = await launch()
                status = await request("status")
                assert (
                    next(a for a in status["applications"] if a["id"] == app)["state"]
                    == "SUBMITTED_USER_REPORTED"
                )
                assert status["active_workers"] == 0
            finally:
                if process and process.returncode is None:
                    process.send_signal(signal.SIGINT)
                    await asyncio.wait_for(process.communicate(), 10)
                await context.close()

    with TemporaryDirectory(prefix="ap-", dir="/tmp") as directory:
        asyncio.run(scenario(Path(directory)))
