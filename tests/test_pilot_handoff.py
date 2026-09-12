import asyncio
import time

import pytest
from playwright.async_api import async_playwright
from test_pilot_runtime import wait_until

from src.pilot.runtime import Runtime
from src.pilot.store import Store


def test_one_blocked_target_other_finishes_then_grouped_resume(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic"})
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            contexts = [await browser.new_context(), await browser.new_context()]
            pages = []
            for index, ctx in enumerate(contexts):
                await ctx.route(
                    "**/*",
                    lambda r: r.fulfill(content_type="text/html", body="<html></html>"),
                )
                page = await ctx.new_page()
                await page.goto("https://example.test/" + str(index))
                await page.set_content(
                    '<form><label for="name">Full name</label><input id="name" required>'
                    + (
                        '<label for="unknown">Favourite programming language</label><input id="unknown" required>'
                        if index == 0
                        else ""
                    )
                    + "</form>"
                )
                pages.append(page)
            r = Runtime(s, browser)
            inventory = await r.inventory()
            pump = asyncio.create_task(r.pump())
            result = await r.start(
                {
                    "targets": [
                        {
                            "url": page.url,
                            "employer": "E",
                            "role": str(i),
                            "identity": str(i),
                            "tab_id": next(
                                t["tab_id"] for t in inventory if t["url"] == page.url
                            ),
                            "scope": "CURRENT_PAGE",
                        }
                        for i, page in enumerate(pages)
                    ],
                    "workers": 2,
                    "profile_version": v,
                    "approval": "synthetic",
                    "expires_at": time.time() + 60,
                }
            )
            await wait_until(lambda: len(r.timings) == 2)
            states = {a["role"]: a["state"] for a in s.snapshot()["applications"]}
            assert states == {"0": "NEEDS_INFORMATION", "1": "PAGE_FILLED"}, [
                (a["role"], a["state"], a["detail"])
                for a in s.snapshot()["applications"]
            ]
            questions = s.snapshot()["questions"]
            assert len(questions) == 1
            await r.command(
                {
                    "op": "answer_questions",
                    "answers": {questions[0]["key"]: "Python"},
                    "reuse": False,
                }
            )
            await wait_until(lambda: len(r.timings) == 3)
            assert await pages[0].locator("#unknown").input_value() == "Python"
            assert not s.snapshot()["questions"]
            app = result["applications"][0]
            await r.command({"op": "submitted", "application_id": app})
            assert (
                s.one("SELECT state FROM applications WHERE id=?", (app,))["state"]
                == "SUBMITTED_USER_REPORTED"
            )
            with pytest.raises(ValueError):
                await r.command({"op": "resume_application", "application_id": app})
            r.closed = True
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await browser.close()
        s.close()

    asyncio.run(scenario())
