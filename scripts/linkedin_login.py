#!/usr/bin/env python3
"""Open Chromium for LinkedIn login. Session is saved for ApplyPilot."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from src.config import ensure_dirs, load_config


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    print("=" * 60, flush=True)
    print("LINKEDIN LOGIN", flush=True)
    print("A Chromium window will open.", flush=True)
    print("Cursor's panel browser CANNOT be used by the bot.", flush=True)
    print("Log into LinkedIn in Chromium (email/password or Google).", flush=True)
    print("Leave the window open until you see 'LOGIN SAVED'.", flush=True)
    print("=" * 60, flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=cfg["browser_data_dir"],
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=90000)
        except Exception as exc:
            print(f"Navigation issue: {exc}", flush=True)

        deadline = time.time() + 600  # 10 minutes
        logged_in = False
        while time.time() < deadline:
            try:
                if page.is_closed():
                    print("Browser was closed. Re-run this script and keep Chromium open.", flush=True)
                    raise SystemExit(1)
                url = page.url.lower()
                nav = page.locator(".global-nav, input[placeholder*='Search']").count() > 0
                on_app = ("linkedin.com" in url) and ("login" not in url) and ("authwall" not in url)
                if on_app and (nav or any(x in url for x in ("/feed", "/jobs", "/in/"))):
                    # Confirm jobs page works
                    page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1500)
                    if "login" not in page.url.lower():
                        logged_in = True
                        break
                page.wait_for_timeout(2000)
            except Exception as exc:
                if "TargetClosed" in type(exc).__name__ or "closed" in str(exc).lower():
                    print("Browser closed early. Run again and keep it open while logging in.", flush=True)
                    raise SystemExit(1) from exc
                page.wait_for_timeout(2000)

        if not logged_in:
            print("Timed out waiting for login.", flush=True)
            try:
                context.close()
            except Exception:
                pass
            raise SystemExit(1)

        print("LOGIN SAVED — session kept in .browser_data/", flush=True)
        print("You can close Chromium. Next I can run London/UK applications.", flush=True)
        page.wait_for_timeout(4000)
        try:
            context.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
