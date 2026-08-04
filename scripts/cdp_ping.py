#!/usr/bin/env python3
"""Worker smoke test: attach via CDP only — must NOT close Chromium."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import connect_existing, ensure_page, get_context
from src.config import load_config


def main() -> None:
    cfg = load_config()
    ctx = connect_existing()
    if ctx is None:
        print("FAIL: cannot attach — start scripts/open_browser.py first", flush=True)
        sys.exit(1)
    page = ensure_page(ctx)
    print(f"OK: attached. pages={len(ctx.pages)} url={(page.url or '')[:100]}", flush=True)
    ctx2 = get_context(cfg["browser_data_dir"])
    print(f"get_context OK pages={len(ctx2.pages)}", flush=True)
    print("Worker exiting — Chromium must STAY open (owner keeps it).", flush=True)


if __name__ == "__main__":
    main()
