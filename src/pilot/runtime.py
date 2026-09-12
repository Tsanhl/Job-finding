"""Single foreground owner, bounded asynchronous workers, local Unix-socket clients."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from .engine import Engine, inspect
from .models import (
    DONE,
    FinalAction,
    Result,
    RunPlan,
    Scope,
    State,
    Target,
)
from .resources import Cache, Documents
from .store import Store, default_home, encode, uid
from .submission import SubmitService


class Runtime:
    def __init__(self, store, browser, *, testing=False, browser_factory=None):
        self.store = store
        self.browser = browser
        self.browser_factory = browser_factory
        self.browser_lock = asyncio.Lock()
        self.background = {}
        from .workspace import Workspace

        self.workspace = Workspace(store)
        from .tracking import MailTracking

        self.tracking = MailTracking(store, self.workspace)
        from .auto_recovery import AutoRecovery

        self.recovery = AutoRecovery(store)
        self.testing = testing
        self.owner = uid()
        self.documents = Documents(store)
        self.cache = Cache(store)
        from .drafting import Drafting

        self.drafting = Drafting(self.cache)
        self.engine = Engine(self.documents, testing=testing, draft=self.drafting)
        self.submitter = SubmitService(store, self.documents, testing=testing)
        self.pages = {}
        self.tabs = {}
        self.contexts = {}
        self.active = {}
        self.wake = asyncio.Event()
        self.closed = False
        self.mail_jobs = {}
        self.mail_generations = {}
        self.pending_edits = {}
        self.started = {}
        self.maximum_active = 0
        self.timings = deque(maxlen=200)
        self.submit_lock = asyncio.Lock()
        from .linkedin import LinkedInCoordinator

        self.linkedin = LinkedInCoordinator(self)
        from .metrics import Resources

        self.resources = Resources()

    async def ensure_browser(self):
        async with self.browser_lock:
            if self.browser is None or not self.browser.is_connected():
                if self.browser_factory is None:
                    raise ValueError(
                        "Start the managed browser, then retry this application action"
                    )
                try:
                    self.browser = await self.browser_factory()
                except Exception:
                    raise ValueError(
                        "Managed browser unavailable. Start it from the launcher, then retry."
                    ) from None
        return self.browser

    def background_job(self, kind, operation, request=None):
        if len(self.background) >= 3:
            raise ValueError(
                "Three background operations are active; wait for one to finish"
            )
        job = uid()
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO workspace_jobs(id,kind,state,created,updated) VALUES(?,?,'RUNNING',?,?)",
                (job, kind, time.time(), time.time()),
            )
            if request is not None:
                self.workspace.remember_search(request, kind, job)

        async def run():
            try:
                result = await operation()
                state = "DONE"
            except asyncio.CancelledError:
                state = "CANCELLED"
                result = {"detail": "Cancelled; saved records are retained"}
            except Exception:
                state = "FAILED"
                result = {
                    "detail": "Operation could not finish. Check source availability, connection or configuration and retry."
                }
            self.store.db.execute(
                "UPDATE workspace_jobs SET state=?,result=?,updated=? WHERE id=?",
                (state, encode(result), time.time(), job),
            )
            self.background.pop(job, None)

        self.background[job] = asyncio.create_task(run())
        return {"job_id": job, "state": "RUNNING"}

    async def inventory(self):
        await self.ensure_browser()
        result = []
        for ctx in self.browser.contexts:
            context_id = self.contexts.setdefault(ctx, uid())
            for p in ctx.pages:
                # Do not inspect contents or expose query strings from login pages.
                tab = next((k for k, v in self.tabs.items() if v is p), None) or uid()
                self.tabs[tab] = p
                u = urlsplit(p.url)
                result.append(
                    {
                        "tab_id": tab,
                        "context_id": context_id,
                        "url": p.url if not u.query else p.url.split("?")[0],
                        "shared_context": len(ctx.pages) > 1,
                    }
                )
        return result

    async def page(self, app, target, guard):
        await self.ensure_browser()
        if app in self.pages and not self.pages[app].is_closed():
            return self.pages[app]
        if target.tab_id:
            p = self.tabs.get(target.tab_id)
            if not p or p.is_closed() or p.url != target.url:
                raise ValueError(
                    "Selected tab changed; select its exact current application URL"
                )
            if any(v is p for k, v in self.pages.items() if k != app):
                raise ValueError("Tab already bound to another application")
        else:
            retained = self.store.one(
                "SELECT COUNT(DISTINCT context_id) AS n FROM sessions WHERE retained=1"
            )["n"]
            if retained >= 10:
                raise ValueError(
                    "Ten retained contexts; explicitly release a reviewed form before opening another"
                )
            self.store.db.execute(
                "INSERT OR REPLACE INTO sessions VALUES(?,?,?,1)",
                (app, "pending", "pending:" + app),
            )
            guard()
            ctx = await self.browser.new_context(accept_downloads=False)
            guard()
            p = await ctx.new_page()
            p.set_default_timeout(15000)
            p.set_default_navigation_timeout(30000)
            try:
                guard()
                await p.goto(target.url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightError:
                pass  # inspect error/auth state without creating another application
        self.pages[app] = p
        ctx_id = self.contexts.setdefault(p.context, uid())
        tab = next((k for k, v in self.tabs.items() if v is p), None) or uid()
        self.tabs[tab] = p
        self.store.db.execute(
            "INSERT OR REPLACE INTO sessions VALUES(?,?,?,1)", (app, tab, ctx_id)
        )
        return p

    async def start(self, raw):
        plan = RunPlan.parse(raw)
        if not plan.targets:
            from .discovery import Discovery

            return {
                "discovered": await Discovery(
                    self.store, testing=self.testing
                ).discover(plan),
                "next_action": "Review identities and eligibility, then select up to ten targets per batch",
            }
        for t in plan.targets:
            host = urlsplit(t.url).hostname
            if not self.testing and (
                urlsplit(t.url).scheme != "https"
                or host in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("Production application URLs must use public HTTPS")
        from .preflight import check

        report = check(plan, self.store, self.documents)
        if report.global_blockers:
            raise ValueError("; ".join(report.global_blockers))
        run, apps = self.store.create_run(plan)
        self.wake.set()
        return {"run_id": run, "applications": apps, "preflight": asdict(report)}

    async def pump(self):
        while not self.closed:
            self.wake.clear()
            await self.linkedin.expand_active()
            tasks = self.store.rows(
                "SELECT t.*,r.plan FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.status='QUEUED' AND r.status='ACTIVE' AND t.not_before<=? AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN tasks dependency ON dependency.id=d.depends_on WHERE d.task_id=t.id AND dependency.status!='DONE') ORDER BY r.created",
                (time.time(),),
            )
            for task in tasks:
                if len(self.active) >= self.resources.sample()["memory_worker_budget"]:
                    break
                app = task["application_id"]
                run = task["run_id"]
                try:
                    plan = RunPlan.parse(json.loads(task["plan"]))
                except ValueError:
                    self.store.db.execute(
                        "UPDATE tasks SET status='PARKED' WHERE id=?", (task["id"],)
                    )
                    self.store.db.execute(
                        "UPDATE applications SET state='POLICY_BLOCKED',detail='Plan expired; approve a new run' WHERE id=?",
                        (app,),
                    )
                    continue
                if app in self.active:
                    continue
                if (
                    sum(1 for v in self.started.values() if v["run"] == run)
                    >= plan.workers
                ):
                    continue
                ra = self.store.one(
                    "SELECT * FROM run_applications WHERE run_id=? AND application_id=?",
                    (run, app),
                )
                target_data = json.loads(ra["target"])
                target = Target(
                    **{
                        **target_data,
                        "scope": Scope(target_data["scope"]),
                        "final_action": FinalAction(
                            target_data.get("final_action", "REVIEW")
                        ),
                    }
                )
                if plan.result_limit:
                    completed = self.store.one(
                        "SELECT COUNT(*) AS n FROM run_applications ra JOIN applications a ON a.id=ra.application_id WHERE ra.run_id=? AND a.state='REVIEW_READY'",
                        (run,),
                    )["n"]
                    if completed >= plan.result_limit:
                        self.store.db.execute(
                            "UPDATE tasks SET status='CANCELLED' WHERE id=?",
                            (task["id"],),
                        )
                        self.store.db.execute(
                            "UPDATE applications SET state='CANCELLED',detail='Requested review-ready limit reached',updated=? WHERE id=? AND state='QUEUED'",
                            (time.time(), app),
                        )
                        continue
                if ra["profile_version"] != plan.profile_version:
                    plan = replace(
                        plan,
                        profile_version=ra["profile_version"],
                        submission_policy="review",
                    )
                    target = replace(target, final_action=FinalAction.REVIEW)
                account = [target.account] if target.account else []
                if target.tab_id and target.tab_id in self.tabs:
                    account.append(
                        "context:"
                        + self.contexts.setdefault(
                            self.tabs[target.tab_id].context, uid()
                        )
                    )
                if (
                    app not in self.pages
                    and not target.tab_id
                    and self.store.one(
                        "SELECT COUNT(DISTINCT context_id) AS n FROM sessions WHERE retained=1"
                    )["n"]
                    >= 10
                ):
                    continue
                fence = self.store.acquire(app, self.owner, account)
                if fence is None:
                    continue
                self.store.db.execute(
                    "UPDATE tasks SET status='RUNNING',attempts=attempts+1 WHERE id=?",
                    (task["id"],),
                )
                self.started[app] = {"run": run, "at": time.monotonic()}
                worker = asyncio.create_task(self.execute(task, plan, target, fence))
                self.active[app] = worker
                self.maximum_active = max(self.maximum_active, len(self.active))
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except TimeoutError:
                pass

    async def execute(self, task, plan, target, fence):
        app = task["application_id"]
        run = task["run_id"]

        def guard():
            self.store.guard(app, run, self.owner, fence)

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                self.store.heartbeat(app, self.owner, fence)

        beat = asyncio.create_task(heartbeat())
        try:
            from .tasks import validate_task

            validate_task(task["kind"], plan)
            guard()
            plan.require("session") if not plan.preview else None
            page = await self.page(app, target, guard)
            if task["kind"] == "WAIT_EMAIL":
                from .mail import EmailTransactions, Gmail

                transactions = EmailTransactions(self.store, Gmail(self.store))
                tx = self.store.one(
                    "SELECT * FROM email_transactions WHERE application_id=? AND status='PENDING' ORDER BY requested DESC LIMIT 1",
                    (app,),
                )
                if not tx:
                    raise ValueError(
                        "Verification requires reconciliation or a new authorised transaction"
                    )
                matched = await transactions.poll(tx["id"], plan)
                if matched:

                    async def positive(p):
                        return bool(
                            target.account
                            and await p.get_by_text(target.account, exact=True).count()
                            and not await p.locator("input[type=password]").count()
                        )

                    outcome = await transactions.consume(
                        page, matched, plan, guard, positive_state=positive
                    )
                    if outcome["status"] != "CONFIRMED":
                        raise ValueError(
                            "Verification outcome uncertain; reconcile account state"
                        )
                    self.store.db.execute(
                        "UPDATE tasks SET status='QUEUED' WHERE application_id=? AND run_id=? AND kind='INSPECT_FORM'",
                        (app, run),
                    )
                    self.store.db.execute(
                        "UPDATE portal_accounts SET status='USABLE' WHERE employer=? AND email=? AND origin=?",
                        (
                            target.employer,
                            target.account,
                            f"{urlsplit(target.url).scheme}://{urlsplit(target.url).netloc}",
                        ),
                    )
                    result = Result(
                        State.INCOMPLETE,
                        detail="Account verified; application filling queued",
                    )
                else:
                    self.store.db.execute(
                        "UPDATE tasks SET not_before=? WHERE id=?",
                        (
                            time.time() + min(60, 5 * 2 ** min(task["attempts"], 4)),
                            task["id"],
                        ),
                    )
                    result = Result(
                        State.EMAIL_PENDING,
                        detail="Awaiting matching verification; other applications continue",
                    )
                self.store.finish(app, self.owner, fence, result)
                self.store.db.execute(
                    "UPDATE tasks SET status=? WHERE id=?",
                    (
                        "QUEUED" if result.state == State.EMAIL_PENDING else "DONE",
                        task["id"],
                    ),
                )
                return
            profile = self.store.load_profile(plan.profile_version)
            if "_confirmed_fields" in profile:
                profile = {
                    **{
                        k: v
                        for k, v in profile.items()
                        if k in profile["_confirmed_fields"]
                        or k == "application_answers"
                    },
                    "_candidate_suggestions": profile,
                }
            account_result = None
            password_present = bool(await page.locator("input[type=password]").count())
            if plan.request_schema == 2 and password_present:
                account_result = {
                    "status": "NEEDS_AUTHENTICATION",
                    "detail": "Create or sign in to the account yourself, complete verification, then Resume",
                }
            elif (
                plan.workflow == "AUTO_APPLY"
                and target.eligibility == "eligible"
                and password_present
            ):
                from .accounts import Accounts
                from .mail import EmailTransactions, Gmail

                accounts = Accounts(self.store)
                current_origin = urlsplit(page.url)
                existing_account = self.store.one(
                    "SELECT id FROM portal_accounts WHERE employer=? AND origin=? AND email=?",
                    (
                        target.employer,
                        f"{current_origin.scheme}://{current_origin.netloc}",
                        target.account,
                    ),
                )
                if existing_account and "credentials" in plan.permissions:
                    account_result = await accounts.ensure(page, target, plan, guard)
                elif "register" in plan.permissions:
                    account_result = await accounts.register(
                        page,
                        target,
                        plan,
                        guard,
                        EmailTransactions(self.store, Gmail(self.store)),
                    )
                elif "credentials" in plan.permissions:
                    account_result = await accounts.ensure(page, target, plan, guard)
            if account_result and account_result["status"] == "EMAIL_PENDING":
                result = Result(State.EMAIL_PENDING, detail=account_result["detail"])
                self.store.db.execute(
                    "INSERT OR IGNORE INTO tasks(id,run_id,application_id,kind,status,not_before) VALUES(?,?,?,'WAIT_EMAIL','QUEUED',?)",
                    (uid(), run, app, time.time() + 5),
                )
            elif account_result and account_result["status"] != "READY":
                result = Result(
                    State(account_result["status"]),
                    detail=account_result.get(
                        "detail", "Complete account authentication"
                    ),
                )
            elif plan.will_submit(target) and target.eligibility != "eligible":
                result = Result(
                    State.ELIGIBILITY_UNVERIFIED,
                    detail="Confirm eligibility for this selected vacancy before AUTO processing",
                )
            else:
                checkpoint = self.store.one(
                    "SELECT * FROM checkpoints WHERE application_id=?", (app,)
                )
                async with asyncio.timeout(180):
                    result = await self.engine.fill(
                        page,
                        plan,
                        target,
                        profile,
                        guard,
                        checkpoint=json.loads(checkpoint["payload"])
                        if checkpoint
                        else None,
                        on_progress=lambda fields, documents: self.store.checkpoint(
                            app, run, self.owner, fence, fields, documents
                        ),
                    )
                    if result.state == State.REVIEW_READY and plan.will_submit(target):
                        async with self.submit_lock:
                            result = await self.submitter.submit(
                                page, plan, target, app, run, result, guard
                            )
        except asyncio.CancelledError:
            result = Result(
                State.INCOMPLETE,
                detail="Stopped at a safe boundary; reconcile before resuming",
            )
        except PermissionError:
            result = Result(
                State.POLICY_BLOCKED,
                detail="Paused, stopped or expired approval; saved work retained",
            )
        except TimeoutError:
            result = Result(
                State.INCOMPLETE,
                detail="Operation deadline reached; inspect the retained form and Resume",
            )
        except ValueError as e:
            result = Result(State.INCOMPLETE, detail=str(e))
        except Exception:  # noqa: BLE001 - worker failures become privacy-safe states
            result = Result(
                State.FAILED_RETRYABLE,
                detail="Technical failure; inspect the retained form before resuming",
            )
        finally:
            beat.cancel()
            try:
                await beat
            except asyncio.CancelledError:
                pass
            if (
                task["kind"] == "WAIT_EMAIL"
                and self.store.one(
                    "SELECT status FROM tasks WHERE id=?", (task["id"],)
                )["status"]
                != "RUNNING"
            ):
                self.timings.append(
                    {
                        "application": app,
                        "start": self.started[app]["at"],
                        "end": time.monotonic(),
                        "state": str(result.state),
                    }
                )
                self.active.pop(app, None)
                self.started.pop(app, None)
                self.wake.set()
        self.store.finish(app, self.owner, fence, result)
        self.store.db.execute(
            "UPDATE tasks SET status=? WHERE id=? AND status='RUNNING'",
            ("DONE" if result.state in DONE else "PARKED", task["id"]),
        )
        self.timings.append(
            {
                "application": app,
                "start": self.started[app]["at"],
                "end": time.monotonic(),
                "state": str(result.state),
            }
        )
        self.active.pop(app, None)
        self.started.pop(app, None)
        self.wake.set()

    async def command(self, request):
        op = request.get("op")
        if op == "workspace_recovery_status":
            return self.recovery.status()
        if op == "workspace_recovery_configure":
            return self.recovery.configure(request)
        if op == "workspace_recovery_now":
            return self.background_job(
                "recovery", lambda: self.recovery.tick(force=True)
            )
        if op == "capabilities":
            return {
                "protocol": 2,
                "workspace": True,
                "browser_optional": True,
                "gmail_interval_seconds": 86400,
            }
        if op == "workspace_revision":
            return {
                "history": self.store.one(
                    "SELECT MAX(id) AS revision FROM history_events"
                )["revision"],
                "applications": self.store.one(
                    "SELECT MAX(updated) AS revision FROM applications"
                )["revision"],
                "mail": self.store.one(
                    "SELECT MAX(last_success) AS revision FROM mail_tracking"
                )["revision"],
                "expiry_clock": int(time.time() // 60),
                "recovery": self.store.one(
                    "SELECT MAX(updated) AS t FROM workspace_settings WHERE key='automatic_recovery'"
                )["t"],
                "discovery": self.store.one(
                    "SELECT MAX(updated) AS revision FROM workspace_jobs"
                )["revision"],
            }
        if op == "workspace_find":

            async def find():
                from .local_discovery import find as local_find

                return await local_find(self, request)

            return self.background_job("discovery", find, request)
        if op == "workspace_sources":
            from .local_discovery import source_list

            return source_list(self.workspace)
        if op == "workspace_discovery_status":
            return [
                {**row, "result": json.loads(row["result"])}
                for row in self.store.rows(
                    "SELECT id,kind,state,created,updated,result FROM workspace_jobs WHERE kind IN ('discovery','codex-discovery') ORDER BY created DESC LIMIT 5"
                )
            ]
        if op == "workspace_source_save":
            from .local_discovery import save_source

            return save_source(self.workspace, request["source"])
        if op == "workspace_job":
            row = self.store.one(
                "SELECT * FROM workspace_jobs WHERE id=?", (request["id"],)
            )
            if not row:
                raise ValueError("Unknown background operation")
            return {**row, "result": json.loads(row["result"])}
        if op == "workspace_cancel_job":
            task = self.background.get(request["id"])
            if task:
                task.cancel()
            return {"cancel_requested": bool(task)}
        if op == "workspace_catalog":
            from .question_catalog import load

            return load()
        if op == "workspace_import_preview":
            from .jobsignal_import import preview

            result = preview(request["path"], request.get("owner"))
            result.pop("payload", None)
            return result
        if op == "workspace_import_jobsignal":
            from .jobsignal_import import apply

            return apply(self, request)
        if op == "workspace_mail_status":
            return self.tracking.status()
        if op == "workspace_mail_configure":
            return self.tracking.configure(request)
        if op == "workspace_mail_sync":
            return self.background_job(
                "gmail", lambda: self.tracking.sync(request["connection_id"])
            )
        if op == "workspace_mail_messages":
            return self.tracking.messages()
        if op == "workspace_mail_resolve":
            return self.tracking.resolve(
                request["id"], request["application_id"], request.get("assessment_id")
            )
        if op == "workspace_codex":
            from .codex_bridge import discover

            async def codex_find():
                result = await discover(request, self.store.path.parent)
                # The model only discovers public candidate URLs. Re-fetch every source.
                from .local_discovery import find as local_find

                verified = await local_find(
                    self,
                    {
                        **request,
                        "sources": result["urls"][:10],
                        "include_builtin": False,
                    },
                )
                return verified

            if request.get("disclosure_confirmed") is not True:
                raise PermissionError(
                    "Approve sending these search criteria to Codex first"
                )
            return self.background_job("codex-discovery", codex_find, request)
        if isinstance(op, str) and op.startswith("workspace_"):
            result = self.workspace.command(request)
            if isinstance(result, dict) and result.get("state") in {
                str(s) for s in DONE
            }:
                task = self.active.get(result.get("application_id"))
                if task:
                    task.cancel()
            return result
        if op == "status":
            return {
                **self.store.snapshot(),
                "linkedin_batches": [
                    self.linkedin.status(row["id"])
                    for row in self.store.rows(
                        "SELECT id FROM linkedin_batches ORDER BY created DESC"
                    )
                ],
                "pending_profile_additions": {
                    k: len(v) for k, v in self.pending_edits.items()
                },
                "maximum_active": self.maximum_active,
                "resources": self.resources.sample(),
                "cache": {"hits": self.cache.hits, "misses": self.cache.misses},
                "model_use": {
                    "requests": self.drafting.requests,
                    "tokens": self.drafting.tokens,
                },
                "timings": list(self.timings)[-100:],
            }
        if op == "tabs":
            return await self.inventory()
        if op == "open_job_links":
            await self.ensure_browser()
            urls = request.get("urls")
            requested = request.get(
                "requested", len(urls) if isinstance(urls, list) else 0
            )
            if not isinstance(urls, list) or not urls:
                raise ValueError("Select job links")
            if type(requested) is not int or not 1 <= requested <= 10:
                raise ValueError("Requested link count must be an integer from 1 to 10")
            checked = []
            for value in urls:
                if not isinstance(value, str):
                    raise TypeError("Each job link must be a URL")
                parsed = urlsplit(value.strip())
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError("Job links must use public HTTPS URLs")
                if value.strip() not in checked:
                    checked.append(value.strip())
            if len(checked) < requested:
                raise ValueError("Not enough unique job links for the requested count")
            checked = checked[:requested]
            context = (
                self.browser.contexts[0]
                if self.browser.contexts
                else await self.browser.new_context(accept_downloads=False)
            )

            async def open_one(url):
                page = await context.new_page()
                page.set_default_navigation_timeout(30000)
                detail = "Opened"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightError:
                    detail = "Opened; loading needs attention"
                context_id = self.contexts.setdefault(context, uid())
                tab_id = uid()
                self.tabs[tab_id] = page
                return {
                    "tab_id": tab_id,
                    "context_id": context_id,
                    "url": page.url,
                    "status": detail,
                }

            return await asyncio.gather(*(open_one(url) for url in checked))
        if op == "portal_audit":
            from .adapters import choose

            page = self.tabs.get(request["tab_id"])
            if not page or page.is_closed():
                raise ValueError(
                    "Refresh managed tabs and select the exact application tab"
                )
            return await choose(page.url, testing=self.testing).audit(page)
        if op == "start":
            if request.get("original_prompt"):
                self.workspace.command(
                    {
                        "op": "workspace_save_context",
                        "content": request["original_prompt"],
                        "kind": "autofill",
                        "user_requested_save": True,
                    }
                )
            return await self.start(request["plan"])
        if op == "setup_status":
            from .onboarding import setup_status

            return setup_status(
                self.store, self.documents, str(request.get("profile_version") or "")
            )
        if op == "find_jobs":
            from .discovery import Discovery

            return await Discovery(
                self.store, testing=self.testing, browser=self.browser
            ).find(request)
        if op == "source_roots":
            return self.store.rows(
                "SELECT id,provider,name,url,enabled,created FROM source_roots ORDER BY created"
            )
        if op == "register_source":
            from .discovery import Discovery

            return Discovery(self.store, testing=self.testing).register_source(
                request.get("url"), request.get("name", "")
            )
        if op == "linkedin_easy_apply":
            await self.ensure_browser()
            return await self.linkedin.begin(request)
        if op == "linkedin_status":
            return self.linkedin.status(request["batch_id"])
        if op == "linkedin_resume":
            await self.ensure_browser()
            return await self.linkedin.resume(request["batch_id"])
        if op == "discover":
            from .discovery import Discovery

            return await Discovery(
                self.store, testing=self.testing, browser=self.browser
            ).discover(RunPlan.parse(request["plan"]))
        if op == "profiles":
            return self.store.rows(
                "SELECT id,profile_id,provenance,created FROM profile_versions ORDER BY created DESC"
            )
        if op == "profile":
            return self.store.load_profile(request["version"])
        if op == "save_profile":
            return {
                "version": self.store.profile(
                    request["payload"],
                    parent=request.get("parent"),
                    provenance="user-confirmed questionnaire",
                )
            }
        if op == "documents":
            return self.store.rows("SELECT * FROM documents ORDER BY created DESC")
        if op == "document_preview":
            from .document_text import preview

            return await preview(self.store, self.cache, request["document_id"])
        if op == "register_document":
            return {
                "document_id": self.documents.register(
                    request["path"],
                    request["kind"],
                    approved=request.get("approved") is True,
                    applicability=request.get("applicability", {}),
                )
            }
        if op == "submitted":
            app = request["application_id"]
            self.workspace.assert_app(app)
            row = self.store.one(
                "SELECT a.id,v.identity,v.url,v.role,e.name AS employer FROM applications a JOIN vacancies v ON v.id=a.vacancy_id JOIN employers e ON e.id=v.employer_id WHERE a.id=?",
                (app,),
            )
            result = self.workspace.applied(row)
            worker = self.active.get(app)
            if worker:
                worker.cancel()
            return result
        if op in {"pause", "stop", "resume"}:
            run = request["run_id"]
            if op == "resume":
                row = self.store.one("SELECT plan FROM runs WHERE id=?", (run,))
                if not row:
                    raise ValueError("Unknown run")
                RunPlan.parse(json.loads(row["plan"]))
                if self.store.one("SELECT revoked FROM grants WHERE run_id=?", (run,))[
                    "revoked"
                ]:
                    raise ValueError("Stopped run requires new approval")
            with self.store.tx():
                self.store.db.execute(
                    "UPDATE runs SET status=? WHERE id=?",
                    (
                        {"pause": "PAUSED", "stop": "STOPPED", "resume": "ACTIVE"}[op],
                        run,
                    ),
                )
                if op == "stop":
                    self.store.db.execute(
                        "UPDATE grants SET revoked=1 WHERE run_id=?", (run,)
                    )
                if op == "resume":
                    self.store.db.execute(
                        "UPDATE tasks SET status='QUEUED' WHERE run_id=? AND status='PARKED' AND application_id IN (SELECT id FROM applications WHERE state NOT IN ('SUBMITTED_CONFIRMED','SUBMITTED_USER_REPORTED','SUBMISSION_UNCONFIRMED','SUBMITTING'))",
                        (run,),
                    )
            self.wake.set()
            return {"status": op}
        if op == "open":
            page = self.pages.get(request["application_id"])
            if not page or page.is_closed():
                raise ValueError("Select the application tab again")
            await page.bring_to_front()
            return {"opened": True}
        if op == "release":
            app = request["application_id"]
            if app in self.active:
                raise ValueError(
                    "Pause and wait for the active operation before releasing"
                )
            if request.get("confirmed") is not True:
                raise ValueError("Explicitly confirm closing the retained form")
            page = self.pages.pop(app, None)
            if page and not page.is_closed():
                await page.close()
            self.store.db.execute(
                "UPDATE sessions SET retained=0 WHERE application_id=?", (app,)
            )
            self.wake.set()
            return {"released": True}
        if op == "review":
            app = request["application_id"]
            row = self.store.one(
                "SELECT payload FROM checkpoints WHERE application_id=?", (app,)
            )
            return json.loads(row["payload"]) if row else {}
        if op == "detect_edits":
            app = request["application_id"]
            page = self.pages.get(app)
            if app in self.active:
                raise ValueError("Wait for the active operation before detecting edits")
            if not page or page.is_closed():
                raise ValueError("Open application required")
            current = await inspect(page)
            row = self.store.one(
                "SELECT payload FROM checkpoints WHERE application_id=?", (app,)
            )
            old = json.loads(row["payload"]).get("fields", []) if row else []
            previous = {
                (f["field_id"], f.get("record", "")): f.get("observed_value")
                for f in old
            }
            changes = [
                r
                for r in current
                if previous.get((r["field_id"], r.get("record", "")))
                != r.get("observed_value")
            ]
            self.pending_edits[app] = changes
            return {"changes": changes, "confirmation_required": True}
        if op == "migration_preview":
            from .resources import legacy_preview

            return legacy_preview(request["paths"])
        if op == "import_legacy":
            from .resources import import_legacy

            return {
                "version": import_legacy(
                    self.store,
                    request["preview"],
                    confirmed=request.get("confirmed") is True,
                )
            }
        if op == "bind_tab":
            app = request["application_id"]
            tab = request["tab_id"]
            if request.get("confirmed") is not True:
                raise ValueError("Confirm the selected tab belongs to this application")
            if app in self.active:
                raise ValueError("Pause the application before changing its tab")
            page = self.tabs.get(tab)
            if not page or page.is_closed():
                raise ValueError("Refresh the managed-tab list")
            if any(p is page for id, p in self.pages.items() if id != app):
                raise ValueError("Tab already belongs to another application")
            ra = self.store.one(
                "SELECT ra.* FROM run_applications ra JOIN runs r ON r.id=ra.run_id WHERE application_id=? ORDER BY r.created DESC LIMIT 1",
                (app,),
            )
            target = json.loads(ra["target"])
            if urlsplit(page.url).netloc != urlsplit(target["url"]).netloc:
                raise ValueError(
                    "Different employer origin requires a new explicitly approved target"
                )
            target.update(tab_id=tab, url=page.url)
            self.store.db.execute(
                "UPDATE run_applications SET target=? WHERE run_id=? AND application_id=?",
                (encode(target), ra["run_id"], app),
            )
            self.pages[app] = page
            context_id = self.contexts.setdefault(page.context, uid())
            self.store.db.execute(
                "INSERT OR REPLACE INTO sessions VALUES(?,?,?,1)",
                (app, tab, context_id),
            )
            self.store.event(app, "user-bound-tab", {"tab_id": tab})
            return {"bound": True}
        if op == "resume_application":
            app = request["application_id"]
            row = self.store.one(
                "SELECT t.*,r.status AS run_status,g.revoked,g.expires FROM tasks t JOIN runs r ON r.id=t.run_id JOIN grants g ON g.run_id=r.id WHERE t.application_id=? ORDER BY r.created DESC LIMIT 1",
                (app,),
            )
            state = self.store.one("SELECT state FROM applications WHERE id=?", (app,))
            if not row or not state:
                raise ValueError("Unknown application")
            if state["state"] in {
                "SUBMITTED_CONFIRMED",
                "SUBMITTED_USER_REPORTED",
                "SUBMISSION_UNCONFIRMED",
                "SUBMITTING",
            }:
                raise ValueError("Completed or uncertain submission cannot be replayed")
            if row["revoked"] or row["expires"] <= time.time():
                raise ValueError(
                    "Approve a new run; previous authority stopped or expired"
                )
            self.store.db.execute(
                "UPDATE runs SET status='ACTIVE' WHERE id=?", (row["run_id"],)
            )
            self.store.db.execute(
                "UPDATE tasks SET status='QUEUED' WHERE id=? AND status='PARKED'",
                (row["id"],),
            )
            self.wake.set()
            return {"queued": True}
        if op in {"answer_questions", "adopt_edits"}:
            from ..field_manifest import FieldKind, classify_field

            by_app = {}
            if op == "answer_questions":
                for q in self.store.rows(
                    "SELECT * FROM missing_questions WHERE resolved=0"
                ):
                    answer = request.get("answers", {}).get(q["question_key"])
                    if answer is not None and str(answer).strip():
                        field = json.loads(q["payload"])
                        if field["kind"] not in {"information", "conflict"}:
                            raise ValueError(
                                "This item needs a form or document action"
                            )
                        by_app.setdefault(q["application_id"], []).append(
                            {**field, "observed_value": str(answer)}
                        )
            else:
                app = request["application_id"]
                approved = self.pending_edits.pop(app, None)
                if approved is None:
                    raise ValueError("Detect and review changes first")
                latest = await inspect(self.pages[app])
                latest_map = {
                    (f["field_id"], f.get("record", "")): f.get("observed_value")
                    for f in latest
                }
                if any(
                    latest_map.get((f["field_id"], f.get("record", "")))
                    != f.get("observed_value")
                    for f in approved
                ):
                    raise ValueError("Form changed after preview; detect edits again")
                by_app[app] = approved
            versions = []
            reusable = {
                FieldKind.FIRST_NAME: "first_name",
                FieldKind.LAST_NAME: "last_name",
                FieldKind.FULL_NAME: "full_name",
                FieldKind.EMAIL: "email",
                FieldKind.PHONE: "phone",
                FieldKind.LOCATION: "location",
                FieldKind.LINKEDIN: "linkedin_url",
            }
            for app, fields in by_app.items():
                if app in self.active:
                    raise ValueError(
                        "Wait for this application to park before applying confirmed changes"
                    )
                ra = self.store.one(
                    "SELECT ra.* FROM run_applications ra JOIN runs r ON r.id=ra.run_id WHERE ra.application_id=? ORDER BY r.created DESC LIMIT 1",
                    (app,),
                )
                profile = self.store.load_profile(ra["profile_version"])
                target = json.loads(ra["target"])
                for f in fields:
                    kind = classify_field(
                        f["question"],
                        field_id=f["field_id"],
                        section=f.get("section", ""),
                    )
                    if kind in {
                        FieldKind.SIGNATURE,
                        FieldKind.DECLARATION,
                        FieldKind.CONSENT,
                        FieldKind.DEMOGRAPHIC,
                    }:
                        continue
                    value = f.get("observed_value", "")
                    if not value:
                        continue
                    profile.setdefault("application_answers", {}).setdefault(
                        target["identity"], {}
                    )[f["field_id"]] = value
                    if (
                        (request.get("reuse") or op == "adopt_edits")
                        and kind in reusable
                        and not f.get("record")
                    ):
                        profile[reusable[kind]] = value
                        if (
                            "_confirmed_fields" in profile
                            and reusable[kind] not in profile["_confirmed_fields"]
                        ):
                            profile["_confirmed_fields"].append(reusable[kind])
                version = self.store.profile(
                    profile,
                    parent=ra["profile_version"],
                    provenance="user-confirmed grouped additions; changed application requires new submission approval",
                )
                self.store.db.execute(
                    "UPDATE run_applications SET profile_version=? WHERE run_id=? AND application_id=?",
                    (version, ra["run_id"], app),
                )
                self.store.event(
                    app,
                    "profile-reconciled",
                    {"version": version, "submission_approval_invalidated": True},
                )
                versions.append(version)
                await self.command({"op": "resume_application", "application_id": app})
            return {"versions": versions, "applications_resumed": len(by_app)}
        if op == "ai_configure":
            from .secrets import NativeSecrets

            NativeSecrets().put(
                "ApplyPilot:AI", "openai", request["api_key"], replace=True
            )
            return {"configured": True}
        if op == "account_registration_policy":
            self.store.db.execute(
                "INSERT OR REPLACE INTO account_policies VALUES(?,?,?,?,?)",
                (
                    request["employer"],
                    request["origin"],
                    request["email"],
                    request["mailbox"],
                    encode(request["rules"]),
                ),
            )
            return {"saved": True}
        if op == "account_add":
            raise ValueError(
                "Employer passwords are not collected or stored; sign in on the employer website"
            )
        if op == "gmail_job":
            return self.mail_jobs.get(request["job_id"], {"state": "UNKNOWN"})
        if op == "gmail_connections":
            return self.store.rows("SELECT id,email,status FROM mail_connections")
        if op in {"gmail_connect", "gmail_status", "gmail_disconnect", "gmail_revoke"}:
            from .mail import Gmail

            gmail = Gmail(self.store)
            if op == "gmail_connect":
                config = json.loads(
                    Path(request["config_path"]).expanduser().read_text()
                )
                job = uid()
                self.mail_jobs[job] = {"state": "CONNECTING"}
                email = request["email"].casefold()
                generation = uid()
                self.mail_generations[email] = generation
                # Completed connection statuses are bounded; tokens never enter this map.
                while len(self.mail_jobs) > 32:
                    self.mail_jobs.pop(next(iter(self.mail_jobs)))

                async def connect():
                    try:
                        self.mail_jobs[job] = await gmail.connect(
                            config,
                            request["email"],
                            is_current=lambda: (
                                self.mail_generations.get(email) == generation
                            ),
                        )
                    except Exception:  # noqa: BLE001 - report reconnect without secret details
                        self.mail_jobs[job] = {
                            "state": "RECONNECT",
                            "detail": "Connection failed, denied or timed out",
                        }

                asyncio.create_task(connect())
                return {
                    "job_id": job,
                    "state": "CONNECTING",
                    "next_action": "Complete system-browser consent, then check Gmail status",
                }
            if op in {"gmail_disconnect", "gmail_revoke"}:
                self.mail_generations[request["connection_id"].casefold()] = uid()
                if op == "gmail_revoke":
                    return await gmail.revoke(request["connection_id"])
                return gmail.disconnect(request["connection_id"])
            return await gmail.status(request["connection_id"])
        if op == "qualification":
            from .adapters import ADAPTERS

            if request["adapter"] not in {a.name for a in ADAPTERS}:
                raise ValueError("Unknown provider adapter")
            if request["level"] not in {"SYNTHETIC", "SUPERVISED", "UNATTENDED"}:
                raise ValueError("Invalid qualification level")
            evidence = Path(request["evidence"]).expanduser().resolve(strict=True)
            if request.get("confirmed") is not True:
                raise ValueError("Confirm this exact workflow evidence")
            from .qualification import record

            record(self.store, request["adapter"], request["level"], evidence)
            return {"recorded": True}
        if op == "doctor":
            return {
                **self.store.doctor(),
                "browser_connected": bool(self.browser and self.browser.is_connected()),
                "mail_required_for_fill": False,
            }
        if op == "cache_clear":
            self.cache.clear()
            return {"cleared": "derived cache only"}
        if op == "backup":
            return {"path": self.store.backup(request["destination"])}
        raise ValueError("Unsupported command")


async def serve(home=None, cdp="http://127.0.0.1:9333"):
    from playwright.async_api import async_playwright

    home = Path(home or default_home()).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    lock = (home / "runtime.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("A foreground runtime already owns this directory")
    socket = home / "runtime.sock"
    if len(os.fsencode(socket)) > 103:
        lock.close()
        raise ValueError(
            "Runtime directory is too long for a macOS Unix socket; choose a shorter local path"
        )
    if socket.exists():
        socket.unlink()
    store = Store(home / "applypilot.sqlite3")
    store.recover()
    async with async_playwright() as p:
        runtime = Runtime(
            store,
            None,
            browser_factory=lambda: p.chromium.connect_over_cdp(cdp, timeout=10000),
        )
        store.db.execute(
            "UPDATE workspace_jobs SET state='FAILED',result=?,updated=? WHERE state='RUNNING'",
            (
                encode({"detail": "Runtime restarted; retry the bounded operation"}),
                time.time(),
            ),
        )

        async def handle(reader, writer):
            try:
                data = await asyncio.wait_for(reader.readline(), timeout=10)
                if len(data) > 1024 * 1024:
                    raise ValueError("Request exceeds size limit")
                request = json.loads(data)
                result = await runtime.command(request)
                response = {"ok": True, "result": result}
            except (ValueError, PermissionError) as e:
                response = {"ok": False, "error": str(e)}
            except Exception:  # noqa: BLE001 - local socket returns a generic safe error
                response = {
                    "ok": False,
                    "error": "Runtime operation failed; inspect doctor/status",
                }
            writer.write((encode(response) + "\n").encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, str(socket), limit=1024 * 1024)
        os.chmod(socket, 0o600)
        pump = asyncio.create_task(runtime.pump())
        tracking = asyncio.create_task(runtime.tracking.run())
        recovery = asyncio.create_task(runtime.recovery.run())
        print(
            "ApplyPilot foreground runtime ready. Ctrl-C stops scheduling; browser owner remains open.",
            flush=True,
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            runtime.closed = True
            pump.cancel()
            tracking.cancel()
            recovery.cancel()
            for worker in list(runtime.active.values()):
                worker.cancel()
            for task in list(runtime.background.values()):
                task.cancel()
            await asyncio.gather(
                pump,
                tracking,
                recovery,
                *runtime.active.values(),
                *runtime.background.values(),
                return_exceptions=True,
            )
            store.close()
            socket.unlink(missing_ok=True)
            lock.close()


def client(request, home=None):
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(
        240
        if request.get("op") == "gmail"
        and request.get("action") in {"connect", "reconnect"}
        else 30
    )
    try:
        sock.connect(str(Path(home or default_home()) / "runtime.sock"))
        sock.sendall((encode(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise RuntimeError("Runtime disconnected")
            data += chunk
            if len(data) > 16 * 1024 * 1024:
                raise RuntimeError("Runtime response too large")
        result = json.loads(data)
        if not result["ok"]:
            raise ValueError(result["error"])
        return result["result"]
    finally:
        sock.close()
