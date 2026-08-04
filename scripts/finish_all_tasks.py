#!/usr/bin/env python3
"""Close the long-lived ApplyPilot Chromium session. Run when user says finish all tasks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser_session import finish_all_tasks
from src.config import load_config


def main() -> None:
    # Also kill any orphaned chrome using our profile
    import subprocess

    cfg = load_config()
    finish_all_tasks()
    subprocess.run(
        ["pkill", "-f", f"user-data-dir={cfg['browser_data_dir']}"],
        check=False,
        capture_output=True,
    )
    print("All tasks finished — Chromium closed.", flush=True)


if __name__ == "__main__":
    main()
