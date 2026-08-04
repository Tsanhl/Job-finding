#!/usr/bin/env python3
"""Submit 5 LinkedIn Easy Apply jobs. Attaches to open Chromium (CDP). Does not close browser."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import defaults_for_location, ensure_page, get_context
from src.cleanup import append_needs_review_queue, load_applied_urls, save_applied_urls
from src.config import ROOT as CFG_ROOT
from src.config import ensure_dirs, load_config
from src.linkedin_apply import (
    APPLY_BUTTON_SELECTORS,
    _collect_job_hrefs,
    _dismiss_modals,
    _easy_apply_modal,
    _safe_text,
    build_search_url,
    complete_easy_apply,
    open_job_detail,
)
from src.profile import load_profile
from src.urlutil import normalize_job_url

EASY_SEARCHES = [
    ("legal intern", "London, England, United Kingdom"),
    ("law intern", "London, England, United Kingdom"),
    ("intern", "London, England, United Kingdom"),
    ("graduate", "London, England, United Kingdom"),
    ("AI law", "London, England, United Kingdom"),
    ("legal AI", "London, England, United Kingdom"),
    ("paralegal", "London, England, United Kingdom"),
    ("software engineer", "London, England, United Kingdom"),
    ("legal intern", "United Kingdom"),
    ("graduate", "United Kingdom"),
]


def ensure_linkedin(page) -> bool:
    page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    if "login" in page.url.lower() or "authwall" in page.url.lower():
        print("PING: log into LinkedIn in Chromium. Waiting up to 5 min…", flush=True)
        page.goto("https://www.linkedin.com/login")
        for _ in range(150):
            if "login" not in page.url.lower() and page.locator(".global-nav").count():
                print("Login OK.", flush=True)
                return True
            page.wait_for_timeout(2000)
        print("Login not detected — stop.", flush=True)
        return False
    return True


def find_apply_button(page):
    for sel in APPLY_BUTTON_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=1200):
                return loc
        except Exception:
            continue
    return None


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    base_defaults = dict(cfg.get("defaults", {}))
    history_path = CFG_ROOT / "data" / "applied_history.json"
    skip = load_applied_urls(history_path)

    easy_target = 5
    results: list[dict] = []

    print("=" * 60, flush=True)
    print("Easy Apply × 5 — Chromium stays open", flush=True)
    print("=" * 60, flush=True)

    try:
        context = get_context(cfg["browser_data_dir"])
    except RuntimeError as e:
        print(f"FAIL: {e}", flush=True)
        print("Start: python scripts/open_browser.py", flush=True)
        return

    page = ensure_page(context)
    if not ensure_linkedin(page):
        return

    easy_done = 0
    for keywords, location in EASY_SEARCHES:
        if easy_done >= easy_target:
            break
        defaults = defaults_for_location(base_defaults, location)
        prof = dict(profile)

        search_url = build_search_url(
            keywords, location, easy_apply_only=True, entry_level=True, past_week=True
        )
        print(f"\n=== Easy Apply: {keywords!r} @ {location} ===", flush=True)
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        _dismiss_modals(page)
        hrefs = _collect_job_hrefs(page, limit=35, log=lambda m: print(m, flush=True))
        hrefs = [h for h in hrefs if normalize_job_url(h) not in skip]
        if not hrefs:
            print("  No new jobs.", flush=True)
            continue

        for href in hrefs:
            if easy_done >= easy_target:
                break
            href = normalize_job_url(href)
            print(f"\n[Easy {easy_done + 1}/{easy_target}] {href}", flush=True)
            try:
                open_job_detail(page, href, search_url=search_url)
                page.wait_for_timeout(800)
                _dismiss_modals(page)
                btn = find_apply_button(page)
                if not btn:
                    print("  no Apply button — next", flush=True)
                    continue
                title = _safe_text(page, "h1")
                company = _safe_text(page, ".job-details-jobs-unified-top-card__company-name")
                job_context = _safe_text(page, "#job-details")
                btn.click(timeout=4000)
                page.wait_for_timeout(1200)
                if _easy_apply_modal(page).count() == 0:
                    print("  no Easy Apply modal — next", flush=True)
                    _dismiss_modals(page)
                    continue
                result = complete_easy_apply(
                    page,
                    profile=prof,
                    defaults=defaults,
                    cv_path=cfg["cv_path"],
                    job_context=job_context,
                    title=title,
                    company=company,
                    url=href,
                    use_ai=False,
                    dry_run=False,
                    log=lambda m: print(f"  {m}", flush=True),
                )
                print(
                    f"  → [{result.status}] {result.title} @ {result.company} — {result.detail}",
                    flush=True,
                )
                results.append(
                    {
                        "url": href,
                        "status": result.status,
                        "title": result.title,
                        "company": result.company,
                        "detail": result.detail,
                        "kind": "easy",
                        "location": location,
                    }
                )
                if result.status == "applied":
                    skip.add(href)
                    easy_done += 1
                elif result.status == "needs_info":
                    print(
                        "\n*** PING: NEED YOUR INPUT — modal left open ***\n"
                        f"{result.detail}\n"
                        "Tell me the answer in chat, or fill in Chromium, then say: continue apply\n",
                        flush=True,
                    )
                    save_applied_urls(history_path, skip)
                    append_needs_review_queue(cfg["output_dir"], results)
                    return
                else:
                    skip.add(href)
                # Close only ephemeral LinkedIn junk tabs — never kill browser
                for extra in list(context.pages)[1:]:
                    try:
                        u = (extra.url or "").lower()
                        if "linkedin.com" in u and "/jobs" not in u:
                            extra.close()
                    except Exception:
                        pass
                page = next(
                    (p for p in context.pages if "linkedin.com" in (p.url or "")),
                    ensure_page(context),
                )
                time.sleep(8)
            except Exception as exc:
                print(f"  error: {exc}", flush=True)
                results.append({"url": href, "status": "error", "detail": str(exc), "kind": "easy"})

    save_applied_urls(history_path, skip)
    append_needs_review_queue(cfg["output_dir"], results)
    print(f"\n=== DONE Easy Apply: {easy_done}/{easy_target} ===", flush=True)
    for r in results:
        if r.get("status") == "applied":
            print(f"  ✓ {r.get('title')} @ {r.get('company')}", flush=True)
    print("Chromium STAYS OPEN. Say 'finish all tasks' to close.", flush=True)


if __name__ == "__main__":
    main()
