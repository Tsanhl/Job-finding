"""Search for suitable jobs and fill several applications concurrently."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .account_automation import GmailBrowserVerifier, PortalCredentials
from .application_ledger import ApplicationLedger
from .application_flow import missing_application_details
from .application_workers import MAX_APPLICATIONS_PER_BATCH, resolve_worker_count
from .browser_session import CDP_URL, get_context
from .cleanup import append_needs_review_queue, load_applied_urls, save_applied_urls
from .config import ROOT
from .linkedin_apply import (
    ApplyResult,
    _collect_job_hrefs,
    apply_to_current_job,
    build_search_url,
)
from .urlutil import normalize_job_url

LogFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class FullAutomationRequest:
    keywords: tuple[str, ...]
    locations: tuple[str, ...]
    max_applications: int = 10
    submission_policy: str = "review"
    easy_apply_only: bool = False
    accept_required_terms: bool = False
    excluded_keywords: tuple[str, ...] = ()
    excluded_employers: tuple[str, ...] = ()
    paid_roles_only: bool = True
    max_required_experience_years: int | None = 1

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "FullAutomationRequest":
        if not isinstance(value, dict):
            raise ValueError("Full-automation requirements must be a JSON object.")

        def string_tuple(key: str, fallback: str = "") -> tuple[str, ...]:
            raw = value.get(key, fallback)
            if isinstance(raw, str):
                raw = raw.split(",")
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list or comma-separated string.")
            return tuple(str(item).strip() for item in raw if str(item).strip())

        keywords = string_tuple("keywords") or string_tuple("roles")
        locations = string_tuple("locations")
        if not keywords:
            raise ValueError("Provide at least one target role or keyword.")
        if not locations:
            raise ValueError("Provide at least one target location.")

        maximum = int(value.get("max_applications", 10))
        if not 1 <= maximum <= MAX_APPLICATIONS_PER_BATCH:
            raise ValueError(
                f"max_applications must be between 1 and {MAX_APPLICATIONS_PER_BATCH}."
            )
        policy = str(value.get("submission_policy", "review")).strip().lower()
        if policy not in {"review", "auto-submit"}:
            raise ValueError("submission_policy must be 'review' or 'auto-submit'.")
        raw_experience_limit = value.get("max_required_experience_years", 1)
        if raw_experience_limit in {None, ""}:
            experience_limit = None
        else:
            experience_limit = int(raw_experience_limit)
            if not 0 <= experience_limit <= 50:
                raise ValueError(
                    "max_required_experience_years must be between 0 and 50, or null."
                )
        return cls(
            keywords=keywords,
            locations=locations,
            max_applications=maximum,
            submission_policy=policy,
            easy_apply_only=value.get("easy_apply_only") is True,
            accept_required_terms=value.get("accept_required_terms") is True,
            excluded_keywords=string_tuple("excluded_keywords"),
            excluded_employers=string_tuple("excluded_employers"),
            paid_roles_only=value.get("paid_roles_only", True) is not False,
            max_required_experience_years=experience_limit,
        )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        data["locations"] = list(self.locations)
        data["excluded_keywords"] = list(self.excluded_keywords)
        data["excluded_employers"] = list(self.excluded_employers)
        return data


def load_full_automation_request(path: str | Path) -> FullAutomationRequest:
    request_path = Path(path).expanduser()
    try:
        value = json.loads(request_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Requirements file was not found: {request_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Requirements file is not valid JSON: {request_path}") from exc
    return FullAutomationRequest.from_mapping(value)


def discover_linkedin_job_urls(
    request: FullAutomationRequest,
    *,
    browser_data_dir: str,
    skip_urls: set[str],
    candidate_limit: int,
    log: LogFn | None = None,
) -> list[str]:
    """Collect unique job URLs that match the user's role and location searches."""
    context = get_context(browser_data_dir)
    page = context.new_page()
    found: list[str] = []
    normalized_skips = {normalize_job_url(url) for url in skip_urls}
    try:
        for location in request.locations:
            for keywords in request.keywords:
                search_url = build_search_url(
                    keywords,
                    location,
                    easy_apply_only=request.easy_apply_only,
                    entry_level=True,
                    past_week=True,
                )
                if log:
                    log(f"Searching {keywords} in {location}.")
                page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
                for _ in range(45):
                    if page.locator(
                        "a.job-card-container__link, .jobs-search-results-list, "
                        "a[href*='/jobs/view/']"
                    ).count():
                        break
                    page.wait_for_timeout(2000)
                for url in _collect_job_hrefs(
                    page,
                    limit=max(candidate_limit * 2, 20),
                    log=log,
                ):
                    normalized = normalize_job_url(url)
                    if normalized in normalized_skips or normalized in found:
                        continue
                    found.append(normalized)
                    if len(found) >= candidate_limit:
                        return found
    finally:
        try:
            page.close()
        except Exception:
            pass
    return found


