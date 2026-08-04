#!/usr/bin/env python3
"""
5 Easy Apply (full) + open 3 external tabs, then STOP.
Keeps Chromium open until user says: finish all tasks
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import defaults_for_location, finish_all_tasks, get_context, get_page
from src.cleanup import append_needs_review_queue, load_applied_urls, save_applied_urls
from src.config import ROOT as CFG_ROOT
from src.config import ensure_dirs, load_config
from src.linkedin_apply import (
    _collect_job_hrefs,
    _dismiss_modals,
    _easy_apply_modal,
    _safe_text,
    apply_to_current_job,
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
    ("software engineer", "London, England, United Kingdom"),
    ("legal intern", "United Kingdom"),
    ("graduate", "United Kingdom"),
]

EXTERNAL_SEARCHES = [
    ("legal intern", "London, England, United Kingdom"),
    ("law graduate", "London, England, United Kingdom"),
    ("AI law", "London, England, United Kingdom"),
    ("software engineer", "London, England, United Kingdom"),
    ("legal intern", "United States"),
]


def ensure_linkedin(page) -> bool:
    page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    if "login" in page.url.lower() or "authwall" in page.url.lower():
        print("PING: log into LinkedIn in the open Chromium window. Waiting up to 5 min…", flush=True)
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
    """Return the main job apply control if visible (button or SDUI <a>)."""
    from src.linkedin_apply import APPLY_BUTTON_SELECTORS

    for sel in APPLY_BUTTON_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=1200):
                return loc
        except Exception:
            continue
    return None


def looks_like_easy_apply_button(btn) -> bool:
    try:
        aria = (btn.get_attribute("aria-label") or "").lower()
        txt = (btn.inner_text() or "").lower()
        blob = aria + " " + txt
        if "easy" in blob:
            return True
        # LinkedIn sometimes only says "Apply" on Easy Apply jobs
        if "apply" in blob and "company website" not in blob and "external" not in blob:
            return True
    except Exception:
        pass
    return False


def main() -> None:
    close_at_end = "--close" in sys.argv
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    base_defaults = dict(cfg.get("defaults", {}))
    history_path = CFG_ROOT / "data" / "applied_history.json"
    skip = load_applied_urls(history_path)

    easy_target = 5
    external_target = 2
    results: list[dict] = []
    empty_searches: list[str] = []

    print("=" * 60, flush=True)
    print(f"Mix: {easy_target} Easy Apply + open {external_target} external → STOP (browser stays open)", flush=True)
    print("Say 'finish sign up' after signups; 'finish all tasks' to close Chrome.", flush=True)
    print("=" * 60, flush=True)

    context = get_context(cfg["browser_data_dir"])
    page = get_page(cfg["browser_data_dir"])
    if not ensure_linkedin(page):
        return

    # External ATS tabs must stay open for signup — never auto-close these
    keep_open_pages: set[int] = set()

    def _close_ephemeral_tabs() -> None:
        """Close leftover tabs from Easy Apply only; keep external signup tabs."""
        for extra in list(context.pages)[1:]:
            try:
                if id(extra) in keep_open_pages:
                    continue
                # Also keep any non-LinkedIn tab (company ATS)
                u = (extra.url or "").lower()
                if u and "linkedin.com" not in u and u not in {"about:blank", "chrome://newtab/"}:
                    keep_open_pages.add(id(extra))
                    continue
                extra.close()
            except Exception:
                pass

    # ---- Easy Apply ----
    easy_done = 0
    paused_for_info = False
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
        hrefs = _collect_job_hrefs(page, limit=35, log=lambda m: print(m, flush=True))
        hrefs = [h for h in hrefs if normalize_job_url(h) not in skip]
        if not hrefs:
            msg = f"{keywords} @ {location}"
            empty_searches.append(msg)
            print(f"  No new jobs for this search.", flush=True)
            continue

        for href in hrefs:
            if easy_done >= easy_target:
                break
            href = normalize_job_url(href)
            print(f"\n[Easy {easy_done+1}/{easy_target}] {href}", flush=True)
            try:
                open_job_detail(page, href, search_url=search_url)
                page.wait_for_timeout(800)
                _dismiss_modals(page)

                # Search already uses f_AL=true (Easy Apply filter).
                # LinkedIn often labels the button just "Apply" — don't require "Easy" text.
                btn = find_apply_button(page)
                if not btn:
                    print("  no Apply button — skip", flush=True)
                    continue

                title = _safe_text(page, "h1")
                company = _safe_text(
                    page, ".job-details-jobs-unified-top-card__company-name"
                )
                job_context = _safe_text(page, "#job-details")

                btn.click(timeout=4000)
                page.wait_for_timeout(1200)

                # Easy Apply opens an in-page modal; external opens a new tab/URL
                modal = page.locator(
                    ".jobs-easy-apply-modal, .jobs-easy-apply-content, "
                    "div.jobs-easy-apply-content, div[aria-labelledby*='jobs-apply']"
                )
                if modal.count() > 0:
                    print("  Easy Apply modal open — filling + Next + Submit…", flush=True)
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
                elif looks_like_easy_apply_button(btn) or _easy_apply_modal(page).count():
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
                else:
                    print("  opened non-modal apply (likely external) — skip in Easy pass", flush=True)
                    # Dismiss if a dialog appeared
                    _dismiss_modals(page)
                    continue

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
                        "\n*** PING: NEED YOUR INPUT ***\n"
                        f"Job: {result.title} @ {result.company}\n"
                        f"Detail: {result.detail}\n"
                        "Easy Apply modal is LEFT OPEN in Chromium.\n"
                        "Tell me the missing answer(s) in chat (or fill them yourself),\n"
                        "then say: continue apply\n"
                        "***",
                        flush=True,
                    )
                    paused_for_info = True
                    break
                else:
                    # needs_review / error — record but don't silently lose the chance forever
                    skip.add(href)
                # Close Easy Apply leftovers only — never touch company ATS tabs
                if not paused_for_info:
                    _close_ephemeral_tabs()
                    page = next(
                        (p for p in context.pages if "linkedin.com" in (p.url or "")),
                        context.pages[0] if context.pages else get_page(cfg["browser_data_dir"]),
                    )
                    time.sleep(8)
            except Exception as exc:
                print(f"  error: {exc}", flush=True)
                results.append({"url": href, "status": "error", "detail": str(exc), "kind": "easy"})
        if paused_for_info:
            break

    print(f"\nEasy Apply submitted: {easy_done}/{easy_target}", flush=True)
    if easy_done == 0:
        print("NOTE: No Easy Apply submissions succeeded in searched lists.", flush=True)

    # ---- Open 3 externals (tabs STAY open for signup) ----
    external_done = 0
    if paused_for_info:
        print(
            "\nSkipping external open for now — finish the open Easy Apply first "
            "(say: continue apply).",
            flush=True,
        )
    for keywords, location in EXTERNAL_SEARCHES:
        if paused_for_info or external_done >= external_target:
            break
        search_url = build_search_url(
            keywords, location, easy_apply_only=False, entry_level=True, past_week=True
        )
        print(f"\n=== External open: {keywords!r} @ {location} ===", flush=True)
        # Prefer LinkedIn page for search (do not reuse an ATS tab)
        page = next(
            (p for p in context.pages if "linkedin.com" in (p.url or "")),
            context.pages[0] if context.pages else get_page(cfg["browser_data_dir"]),
        )
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        hrefs = _collect_job_hrefs(page, limit=35, log=lambda m: print(m, flush=True))
        hrefs = [h for h in hrefs if normalize_job_url(h) not in skip]
        if not hrefs:
            empty_searches.append(f"external:{keywords} @ {location}")
            print("  No new jobs for this search.", flush=True)
            continue

        for href in hrefs:
            if external_done >= external_target:
                break
            href = normalize_job_url(href)
            print(f"\n[External {external_done+1}/{external_target}] {href}", flush=True)
            try:
                open_job_detail(page, href, search_url=search_url)
                page.wait_for_timeout(800)
                _dismiss_modals(page)
                apply_btn = find_apply_button(page)
                if not apply_btn:
                    print("  no Apply button — will try next job", flush=True)
                    continue
                # Prefer jobs that are NOT in-modal Easy Apply
                aria = ((apply_btn.get_attribute("aria-label") or "") + " " + (apply_btn.inner_text() or "")).lower()
                if "easy apply" in aria:
                    print("  Easy Apply — try next for external pass", flush=True)
                    continue

                title = _safe_text(page, "h1")
                company = _safe_text(page, ".job-details-jobs-unified-top-card__company-name")
                before = list(context.pages)
                external_page = page
                try:
                    with context.expect_page(timeout=10000) as np:
                        apply_btn.click(timeout=4000)
                    external_page = np.value
                    external_page.wait_for_load_state("domcontentloaded", timeout=60000)
                except Exception:
                    page.wait_for_timeout(2500)
                    if _easy_apply_modal(page).count():
                        print("  turned out Easy Apply modal — try next", flush=True)
                        _dismiss_modals(page)
                        continue
                    if len(context.pages) > len(before):
                        external_page = context.pages[-1]

                keep_open_pages.add(id(external_page))
                print(f"  OPENED (tab kept open): {external_page.url}", flush=True)
                print(f"  Job: {title} @ {company}", flush=True)
                # Keep ATS tab visible for signup — do NOT bring LinkedIn over it
                try:
                    external_page.bring_to_front()
                except Exception:
                    pass
                results.append(
                    {
                        "url": href,
                        "external": external_page.url,
                        "status": "opened_waiting_signup",
                        "title": title,
                        "company": company,
                        "detail": "Opened — tab kept open for signup",
                        "kind": "external",
                        "location": location,
                    }
                )
                skip.add(href)
                external_done += 1
                time.sleep(2)
            except Exception as exc:
                print(f"  error: {exc}", flush=True)

    save_applied_urls(history_path, skip)
    append_needs_review_queue(cfg["output_dir"], results)
    opened = [r for r in results if r.get("kind") == "external"]
    (Path(cfg["output_dir"]) / "external_opened_waiting.json").write_text(
        json.dumps(opened, indent=2) + "\n", encoding="utf-8"
    )

    print("\n=== STOPPED — WAITING FOR YOU ===", flush=True)
    print(f"Easy Apply submitted: {easy_done}/{easy_target}", flush=True)
    print(f"External tabs opened: {external_done}/{external_target}", flush=True)
    for r in opened:
        print(
            f"  - {r.get('title') or '?'} @ {r.get('company') or '?'} → {r.get('external')}",
            flush=True,
        )
    if empty_searches:
        print("\nSearches with no new jobs:", flush=True)
        for s in empty_searches:
            print(f"  • {s}", flush=True)
    if easy_done < easy_target or external_done < external_target:
        print(
            f"\nCould not fully reach targets (Easy {easy_done}/{easy_target}, "
            f"External {external_done}/{external_target}). Not searching forever.",
            flush=True,
        )
    print("\nNext:", flush=True)
    print("  • Finish signups in open tabs, then say: finish sign up", flush=True)
    print("  • When completely done for the session, say: finish all tasks", flush=True)
    print("Chromium STAYS OPEN (not closed by this script).", flush=True)

    if close_at_end:
        finish_all_tasks()


if __name__ == "__main__":
    main()
