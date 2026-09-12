"""Long-lived Chromium session — owner keeps browser open; workers only attach via CDP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

CDP_URL = "http://127.0.0.1:9333"

_playwright: Playwright | None = None
_context: BrowserContext | None = None
_is_owner: bool = False


def connect_existing() -> BrowserContext | None:
    """Attach to Chromium already listening on CDP. Never launches. Never closes the browser."""
    global _playwright, _context
    try:
        if _playwright is None:
            _playwright = sync_playwright().start()
        browser = _playwright.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            return None
        # Prefer a context that already has tabs
        for ctx in browser.contexts:
            if ctx.pages:
                _context = ctx
                return _context
        _context = browser.contexts[0]
        return _context
    except Exception:
        return None


def launch_owner(browser_data_dir: str, *, headless: bool = False) -> BrowserContext:
    """ONLY open_browser.py should call this. Launches Chromium with CDP for workers."""
    global _playwright, _context, _is_owner
    Path(browser_data_dir).mkdir(parents=True, exist_ok=True)
    if _playwright is None:
        _playwright = sync_playwright().start()
    _context = _playwright.chromium.launch_persistent_context(
        user_data_dir=browser_data_dir,
        headless=headless,
        viewport={"width": 1400, "height": 900},
        locale="en-GB",
        args=[
            "--remote-debugging-port=9333",
        ],
        slow_mo=40,
    )
    _is_owner = True
    if not _context.pages:
        _context.new_page()
    return _context


def ensure_page(context: BrowserContext, url: str = "https://www.linkedin.com/feed/") -> Page:
    """Guarantee at least one tab exists (CDP sometimes shows 0 pages)."""
    if context.pages:
        return context.pages[0]
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except Exception:
        pass
    return page


def get_context(browser_data_dir: str, *, headless: bool = False) -> BrowserContext:
    """
    Workers: attach via CDP only.
    If Chromium is not open, raise — do NOT relaunch (that risks closing the owner).
    """
    global _context
    if _context is not None:
        try:
            _ = _context.pages
            return _context
        except Exception:
            _context = None

    attached = connect_existing()
    if attached is not None:
        return attached

    raise RuntimeError(
        "Chromium is not open (no CDP on :9333). "
        "Run: python scripts/open_browser.py — and leave it running. "
        "Workers must never kill/relaunch Chromium."
    )


def get_page(browser_data_dir: str) -> Page:
    context = get_context(browser_data_dir)
    return ensure_page(context)


def finish_all_tasks() -> None:
    """Call ONLY when user says finish all tasks — closes Chromium."""
    global _playwright, _context, _is_owner
    if _context is not None:
        try:
            _context.close()
        except Exception:
            pass
        _context = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None
    _is_owner = False


def defaults_for_location(base: dict[str, Any], location: str) -> dict[str, Any]:
    """Add neutral location metadata without guessing work-authorisation facts."""
    d = dict(base)
    loc = (location or "").lower()
    if any(x in loc for x in ("united states", "usa", "u.s.", "new york", "san francisco", "remote, us")):
        d.setdefault("work_authorization", "")
        d.setdefault("require_sponsorship", "")
        d.setdefault("visa_type", "")
        d["_usa"] = True
    else:
        d.setdefault("work_authorization", "")
        d.setdefault("require_sponsorship", "")
        d.setdefault("visa_type", "")
        d["_usa"] = False
    return d
