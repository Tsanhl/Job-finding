#!/usr/bin/env python3
"""Open Simpson Thacher London careers / apply pages in saved Chromium session."""

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

from src.config import ensure_dirs, load_config

URLS = [
    "https://www.stblaw.com/your-career/why-simpson-thacher/london-office-careers",
    "https://www.stblaw.com/your-career",
]


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    print("Opening Simpson Thacher London careers in Chromium…", flush=True)
    print("If a login/signup wall appears, I'll ping you — please sign in there.", flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=cfg["browser_data_dir"],
            headless=False,
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URLS[0], wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)

        # Try legal apply button
        for sel in [
            "a:has-text('Apply for a Legal Position')",
            "text=Apply for a Legal Position",
            "a:has-text('Apply')",
        ]:
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible(timeout=1500):
                    with context.expect_page(timeout=8000) as np:
                        loc.click()
                    app = np.value
                    app.wait_for_load_state("domcontentloaded")
                    print(f"Opened apply page: {app.url}", flush=True)
                    page = app
                    break
            except Exception:
                try:
                    loc.click()
                    page.wait_for_timeout(2000)
                    print(f"Navigated: {page.url}", flush=True)
                    break
                except Exception:
                    continue

        html = page.content().lower()
        needs_signup = any(
            k in html
            for k in (
                "create an account",
                "sign up",
                "register",
                "log in",
                "sign in",
                "new user",
            )
        )
        # Collect visible opportunity-like links
        links = page.evaluate(
            """() => [...document.querySelectorAll('a')]
              .map(a => ({t: (a.innerText||'').trim(), h: a.href}))
              .filter(x => x.t && x.h && /train|intern|graduate|paralegal|trainee|vacancy|apply|career/i.test(x.t+x.h))
              .slice(0, 40)"""
        )
        print("\n=== STB page findings ===", flush=True)
        print(f"URL: {page.url}", flush=True)
        if needs_signup:
            print("PING: signup/login may be required on this page.", flush=True)
        for item in links:
            print(f"- {item['t'][:80]} → {item['h']}", flush=True)

        print("\nLeaving browser open 4 minutes for you to use your STB account…", flush=True)
        page.wait_for_timeout(240_000)
        try:
            context.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
