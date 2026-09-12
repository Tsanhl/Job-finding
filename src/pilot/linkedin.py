"""Async LinkedIn Easy Apply preparation owned by the foreground runtime."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode, urlsplit

from .models import FinalAction, FunctionKind, RunPlan, Scope, Target
from .onboarding import setup_status
from .store import uid


def search_url(keywords, location):
    return "https://www.linkedin.com/jobs/search/?" + urlencode(
        {
            "keywords": keywords,
            "location": location,
            "f_AL": "true",
            "f_E": "1,2",
            "f_TPR": "r604800",
        }
    )


def job_id(url):
    match = re.search(r"/jobs/view/(\d+)", str(url))
    return match.group(1) if match else ""


def normalized_job_url(value):
    identity = job_id(value)
    return f"https://www.linkedin.com/jobs/view/{identity}" if identity else ""


class LinkedInCoordinator:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store

    async def begin(self, request):
        keywords = str(request.get("keywords") or "").strip()
        location = str(request.get("location") or "").strip()
        requested = request.get("requested", 1)
        if not keywords or len(keywords) > 300:
            raise ValueError("Enter LinkedIn role keywords of 1–300 characters")
        if not location or len(location) > 200:
            raise ValueError("Enter a LinkedIn location of 1–200 characters")
        if type(requested) is not int or not 1 <= requested <= 10:
            raise ValueError("LinkedIn requested count must be an integer from 1 to 10")
        profile_version = str(request.get("profile_version") or "")
        readiness = setup_status(
            self.store, self.runtime.documents, profile_version
        )
        if not readiness["ready"]:
            raise ValueError(
                "Complete reusable profile setup: " + "; ".join(readiness["missing"])
            )
        profile_version = readiness["profile_version"]
        documents = request.get("documents") or []
        if not documents:
            documents = [
                self.store.one(
                    "SELECT id FROM documents WHERE kind='cv' AND approved=1 ORDER BY created DESC LIMIT 1"
                )["id"]
            ]
        if not isinstance(documents, list):
            raise TypeError("documents must be a list of approved document IDs")
        context = self._context()
        context_id = self.runtime.contexts.setdefault(context, uid())
        page = next(
            (
                item
                for item in context.pages
                if (urlsplit(item.url).hostname or "").endswith("linkedin.com")
                and "/jobs/search" in item.url
            ),
            None,
        )
        if page is None:
            page = await context.new_page()
        await page.goto(
            search_url(keywords, location),
            wait_until="domcontentloaded",
            timeout=30000,
        )
        if await self._authentication_required(page):
            return {
                "status": "NEEDS_AUTHENTICATION",
                "detail": "Sign in to LinkedIn yourself, complete any checkpoint, then start this function again",
            }
        attempt_limit = min(requested * 3, 30)
        urls = await self._collect(page, attempt_limit)
        batch = uid()
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO linkedin_batches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch,
                    None,
                    keywords,
                    location,
                    requested,
                    attempt_limit,
                    profile_version,
                    json.dumps(documents),
                    "ACTIVE",
                    context_id,
                    time.time(),
                ),
            )
            ordinal = 0
            for url in urls:
                identity = "linkedin:" + job_id(url)
                duplicate = self.store.one(
                    "SELECT a.id FROM vacancies v JOIN applications a ON a.vacancy_id=v.id WHERE v.identity=?",
                    (identity,),
                )
                self.store.db.execute(
                    "INSERT INTO linkedin_candidates VALUES(?,?,?,?,?,?,?,NULL,?)",
                    (
                        batch,
                        ordinal,
                        identity,
                        url,
                        "",
                        "",
                        "SKIPPED" if duplicate else "QUEUED",
                        "Existing application" if duplicate else "",
                    ),
                )
                ordinal += 1
        if not self.store.one(
            "SELECT 1 FROM linkedin_candidates WHERE batch_id=?", (batch,)
        ):
            self.store.db.execute(
                "UPDATE linkedin_batches SET status='EXHAUSTED' WHERE id=?", (batch,)
            )
            return self.status(batch)
        await self.expand(batch, maximum=1)
        return self.status(batch)

    def _context(self):
        for context in self.runtime.browser.contexts:
            if any(
                (urlsplit(page.url).hostname or "").endswith("linkedin.com")
                for page in context.pages
            ):
                return context
        if self.runtime.browser.contexts:
            return self.runtime.browser.contexts[0]
        raise ValueError(
            "Open the managed browser and sign in to LinkedIn before starting Easy Apply"
        )

    async def _authentication_required(self, page):
        host = (urlsplit(page.url).hostname or "").casefold()
        path = urlsplit(page.url).path.casefold()
        return bool(
            "linkedin.com" not in host
            or any(part in path for part in ("/login", "/checkpoint", "/authwall"))
            or await page.locator("input[type='password']").count()
        )

    async def _collect(self, page, limit):
        found = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(found) < limit:
            hrefs = await page.locator("a[href*='/jobs/view/']").evaluate_all(
                "els=>els.map(el=>el.href)"
            )
            for href in hrefs:
                url = normalized_job_url(href)
                if url and url not in found:
                    found.append(url)
            if len(found) >= limit:
                break
            await page.mouse.wheel(0, 2400)
            await page.wait_for_timeout(500)
            if found and time.monotonic() + 2 >= deadline:
                break
        return found[:limit]

    async def expand_active(self):
        for row in self.store.rows(
            "SELECT id FROM linkedin_batches WHERE status IN ('ACTIVE','CAPACITY_WAIT') ORDER BY created"
        ):
            try:
                await self.expand(row["id"], maximum=1)
            except Exception:
                # Keep unrelated application workers moving and retry this batch
                # on the next foreground pump after its local state is reconciled.
                self.store.db.execute(
                    "UPDATE linkedin_batches SET status='CAPACITY_WAIT' WHERE id=?",
                    (row["id"],),
                )

    async def expand(self, batch_id, *, maximum=1):
        batch = self.store.one(
            "SELECT * FROM linkedin_batches WHERE id=?", (batch_id,)
        )
        if not batch or batch["status"] not in {"ACTIVE", "CAPACITY_WAIT"}:
            return
        summary = self.status(batch_id)
        if summary["review_ready"] >= batch["requested"]:
            self.store.db.execute(
                "UPDATE linkedin_batches SET status='DONE' WHERE id=?", (batch_id,)
            )
            self.store.db.execute(
                "UPDATE linkedin_candidates SET status='CANCELLED',detail='Requested review-ready count reached' WHERE batch_id=? AND status='QUEUED'",
                (batch_id,),
            )
            return
        in_flight = self.store.one(
            "SELECT COUNT(*) AS n FROM linkedin_candidates c LEFT JOIN applications a ON a.id=c.application_id WHERE c.batch_id=? AND (c.status='PREPARING' OR a.state IN ('QUEUED','FILLING','PAGE_FILLED','FAILED_RETRYABLE'))",
            (batch_id,),
        )["n"]
        if summary["review_ready"] + in_flight >= batch["requested"]:
            return
        retained = self.store.one(
            "SELECT COUNT(*) AS n FROM linkedin_candidates c JOIN sessions s ON s.application_id=c.application_id WHERE c.batch_id=? AND s.retained=1",
            (batch_id,),
        )["n"]
        if retained >= 10:
            self.store.db.execute(
                "UPDATE linkedin_batches SET status='CAPACITY_WAIT' WHERE id=?",
                (batch_id,),
            )
            return
        prepared = 0
        while prepared < maximum and retained + prepared < 10:
            candidate = self.store.one(
                "SELECT * FROM linkedin_candidates WHERE batch_id=? AND status='QUEUED' ORDER BY ordinal LIMIT 1",
                (batch_id,),
            )
            if not candidate:
                active = self.store.one(
                    "SELECT 1 FROM linkedin_candidates c LEFT JOIN applications a ON a.id=c.application_id WHERE c.batch_id=? AND (c.status='PREPARING' OR a.state IN ('QUEUED','FILLING','PAGE_FILLED','FAILED_RETRYABLE'))",
                    (batch_id,),
                )
                if active:
                    return
                blocked = self.store.one(
                    "SELECT 1 FROM linkedin_candidates c LEFT JOIN applications a ON a.id=c.application_id WHERE c.batch_id=? AND (c.status='BLOCKED' OR a.state IN ('NEEDS_INFORMATION','NEEDS_AUTHENTICATION','INCOMPLETE','POLICY_BLOCKED'))",
                    (batch_id,),
                )
                self.store.db.execute(
                    "UPDATE linkedin_batches SET status=? WHERE id=?",
                    ("CAPACITY_WAIT" if blocked else "EXHAUSTED", batch_id),
                )
                return
            await self._prepare(batch, candidate)
            prepared += 1

    async def _prepare(self, batch, candidate):
        self.store.db.execute(
            "UPDATE linkedin_candidates SET status='PREPARING' WHERE batch_id=? AND identity=?",
            (batch["id"], candidate["identity"]),
        )
        context = next(
            (
                value
                for value, identity in self.runtime.contexts.items()
                if identity == batch["context_id"]
            ),
            None,
        )
        if context is None:
            self.store.db.execute(
                "UPDATE linkedin_batches SET status='NEEDS_AUTHENTICATION' WHERE id=?",
                (batch["id"],),
            )
            return
        page = await context.new_page()
        try:
            await page.goto(
                candidate["url"], wait_until="domcontentloaded", timeout=30000
            )
            if await self._authentication_required(page):
                await page.close()
                self.store.db.execute(
                    "UPDATE linkedin_candidates SET status='BLOCKED',detail='LinkedIn sign-in required' WHERE batch_id=? AND identity=?",
                    (batch["id"], candidate["identity"]),
                )
                self.store.db.execute(
                    "UPDATE linkedin_batches SET status='NEEDS_AUTHENTICATION' WHERE id=?",
                    (batch["id"],),
                )
                return
            role = await self._text(
                page,
                "h1,.job-details-jobs-unified-top-card__job-title",
                "LinkedIn Easy Apply role",
            )
            employer = await self._text(
                page,
                ".job-details-jobs-unified-top-card__company-name,a.job-details-jobs-unified-top-card__company-name",
                "LinkedIn employer",
            )
            easy = page.locator(
                "button[aria-label*='Easy Apply' i],a[aria-label*='Easy Apply' i],button:text-is('Easy Apply'),button:text-is('Continue applying')"
            )
            visible_easy = [
                easy.nth(index)
                for index in range(await easy.count())
                if await easy.nth(index).is_visible()
            ]
            if len(visible_easy) != 1:
                await page.close()
                self.store.db.execute(
                    "UPDATE linkedin_candidates SET status='UNSUPPORTED',detail='No unique visible Easy Apply control' WHERE batch_id=? AND identity=?",
                    (batch["id"], candidate["identity"]),
                )
                return
            await visible_easy[0].click(timeout=10000)
            try:
                await page.locator("div[role='dialog']").wait_for(
                    state="visible", timeout=10000
                )
            except Exception:
                await page.close()
                self.store.db.execute(
                    "UPDATE linkedin_candidates SET status='UNSUPPORTED',detail='Easy Apply form did not open' WHERE batch_id=? AND identity=?",
                    (batch["id"], candidate["identity"]),
                )
                return
            tab_id = uid()
            self.runtime.tabs[tab_id] = page
            target = Target(
                page.url,
                employer,
                role,
                candidate["identity"],
                scope=Scope.EXISTING_APPLICATION,
                tab_id=tab_id,
                account="linkedin:" + batch["context_id"],
                adapter="linkedin",
                final_action=FinalAction.REVIEW,
            )
            permissions = ["fill", "session"]
            documents = json.loads(batch["documents"])
            if documents:
                permissions.append("upload")
            raw = {
                "request_schema": 2,
                "function": FunctionKind.LINKEDIN_EASY_APPLY,
                "targets": [target.__dict__],
                "profile_version": batch["profile_version"],
                "documents": documents,
                "permissions": permissions,
                "workers": 1,
                "result_limit": batch["requested"],
                "approval": "Explicit local LinkedIn Easy Apply request",
                "expires_at": time.time() + 7200,
            }
            plan = RunPlan.parse(raw)
            if batch["run_id"]:
                app = self.store.add_target(batch["run_id"], plan, target)
                run_id = batch["run_id"]
                self.runtime.wake.set()
            else:
                started = await self.runtime.start(raw)
                run_id = started["run_id"]
                app = started["applications"][0]
                self.store.db.execute(
                    "UPDATE linkedin_batches SET run_id=? WHERE id=?",
                    (run_id, batch["id"]),
                )
            self.store.db.execute(
                "UPDATE linkedin_candidates SET employer=?,role=?,status='PREPARED',application_id=?,detail='Easy Apply form opened' WHERE batch_id=? AND identity=?",
                (employer, role, app, batch["id"], candidate["identity"]),
            )
        except Exception:
            if not page.is_closed():
                await page.close()
            self.store.db.execute(
                "UPDATE linkedin_candidates SET status='BLOCKED',detail='Technical preparation failure; Resume to retry' WHERE batch_id=? AND identity=?",
                (batch["id"], candidate["identity"]),
            )

    async def resume(self, batch_id):
        batch = self.store.one(
            "SELECT * FROM linkedin_batches WHERE id=?", (batch_id,)
        )
        if not batch:
            raise ValueError("Unknown LinkedIn batch")
        if batch["status"] not in {"NEEDS_AUTHENTICATION", "CAPACITY_WAIT"}:
            return self.status(batch_id)
        context = self._context()
        context_id = self.runtime.contexts.setdefault(context, uid())
        page = next(
            (
                item
                for item in context.pages
                if (urlsplit(item.url).hostname or "").endswith("linkedin.com")
            ),
            None,
        )
        if page is None:
            page = await context.new_page()
            await page.goto(
                search_url(batch["query"], batch["location"]),
                wait_until="domcontentloaded",
                timeout=30000,
            )
        if await self._authentication_required(page):
            return self.status(batch_id)
        with self.store.tx():
            self.store.db.execute(
                "UPDATE linkedin_candidates SET status='QUEUED',detail='' WHERE batch_id=? AND status='BLOCKED' AND application_id IS NULL",
                (batch_id,),
            )
            self.store.db.execute(
                "UPDATE linkedin_batches SET status='ACTIVE',context_id=? WHERE id=?",
                (context_id, batch_id),
            )
        await self.expand(batch_id, maximum=1)
        return self.status(batch_id)

    async def _text(self, page, selector, fallback):
        locator = page.locator(selector).first
        if await locator.count():
            value = " ".join((await locator.inner_text()).split())
            if value:
                return value[:300]
        return fallback

    def status(self, batch_id):
        batch = self.store.one(
            "SELECT * FROM linkedin_batches WHERE id=?", (batch_id,)
        )
        if not batch:
            raise ValueError("Unknown LinkedIn batch")
        candidates = self.store.rows(
            "SELECT c.status AS candidate_status,c.detail,a.state AS application_state FROM linkedin_candidates c LEFT JOIN applications a ON a.id=c.application_id WHERE c.batch_id=?",
            (batch_id,),
        )
        states = [row["application_state"] for row in candidates]
        return {
            "batch_id": batch_id,
            "run_id": batch["run_id"],
            "status": batch["status"],
            "requested": batch["requested"],
            "attempt_limit": batch["attempt_limit"],
            "attempted": sum(
                row["candidate_status"] not in {"QUEUED", "CANCELLED"}
                for row in candidates
            ),
            "skipped": sum(
                row["candidate_status"] == "SKIPPED" for row in candidates
            ),
            "review_ready": states.count("REVIEW_READY"),
            "blocked": sum(
                row["candidate_status"] == "BLOCKED"
                or row["application_state"]
                in {
                    "NEEDS_INFORMATION",
                    "NEEDS_AUTHENTICATION",
                    "INCOMPLETE",
                    "POLICY_BLOCKED",
                }
                for row in candidates
            ),
            "unsupported": sum(
                row["candidate_status"] == "UNSUPPORTED"
                or row["application_state"] == "UNSUPPORTED"
                for row in candidates
            ),
            "queued": sum(row["candidate_status"] == "QUEUED" for row in candidates),
            "next_action": (
                "Sign in to LinkedIn in the managed browser, then Resume this batch"
                if batch["status"] == "NEEDS_AUTHENTICATION"
                else (
                    "Open a retained form, answer grouped questions, or Resume this batch"
                    if batch["status"] == "CAPACITY_WAIT"
                    else "No action required while the runtime continues"
                )
            ),
        }
