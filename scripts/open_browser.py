#!/usr/bin/env python3
"""
OWNER process — launches Chromium and keeps it open forever.
Workers attach via CDP :9333. Never kill this unless user says: finish all tasks
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import connect_existing, ensure_page, launch_owner
from src.config import ensure_dirs, load_config


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    print("=" * 60, flush=True)
    print("OWNER: Chromium keep-alive", flush=True)
    print("Stays open until you say: finish all tasks", flush=True)
    print("CDP: http://127.0.0.1:9333", flush=True)
    print("=" * 60, flush=True)

    existing = connect_existing()
    if existing is not None:
        print("Chromium already open — keep-alive without relaunch.", flush=True)
        context = existing
        ensure_page(context)
    else:
        context = launch_owner(cfg["browser_data_dir"])
        ensure_page(context)

    print("Chromium is open. Leave this terminal job running.", flush=True)
    while True:
        try:
            ensure_page(context)
            time.sleep(5)
        except KeyboardInterrupt:
            print("Keep-alive interrupted — Chromium left running.", flush=True)
            break
        except Exception:
            time.sleep(3)
            again = connect_existing()
            if again is not None:
                context = again


if __name__ == "__main__":
    main()
