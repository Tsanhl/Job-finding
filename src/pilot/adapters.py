"""Capability contracts. Brand detection alone never qualifies submission."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Adapter:
    name: str
    hosts: tuple[str, ...] = ()
    root: str = "form, main, body"
    next_selector: str = ""
    review_selector: str = ""
    submit_selector: str = ""
    receipt_selector: str = ""
    attachment_selector: str = ""
    record_add_selector: str = ""
    record_save_selector: str = ""
    implemented_submission: bool = False

    async def attachment_evidence(self, page, field_id, filename):
        if not self.attachment_selector:
            return None
        candidates = page.locator(self.attachment_selector).filter(has_text=filename)
        if await candidates.count() != 1 or not await candidates.is_visible():
            return None
        data = await candidates.evaluate(
            """el=>({server_id:el.dataset.serverId||el.dataset.attachmentId||el.closest('[data-attachment-id]')?.dataset.attachmentId||'',slot:el.dataset.slot||'',href:el.getAttribute('href')||''})"""
        )
        if self.name == "linkedin":
            pending = page.locator(
                "div[role='dialog'] [role='progressbar'],"
                "div[role='dialog'] .artdeco-loader"
            )
            if not await pending.count():
                return {"linkedin_saved": True, "slot": field_id}
        if data["slot"] and data["slot"] != field_id:
            return None
        # A server-issued attachment identifier or same-origin attachment URL is
        # required in addition to the displayed filename. File selection is not
        # evidence that an async upload finished.
        if data["server_id"]:
            return data
        from urllib.parse import urljoin

        if data["href"]:
            url = urlsplit(urljoin(page.url, data["href"]))
            if url.netloc == urlsplit(page.url).netloc and any(
                x in url.path.lower() for x in ("attachment", "document", "download")
            ):
                return {"attachment_path": url.path, "slot": data["slot"]}
        return None

    async def next_action(self, page):
        if self.name == "linkedin":
            if await self.is_review(page):
                return None
            for selector in (
                "button[aria-label='Continue to next step']",
                "button[aria-label='Review your application']",
                "button:text-is('Next')",
                "button:text-is('Continue')",
                "button:text-is('Review')",
            ):
                candidate = page.locator("div[role='dialog']").locator(selector)
                visible = [
                    candidate.nth(index)
                    for index in range(await candidate.count())
                    if await candidate.nth(index).is_visible()
                ]
                if len(visible) == 1:
                    if (
                        await visible[0].get_attribute("type") or "button"
                    ).lower() != "button":
                        return None
                    return visible[0]
            return None
        if self.name in {"allhires", "apply4law"}:
            if await self.is_review(page):
                return None
            from .portal_actions import family_next

            return await family_next(page)
        if not self.next_selector:
            return None
        loc = page.locator(self.next_selector)
        if await loc.count() != 1 or not await loc.is_visible():
            return None
        if (await loc.get_attribute("type") or "").lower() != "button":
            return None
        text = (await loc.inner_text()).strip().lower()
        if text not in {"next", "save and continue", "continue", "review"}:
            return None
        # Final review is never advanced by the filling service.
        if self.review_selector and await page.locator(self.review_selector).count():
            return None
        return loc

    async def audit(self, page):
        from .portal_records import SECTIONS

        next_control = await self.next_action(page)
        return {
            "adapter": self.name,
            "non_final_navigation_recognised": next_control is not None,
            "review_recognised": await self.is_review(page),
            "submission_implemented": self.implemented_submission,
            "submission_control_recognised": bool(
                self.submit_selector
                and await page.locator(self.submit_selector).count() == 1
            ),
            "record_sections": {
                kind: await page.locator(selector).count()
                for kind, selector in SECTIONS.items()
            },
            "live_qualification": "Check stored qualification evidence; page recognition alone is not qualification",
        }

    async def advance(self, page, action, guard):
        if self.name in {"allhires", "apply4law"}:
            from .portal_actions import execute

            await execute(page, action, "next", guard)
        else:
            guard()
            await action.click(timeout=15000)

    async def is_review(self, page):
        return bool(
            self.review_selector
            and await page.locator(self.review_selector).count() == 1
            and await page.locator(self.review_selector).is_visible()
        )

    async def identity_matches(self, page, target):
        if self.name == "fixture":
            return True
        if self.name in {"allhires", "apply4law"}:
            identity = page.locator(
                'input[name$="ApplicationID"],input[name$="ApplicationId"]'
            )
            exact_identity = (
                await identity.count() == 1
                and await identity.input_value()
                == (target.portal_application_id or target.identity)
            )
            return bool(
                exact_identity
                and target.account
                and await page.get_by_text(target.account, exact=True).count()
                and await page.get_by_text(target.role, exact=True).count()
            )
        if self.name == "workday":
            role = page.get_by_text(target.role, exact=True)
            account = (
                page.get_by_text(target.account, exact=True) if target.account else None
            )
            return bool(
                target.identity in page.url
                and await role.count()
                and account
                and await account.count()
            )
        return False

    async def receipt(self, page, target):
        if not self.receipt_selector:
            return None
        if self.name in {"allhires", "apply4law"}:
            status = page.locator(self.receipt_selector)
            reference = page.locator('[id$="ApplicationReference"]')
            if (
                await status.count() != 1
                or await reference.count() != 1
                or not await self.identity_matches(page, target)
            ):
                return None
            if "submitted" not in (await status.inner_text()).lower():
                return None
            value = (await reference.inner_text()).strip()
            return (
                {
                    "reference": value,
                    "identity": target.identity,
                    "account": target.account,
                }
                if value
                else None
            )
        if self.name == "workday":
            status = page.locator(self.receipt_selector)
            reference = page.locator('[data-automation-id="applicationId"]')
            if (
                await status.count() != 1
                or await reference.count() != 1
                or not await self.identity_matches(page, target)
            ):
                return None
            if "submitted" not in (await status.inner_text()).lower():
                return None
            value = (await reference.inner_text()).strip()
            return (
                {
                    "reference": value,
                    "identity": target.identity,
                    "account": target.account,
                }
                if value
                else None
            )
        loc = page.locator(self.receipt_selector)
        if await loc.count() != 1:
            return None
        data = await loc.evaluate(
            "(el)=>({reference:el.dataset.reference,identity:el.dataset.applicationIdentity,account:el.dataset.account})"
        )
        if (
            data.get("reference")
            and data.get("identity") == target.identity
            and data.get("account") == target.account
        ):
            return data
        return None


ADAPTERS = (
    Adapter(
        "linkedin",
        ("linkedin.com",),
        root="div[role='dialog'] form,div[role='dialog']",
        review_selector=(
            "div[role='dialog']:has(button[aria-label*='Submit application' i]),"
            "div[role='dialog']:has(button:text-is('Submit application'))"
        ),
        attachment_selector=(
            "div[role='dialog'] [data-upload-saved],"
            "div[role='dialog'] .jobs-document-upload__file-name,"
            "div[role='dialog'] [class*='document-upload']"
        ),
    ),
    Adapter(
        "allhires",
        ("allhires.com",),
        attachment_selector="a[href*='attachment'], a[href*='document']",
        review_selector='[id$="ApplicationReview"],[id$="ApplicationPreview"]',
        submit_selector='input[id$="btnSubmitApplication"],button[id$="btnSubmitApplication"]',
        receipt_selector='[id$="ApplicationSubmitted"]',
        implemented_submission=True,
    ),
    Adapter(
        "apply4law",
        ("apply4law.com",),
        attachment_selector="a[href*='attachment'], a[href*='document']",
        review_selector='[id$="ApplicationReview"],[id$="ApplicationPreview"]',
        submit_selector='input[id$="btnSubmitApplication"],button[id$="btnSubmitApplication"]',
        receipt_selector='[id$="ApplicationSubmitted"]',
        implemented_submission=True,
    ),
    Adapter(
        "workday",
        ("myworkdayjobs.com",),
        root="[data-automation-id='applicationPage'],main",
        next_selector=(
            "button[data-automation-id='pageFooterNextButton'],"
            "button[data-automation-id='bottom-navigation-next-button']"
        ),
        review_selector="[data-automation-id='reviewPage']",
        attachment_selector="[data-automation-id='file-upload-file-name']",
        submit_selector="button[data-automation-id='bottom-navigation-next-button']:text-is('Submit')",
        receipt_selector="[data-automation-id='applicationSubmitted']",
        implemented_submission=True,
    ),
    Adapter(
        "greenhouse",
        ("greenhouse.io", "greenhouse.com"),
        root="#application_form,form",
        attachment_selector="#resume_filename,#cover_letter_filename",
    ),
    Adapter(
        "ashby", ("ashbyhq.com",), attachment_selector="[data-testid='uploaded-file']"
    ),
    Adapter(
        "lever", ("lever.co",), attachment_selector=".application-file-upload-success"
    ),
    Adapter("smartrecruiters", ("smartrecruiters.com",)),
    Adapter("taleo", ("taleo.net",)),
)
GENERIC = Adapter("generic")
# Explicitly enabled only by the isolated fixture runtime; never selected by
# HTML branding, page instructions, or a production RunPlan.
FIXTURE = Adapter(
    "fixture",
    ("127.0.0.1", "localhost"),
    next_selector='button[data-action="next"]',
    review_selector='[data-stage="review"]',
    submit_selector='button[data-action="submit"]',
    receipt_selector="[data-receipt]",
    attachment_selector="[data-upload-saved]",
    record_add_selector='button[data-action="add-record"]',
    record_save_selector='button[data-action="save-record"]',
    implemented_submission=True,
)


def choose(url, *, testing=False):
    host = urlsplit(url).hostname or ""
    if testing and host in FIXTURE.hosts:
        return FIXTURE
    return next(
        (
            a
            for a in ADAPTERS
            if any(host == h or host.endswith("." + h) for h in a.hosts)
        ),
        GENERIC,
    )