def _visible_job_title(page: Any) -> str:
    for selector in (
        "h1",
        ".job-details-jobs-unified-top-card__job-title",
        "div.job-details-jobs-unified-top-card__job-title",
    ):
        try:
            value = (page.locator(selector).first.inner_text(timeout=1000) or "").strip()
            if value:
                return value
        except Exception:
            continue
    return ""


def suitability_blocker(
    request: FullAutomationRequest,
    *,
    title: str,
    description: str,
) -> str:
    """Return a concrete reason to skip an unsuitable search result."""
    combined = f"{title}\n{description}".lower()
    for employer in request.excluded_employers:
        if employer.lower() in combined:
            return f"Employer excluded by user: {employer}"
    if request.paid_roles_only and any(
        marker in combined
        for marker in (
            "unpaid placement",
            "unpaid programme",
            "unpaid program",
            "this unpaid",
            "voluntary unpaid",
            "you will not be working for the company",
        )
    ):
        return "Explicitly unpaid or training-only rather than paid employment"

    limit = request.max_required_experience_years
    if limit is not None:
        required_years: list[int] = []
        for pattern in (
            r"(?:at least|minimum(?: of)?|requires?)\s+(\d{1,2})\+?\s+years?",
            r"(\d{1,2})\+\s+years?\s+(?:of\s+)?(?:relevant\s+)?experience",
        ):
            required_years.extend(int(value) for value in re.findall(pattern, combined))
        if required_years and min(required_years) > limit:
            return (
                f"Requires at least {min(required_years)} years of experience; "
                f"configured maximum is {limit}"
            )
    return ""


