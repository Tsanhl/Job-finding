#!/usr/bin/env python3
"""Batch apply: Easy Apply + external ATS links for London/UK graduate roles."""

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

from playwright.sync_api import sync_playwright

from src.cleanup import cleanup_run_logs, load_applied_urls
from src.config import ROOT as CFG_ROOT
from src.config import ensure_dirs, load_config
from src.linkedin_apply import build_search_url, run_linkedin_auto_apply
from src.profile import load_profile
from src.urlutil import normalize_job_url


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    defaults = dict(cfg.get("defaults", {}))
    li = cfg.get("linkedin", {})
    searches = li.get("searches") or []
    history_path = CFG_ROOT / "data" / "applied_history.json"
    skip = load_applied_urls(history_path)
    cleanup_run_logs(cfg["output_dir"], keep=int(li.get("keep_run_logs", 3)))

    dry_run = "--dry-run" in sys.argv
    print("=" * 60, flush=True)
    print("ApplyPilot — London/UK (Easy Apply + external Apply links)", flush=True)
    print("NOTE: Cursor's panel browser cannot be automated.", flush=True)
    print("      Use the Chromium window this script opens (login once).", flush=True)
    print("ASSISTED-REVIEW" if not dry_run else "LOCAL-PREVIEW", "mode", flush=True)
    print("=" * 60, flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=cfg["browser_data_dir"],
            headless=False,
            viewport={"width": 1400, "height": 900},
            slow_mo=40,
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Ensure logged in
        page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        if "login" in page.url.lower() or "authwall" in page.url.lower():
            print("\n>>> Not logged in. Please log into LinkedIn in the Chromium window now.", flush=True)
            print(">>> Waiting up to 5 minutes…", flush=True)
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            for _ in range(150):
                if "login" not in page.url.lower() and "checkpoint" not in page.url.lower():
                    if page.locator(".global-nav, input[placeholder*='Search']").count() > 0:
                        break
                page.wait_for_timeout(2000)
            else:
                print("Login not detected — aborting. Run: python scripts/linkedin_login.py", flush=True)
                context.close()
                raise SystemExit(1)
            print("Login OK — starting applications.", flush=True)

        all_results = []
        for i, search in enumerate(searches, 1):
            keywords = search["keywords"]
            location = search.get("location", "London, England, United Kingdom")
            max_apps = int(search.get("max", 15))
            print(f"\n=== Search {i}/{len(searches)}: {keywords}", flush=True)
            print(f"    Location: {location} | max {max_apps}", flush=True)
            print(
                f"    URL: {build_search_url(keywords, location, easy_apply_only=False, entry_level=True, past_week=True)}",
                flush=True,
            )

            summary = run_linkedin_auto_apply(
                keywords=keywords,
                location=location,
                cv_path=cfg["cv_path"],
                browser_data_dir=cfg["browser_data_dir"],
                max_applications=max_apps,
                delay_seconds=float(li.get("delay_seconds_between_apps", 12)),
                easy_apply_only=False,
                headless=False,
                dry_run=dry_run,
                use_ai=False,
                defaults=defaults,
                profile=profile,
                output_dir=cfg["output_dir"],
                skip_urls=skip,
                existing_page=page,
                log=lambda m: print(m, flush=True),
            )
            for r in summary.results:
                all_results.append(r)
                if r.url and r.status in {
                    "review-ready",
                    "needs-authentication",
                    "skipped-unsuitable",
                }:
                    skip.add(normalize_job_url(r.url))

        applied_n = sum(1 for r in all_results if r.status == "submitted-confirmed")
        review_n = sum(1 for r in all_results if r.status == "review-ready")
        signup_n = sum(1 for r in all_results if r.status == "needs-authentication")
        skipped_n = sum(1 for r in all_results if r.status == "skipped-unsuitable")
        print("\n=== BATCH DONE ===", flush=True)
        print(
            f"Applied: {applied_n} | Needs review: {review_n} | "
            f"Signup ping: {signup_n} | Skipped: {skipped_n} | Total: {len(all_results)}",
            flush=True,
        )
        print("Chromium stays open. Say 'finish all tasks' when you want it closed.", flush=True)
        # Do not close the browser — user asked to keep it open across tasks.
        # Detach by leaving process wait lightly so tabs remain (parent may exit).
        try:
            page.wait_for_timeout(3_600_000)  # keep alive up to 1h if run alone
        except Exception:
            pass


if __name__ == "__main__":
    main()
