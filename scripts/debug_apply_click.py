#!/usr/bin/env python3
"""
DEBUG ONLY — does NOT submit applications.
Verifies Easy Apply click + external Apply tab open still work.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import get_page
from src.config import ensure_dirs, load_config
from src.linkedin_apply import (
    APPLY_BUTTON_SELECTORS,
    _collect_job_hrefs,
    _dismiss_modals,
    build_search_url,
    open_job_detail,
)
from src.urlutil import normalize_job_url


def find_apply(page):
    for sel in APPLY_BUTTON_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=1200):
                return loc, sel
        except Exception:
            continue
    return None, ""


def try_click(page, kind: str) -> dict:
    before_pages = len(page.context.pages)
    before_url = page.url
    loc, sel = find_apply(page)
    out = {"kind": kind, "found": bool(loc), "selector": sel, "clicked": False, "modal": 0, "new_tab": False}
    if not loc:
        return out
    try:
        loc.click(timeout=5000)
        out["clicked"] = True
    except Exception as e:
        out["error"] = str(e)[:160]
        return out
    page.wait_for_timeout(2000)
    out["modal"] = page.locator(".jobs-easy-apply-modal, .jobs-easy-apply-content").count()
    out["new_tab"] = len(page.context.pages) > before_pages
    out["url_changed"] = page.url != before_url
    # Close modal / extra tabs — never submit
    if out["modal"]:
        for s in ("button[aria-label='Dismiss']", "button:has-text('Discard')"):
            try:
                b = page.locator(s).first
                if b.count() and b.is_visible(timeout=500):
                    b.click(timeout=2000)
                    conf = page.locator("button:has-text('Discard')").first
                    if conf.count() and conf.is_visible(timeout=500):
                        conf.click(timeout=2000)
                    break
            except Exception:
                pass
    for p in list(page.context.pages)[1:]:
        try:
            p.close()
        except Exception:
            pass
    return out


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    print("DEBUG PROBE — no submissions.", flush=True)
    page = get_page(cfg["browser_data_dir"])

    page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1200)
    if "login" in page.url.lower() or "authwall" in page.url.lower():
        print("PING: Log into LinkedIn in Chromium…", flush=True)
        for _ in range(150):
            if "login" not in page.url.lower() and page.locator(".global-nav").count():
                break
            page.wait_for_timeout(2000)

    easy_url = build_search_url(
        "legal intern",
        "London, England, United Kingdom",
        easy_apply_only=True,
        entry_level=True,
        past_week=True,
    )
    page.goto(easy_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    hrefs = _collect_job_hrefs(page, limit=5, log=lambda m: print(m, flush=True))
    if not hrefs:
        print("FAIL: no Easy Apply jobs", flush=True)
        return

    href = normalize_job_url(hrefs[0])
    print(f"\n[1] Direct view: {href}", flush=True)
    page.goto(href, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    _dismiss_modals(page)
    r1 = try_click(page, "easy_direct")
    print(f"  → {r1}", flush=True)

    print("\n[2] Search pane open_job_detail", flush=True)
    page.goto(easy_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    open_job_detail(page, href, search_url=easy_url)
    _dismiss_modals(page)
    r2 = try_click(page, "easy_pane")
    print(f"  → {r2}", flush=True)

    print("\n[3] External company-website Apply", flush=True)
    ext_url = build_search_url(
        "software engineer",
        "London, England, United Kingdom",
        easy_apply_only=False,
        entry_level=True,
        past_week=True,
    )
    page.goto(ext_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    hrefs2 = _collect_job_hrefs(page, limit=15, log=lambda m: print(m, flush=True))
    r3 = {"skipped": True}
    for h in hrefs2:
        h = normalize_job_url(h)
        open_job_detail(page, h, search_url=ext_url)
        _dismiss_modals(page)
        loc, sel = find_apply(page)
        if not loc:
            continue
        aria = ((loc.get_attribute("aria-label") or "") + " " + (loc.inner_text() or "")).lower()
        if "easy" in aria:
            continue
        print(f"  candidate: {h} ({aria[:70]})", flush=True)
        before = len(page.context.pages)
        try:
            with page.context.expect_page(timeout=10000) as ni:
                loc.click(timeout=4000)
            ext = ni.value
            ext.wait_for_load_state("domcontentloaded", timeout=30000)
            r3 = {"found": True, "clicked": True, "new_tab": True, "url": ext.url[:160], "selector": sel}
            ext.close()
        except Exception as e:
            r3 = {
                "found": True,
                "error": str(e)[:160],
                "new_tab": len(page.context.pages) > before,
                "url": page.url[:160],
            }
            for p in list(page.context.pages)[1:]:
                try:
                    p.close()
                except Exception:
                    pass
        print(f"  → {r3}", flush=True)
        break

    ok_easy = r1.get("clicked") and r1.get("modal", 0) > 0 and r2.get("clicked") and r2.get("modal", 0) > 0
    ok_ext = bool(r3.get("new_tab") or (r3.get("url") and "linkedin.com" not in str(r3.get("url", "linkedin.com"))))
    print("\n=== SUMMARY ===", flush=True)
    print(f"Easy Apply clickable: {'PASS' if ok_easy else 'FAIL'}", flush=True)
    print(f"External open:        {'PASS' if ok_ext else 'FAIL / none found'}", flush=True)
    print("No applications submitted. Automation path is active.", flush=True)


if __name__ == "__main__":
    main()
