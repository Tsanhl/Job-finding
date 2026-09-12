"""Complete local service integration: feed, account, email, form, submit, restart."""

import asyncio
import base64
import json
import time

import httpx
from playwright.async_api import async_playwright
from test_pilot_oauth import MemorySecrets
from test_pilot_runtime import HTML

from src.pilot.accounts import Accounts
from src.pilot.discovery import Discovery
from src.pilot.engine import Engine
from src.pilot.mail import EmailTransactions
from src.pilot.models import RunPlan, State
from src.pilot.resources import Documents
from src.pilot.store import Store
from src.pilot.submission import SubmitService


def test_complete_positive_service_flow(tmp_path):
    async def scenario():
        s = Store(tmp_path / "db")
        v = s.profile({"full_name": "Synthetic Candidate"})
        docfile = tmp_path / "cv.pdf"
        docfile.write_bytes(b"%PDF-1.4\nSynthetic")
        docs = Documents(s)
        doc = docs.register(docfile, "cv", approved=True)
        raw = {
            "workflow": "AUTO_APPLY",
            "targets": [],
            "research_sources": ["http://127.0.0.1/feed"],
            "discovery_budget": 1,
            "profile_version": v,
            "approval": "synthetic",
            "expires_at": time.time() + 120,
            "permissions": [
                "fill",
                "session",
                "upload",
                "discover",
                "register",
                "mail",
                "activate",
                "submit",
            ],
            "submission_policy": "submit",
        }
        discovery_plan = RunPlan.parse(raw)
        feed = {
            "jobs": [
                {
                    "employer": "E",
                    "role": "R",
                    "url": "http://127.0.0.1/role",
                    "identity": "role",
                }
            ]
        }
        found = await Discovery(
            s,
            testing=True,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=feed)
            ),
        ).discover(discovery_plan)
        assert len(found) == 1
        target = {k: found[0][k] for k in ("url", "employer", "role", "identity")}
        target.update(account="candidate@example.test", eligibility="eligible")
        plan = RunPlan.parse({**raw, "targets": [target], "documents": [doc]})
        target = plan.targets[0]
        run, apps = s.create_run(plan)
        app = apps[0]
        fence = s.acquire(app, "test")

        def guard():
            s.guard(app, run, "test", fence)

        rules = {
            "senders": ["auth@employer.test"],
            "hosts": ["127.0.0.1"],
            "paths": ["/role"],
            "subject": "Activate",
        }
        s.db.execute(
            "INSERT INTO account_policies VALUES(?,?,?,?,?)",
            (
                "E",
                "http://127.0.0.1",
                "candidate@example.test",
                "candidate@example.test",
                json.dumps(rules),
            ),
        )

        class MailProvider:
            async def request(self, connection, path, params=None):
                if path == "/messages":
                    return {"messages": [{"id": "message-1"}]}
                headers = [
                    {"name": "From", "value": "auth@employer.test"},
                    {"name": "To", "value": "candidate@example.test"},
                    {"name": "Subject", "value": "Activate"},
                    {
                        "name": "Authentication-Results",
                        "value": "mx.google.com; dkim=pass header.d=employer.test;",
                    },
                ]
                return {
                    "internalDate": str(int(time.time() * 1000)),
                    "payload": {
                        "headers": headers,
                        "mimeType": "text/plain",
                        "body": {
                            "data": base64.urlsafe_b64encode(
                                b"http://127.0.0.1/role?activation=one-use"
                            ).decode()
                        },
                    },
                }

        transactions = EmailTransactions(s, MailProvider(), testing=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            activated = (
                HTML.replace(
                    'data-account=""', 'data-account="candidate@example.test"'
                ).replace('data-account=""', 'data-account="candidate@example.test"')
                + "<span>candidate@example.test</span>"
            )

            async def route(request):
                if request.request.method == "POST":
                    await request.fulfill(body="ok")
                else:
                    await request.fulfill(content_type="text/html", body=activated)

            await page.route("**/*", route)
            await page.goto(target.url)
            await page.set_content(
                '<form onsubmit="event.preventDefault()"><input id="email"><input id="password" type="password"><input id="confirm" type="password"><button type="button" id="create">Create account</button></form>'
            )
            waiting = await Accounts(s, MemorySecrets()).register(
                page, target, plan, guard, transactions
            )
            assert waiting["status"] == "NEEDS_AUTHENTICATION"
            assert not s.rows("SELECT * FROM portal_accounts")
            # The applicant completes account/password setup. Mail matching is a
            # separate, explicit service and never receives the password.
            await page.set_content(activated)
            transaction = transactions.begin(
                app,
                "candidate@example.test",
                "candidate@example.test",
                "E",
                "activation",
                rules,
                plan,
            )
            message = await transactions.poll(transaction, plan)
            assert message

            async def positive(page):
                return (
                    await page.get_by_text("candidate@example.test", exact=True).count()
                    > 0
                )

            verified = await transactions.consume(
                page, message, plan, guard, positive_state=positive
            )
            assert verified["status"] == "CONFIRMED"
            assert "value" not in message
            result = await Engine(docs, testing=True).fill(
                page, plan, target, s.load_profile(v), guard
            )
            assert result.state == State.REVIEW_READY, result
            s.db.execute(
                "INSERT INTO adapter_qualifications VALUES(?,?,?,?)",
                ("fixture", "submit", "SYNTHETIC", "isolated-test"),
            )
            result = await SubmitService(s, docs, testing=True).submit(
                page, plan, target, app, run, result, guard
            )
            assert result.state == State.SUBMITTED_CONFIRMED, result
            s.finish(app, "test", fence, result)
            await browser.close()
        s.recover()
        assert s.snapshot()["applications"][0]["state"] == "SUBMITTED_CONFIRMED"
        assert s.acquire(app, "restart") is None
        assert "one-use" not in json.dumps(s.rows("SELECT * FROM email_transactions"))
        s.close()

    asyncio.run(scenario())