def _apply_one_job(
    url: str,
    *,
    request: FullAutomationRequest,
    credentials: Any,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    use_ai: bool,
    verifier: GmailBrowserVerifier,
) -> ApplyResult:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            return ApplyResult("", "", url, "error", "Shared Chromium has no browser context")
        context = next(
            (candidate for candidate in browser.contexts if candidate.pages),
            browser.contexts[0],
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(1500)
            title = _visible_job_title(page)
            lowered_title = title.lower()
            if any(term.lower() in lowered_title for term in request.excluded_keywords):
                page.close()
                return ApplyResult(title, "", url, "skipped", "Excluded by user requirements")
            try:
                description = page.locator("body").inner_text(timeout=3000) or ""
            except Exception:
                description = ""
            blocker = suitability_blocker(
                request,
                title=title,
                description=description[:60000],
            )
            if blocker:
                page.close()
                return ApplyResult(title, "", url, "skipped", blocker)
            return apply_to_current_job(
                page,
                profile=profile,
                defaults=defaults,
                cv_path=cv_path,
                use_ai=use_ai,
                dry_run=False,
                allow_submit=request.submission_policy == "auto-submit",
                account_credentials=credentials,
                verification_client=verifier,
                accept_required_terms=request.accept_required_terms,
                log=None,
            )
        except Exception as exc:
            safe_detail = str(exc)
            if hasattr(credentials, "redact"):
                safe_detail = credentials.redact(safe_detail)
            elif isinstance(credentials, PortalCredentials):
                safe_detail = safe_detail.replace(credentials.password, "[redacted]")
            return ApplyResult("", "", url, "error", f"{type(exc).__name__}: {safe_detail}")


def _ledger_status(result: ApplyResult) -> str:
    return {
        "applied": "submitted",
        "needs_review": "review_ready",
        "needs_info": "blocked",
        "needs_signup": "blocked",
        "skipped": "rejected",
        "error": "failed",
        "dry_run": "review_ready",
    }.get(result.status, result.status)


def run_full_automation(
    request: FullAutomationRequest,
    *,
    credentials: Any,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    browser_data_dir: str,
    output_dir: str,
    worker_count: int | None = None,
    use_ai: bool = True,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Search, create accounts when required, and fill applications in parallel."""
    missing = missing_application_details(
        profile,
        cv_path,
        keywords=request.keywords[0],
        location=request.locations[0],
        mode="linkedin",
    )
    if missing:
        return {
            "status": "needs_information",
            "submission_policy": request.submission_policy,
            "worker_count": 0,
            "results": [],
            "detail": "Before full automation, provide: " + "; ".join(missing),
        }

    history_path = ROOT / "data" / "applied_history.json"
    applied_history = load_applied_urls(history_path)
    ledger = ApplicationLedger(Path(output_dir) / "application_ledger.json")
    skip_urls = applied_history | ledger.skip_urls()
    discovery_limit = min(max(request.max_applications * 5, 20), 60)
    candidates = discover_linkedin_job_urls(
        request,
        browser_data_dir=browser_data_dir,
        skip_urls=skip_urls,
        candidate_limit=discovery_limit,
        log=log,
    )
    if not candidates:
        return {
            "status": "needs_user_attention",
            "submission_policy": request.submission_policy,
            "worker_count": 0,
            "results": [],
            "detail": "No new matching job links were found. Check the signed-in LinkedIn browser.",
        }

    workers = resolve_worker_count(
        min(len(candidates), request.max_applications),
        worker_count,
    )
    verifier = GmailBrowserVerifier()
    results: list[ApplyResult] = []
    completed_count = 0
    candidate_index = 0
    ready_statuses = {"applied", "needs_review"}

    while candidate_index < len(candidates) and completed_count < request.max_applications:
        remaining = request.max_applications - completed_count
        wave_size = min(workers, remaining, len(candidates) - candidate_index)
        wave = candidates[candidate_index : candidate_index + wave_size]
        candidate_index += wave_size
        for url in wave:
            ledger.record(
                url,
                status="in_progress",
                submission_policy=request.submission_policy,
            )

        indexed: dict[str, ApplyResult] = {}
        with ThreadPoolExecutor(
            max_workers=min(workers, len(wave)),
            thread_name_prefix="full-application",
        ) as pool:
            futures = {
                pool.submit(
                    _apply_one_job,
                    url,
                    request=request,
                    credentials=credentials,
                    profile=profile,
                    defaults=defaults,
                    cv_path=cv_path,
                    use_ai=use_ai,
                    verifier=verifier,
                ): url
                for url in wave
            }
            for future in as_completed(futures):
                result = future.result()
                indexed[futures[future]] = result
                if log:
                    log(f"[{result.status}] {result.title or futures[future]}")

        for url in wave:
            result = indexed[url]
            results.append(result)
            ledger.record(
                result.url or url,
                status=_ledger_status(result),
                title=result.title,
                company=result.company,
                detail=result.detail,
                submission_policy=request.submission_policy,
            )
            if result.status in ready_statuses:
                completed_count += 1

    applied_history.update(
        normalize_job_url(result.url) for result in results if result.status == "applied"
    )
    save_applied_urls(history_path, applied_history)
    public_results = [asdict(result) for result in results]
    append_needs_review_queue(output_dir, public_results)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    target_reached = completed_count >= request.max_applications
    submission_attention = (
        request.submission_policy == "auto-submit"
        and any(result.status == "needs_review" for result in results)
    )
    overall_status = (
        "complete"
        if target_reached and not submission_attention
        else "needs_user_attention"
    )
    return {
        "status": overall_status,
        "submission_policy": request.submission_policy,
        "worker_count": workers,
        "requested": request.max_applications,
        "completed": completed_count,
        "discovered": len(candidates),
        "searched": len(results),
        "counts": counts,
        "results": public_results,
    }
