#!/usr/bin/env python3
"""
Trail ONE Easy Apply safely: click → fill → verified progress → manual review.
Verbose logs. Leaves Chromium open. Stops + PINGs if a field is unknown.
"""

from __future__ import annotations

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.pilot.legacy import main as runtime_entry
    runtime_entry()
    raise SystemExit(0)


import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import defaults_for_location, get_context, get_page
from src.cleanup import load_applied_urls, save_applied_urls
from src.config import ROOT as CFG_ROOT
from src.config import ensure_dirs, load_config
from src.linkedin_apply import (
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


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    defaults = defaults_for_location(dict(cfg["defaults"]), "London")
    skip = load_applied_urls(CFG_ROOT / "data" / "applied_history.json")

    print("=" * 60, flush=True)
    print("TRAIL: 1 Easy Apply through to Submit (or PING if stuck)", flush=True)
    print("Profile facts are read from the ignored local profile and never guessed.", flush=True)
    print("=" * 60, flush=True)

    context = get_context(cfg["browser_data_dir"])
    page = get_page(cfg["browser_data_dir"])
    page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1200)
    if "login" in page.url.lower():
        print("PING: Log into LinkedIn in Chromium…", flush=True)
        for _ in range(120):
            if "login" not in page.url.lower():
                break
            page.wait_for_timeout(2000)

    searches = [
        ("legal intern", "London, England, United Kingdom"),
        ("intern", "London, England, United Kingdom"),
        ("graduate", "London, England, United Kingdom"),
    ]
    done = False
    for keywords, location in searches:
        if done:
            break
        search_url = build_search_url(
            keywords, location, easy_apply_only=True, entry_level=True, past_week=True
        )
        print(f"\n=== Search: {keywords!r} ===", flush=True)
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        _dismiss_modals(page)
        hrefs = _collect_job_hrefs(page, limit=12, log=lambda m: print(m, flush=True))
        hrefs = [normalize_job_url(h) for h in hrefs if normalize_job_url(h) not in skip]
        for href in hrefs:
            print(f"\n→ Opening {href}", flush=True)
            open_job_detail(page, href, search_url=search_url)
            page.wait_for_timeout(1000)
            _dismiss_modals(page)

            btn = page.locator("button.jobs-apply-button, a[aria-label*='Easy Apply' i]").first
            try:
                if not (btn.count() and btn.is_visible(timeout=2000)):
                    print("  no Easy Apply control — next", flush=True)
                    continue
            except Exception:
                continue

            aria = ((btn.get_attribute("aria-label") or "") + " " + (btn.inner_text() or ""))[:100]
            print(f"  control: {aria!r}", flush=True)
            try:
                btn.click(timeout=5000)
            except Exception as e:
                print(f"  entry control click failed safely: {e}", flush=True)
                continue
            page.wait_for_timeout(1500)
            if _easy_apply_modal(page).count() == 0:
                print("  modal did not open — next", flush=True)
                continue

            title = _safe_text(page, "h1")
            company = _safe_text(page, ".job-details-jobs-unified-top-card__company-name")
            print(f"  modal open — {title} @ {company}", flush=True)
            print("  Running safe wizard: fill → verified progress → manual review", flush=True)

            result = complete_easy_apply(
                page,
                profile=profile,
                defaults=defaults,
                cv_path=cfg["cv_path"],
                job_context=_safe_text(page, "#job-details"),
                title=title,
                company=company,
                url=href,
                use_ai=False,
                dry_run=False,
                log=lambda m: print(f"  {m}", flush=True),
            )
            print(f"\n=== RESULT: [{result.status}] {result.detail} ===", flush=True)
            if result.status == "review-ready":
                skip.add(href)
                save_applied_urls(CFG_ROOT / "data" / "applied_history.json", skip)
                print("READY — review and submit the application manually.", flush=True)
            elif result.status == "needs-information":
                print(
                    "\n*** PING: fill the open modal or tell me the answer, then say continue apply ***\n",
                    flush=True,
                )
            done = True
            break

    if not done:
        print("No trailable Easy Apply job found.", flush=True)
    print("Chromium stays open.", flush=True)
    try:
        page.wait_for_timeout(600_000)
    except Exception:
        pass


if __name__ == "__main__":
    main()
