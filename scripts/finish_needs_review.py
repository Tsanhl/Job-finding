#!/usr/bin/env python3
"""Re-open attention items and advance them only to a safe manual-review state."""

from __future__ import annotations

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.pilot.legacy import main as runtime_entry
    runtime_entry()
    raise SystemExit(0)


import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from src.cleanup import cleanup_run_logs, load_applied_urls, save_applied_urls
from src.config import ROOT as CFG_ROOT
from src.config import ensure_dirs, load_config
from src.linkedin_apply import apply_to_current_job, _dismiss_modals
from src.profile import load_profile
from src.urlutil import normalize_job_url


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    defaults = dict(cfg.get("defaults", {}))
    queue_path = Path(cfg["output_dir"]) / "needs_review_queue.json"
    if not queue_path.exists():
        raise SystemExit(f"No queue file: {queue_path}")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    history_path = CFG_ROOT / "data" / "applied_history.json"
    skip = load_applied_urls(history_path)

    # CLI: --skip <url-substring>
    skip_args = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--skip" and i + 1 < len(args):
            skip_args.append(args[i + 1].lower())
            i += 2
        else:
            i += 1

    results = []
    print(f"Preparing {len(queue)} jobs for manual review (no automated submission)…", flush=True)
    print("External signup walls → PING (wait 90s then skip unless you sign in).", flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=cfg["browser_data_dir"],
            headless=False,
            viewport={"width": 1400, "height": 900},
            slow_mo=50,
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(1500)
        if "login" in page.url.lower():
            print("PING: LinkedIn login required. Waiting up to 5 min…", flush=True)
            page.goto("https://www.linkedin.com/login")
            for _ in range(150):
                if "login" not in page.url.lower() and page.locator(".global-nav").count():
                    break
                page.wait_for_timeout(2000)

        for idx, item in enumerate(queue, 1):
            url = normalize_job_url(item.get("url") or "")
            if not url:
                continue
            if any(s in url.lower() for s in skip_args):
                print(f"[{idx}] CLI skip: {url}", flush=True)
                continue
            if url in skip:
                print(f"[{idx}] already in history — skip: {url}", flush=True)
                continue

            print(f"\n[{idx}/{len(queue)}] {url}", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                _dismiss_modals(page)

                result = apply_to_current_job(
                    page,
                    profile=profile,
                    defaults=defaults,
                    cv_path=cfg["cv_path"],
                    use_ai=False,
                    dry_run=False,
                    log=lambda m: print(f"  {m}", flush=True),
                )

                # Signup: pause briefly for user
                if result.status == "needs-authentication":
                    print(f"  *** PING: SIGNUP/LOGIN REQUIRED ***", flush=True)
                    print(f"  {result.detail}", flush=True)
                    print("  Waiting 90s for you to sign in (or say skip)…", flush=True)
                    page.wait_for_timeout(90_000)
                    # Retry once after wait
                    result = apply_to_current_job(
                        page,
                        profile=profile,
                        defaults=defaults,
                        cv_path=cfg["cv_path"],
                        use_ai=False,
                        dry_run=False,
                        log=lambda m: print(f"  retry: {m}", flush=True),
                    )
                    if result.status == "needs-authentication":
                        print("  Still needs signup — skipping this job", flush=True)
                        skip.add(url)

                print(f"  → [{result.status}] {result.title} @ {result.company} — {result.detail}", flush=True)
                results.append(
                    {
                        "url": url,
                        "status": result.status,
                        "title": result.title,
                        "company": result.company,
                        "detail": result.detail,
                    }
                )
                if result.status in {"review-ready", "needs-authentication"}:
                    skip.add(url)

                # Close extra tabs
                for pge in list(context.pages)[1:]:
                    try:
                        pge.close()
                    except Exception:
                        pass
                page = context.pages[0]
                time.sleep(float(cfg.get("linkedin", {}).get("delay_seconds_between_apps", 10)))
            except Exception as exc:
                print(f"  error: {exc}", flush=True)
                results.append({"url": url, "status": "error", "detail": str(exc)})

        save_applied_urls(history_path, skip)
        out = Path(cfg["output_dir"]) / f"needs_review_finish_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        remaining = [
            r
            for r in results
            if r.get("status")
            in {
                "needs-information",
                "needs-authentication",
                "policy-blocked",
                "unsupported",
                "failed-retryable",
                "submission-unconfirmed",
            }
        ]
        queue_path.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        cleanup_run_logs(cfg["output_dir"], keep=5)

        applied = sum(1 for r in results if r.get("status") == "submitted-confirmed")
        print("\n=== FINISH PASS DONE ===", flush=True)
        print(
            f"Applied: {applied} | "
            f"Signup: {sum(1 for r in results if r.get('status')=='needs-authentication')} | "
            f"Ready for review: {sum(1 for r in results if r.get('status')=='review-ready')}",
            flush=True,
        )
        print(f"Saved: {out}", flush=True)
        page.wait_for_timeout(60_000)
        try:
            context.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
