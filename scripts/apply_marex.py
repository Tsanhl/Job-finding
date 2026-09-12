#!/usr/bin/env python3
"""Open and safely fill one external ATS page.

The old version of this script contained a candidate's contact details and a
single hardcoded job. Keep those values in the ignored local profile instead.
Despite the historical filename, this script now works with any external URL.
"""

from __future__ import annotations

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.pilot.legacy import main as runtime_entry
    runtime_entry()
    raise SystemExit(0)


import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application_flow import missing_application_details
from src.application_models import AiPolicy, RunMode
from src.browser_session import get_context
from src.config import ensure_dirs, load_config
from src.external_apply import fill_generic_application_form
from src.profile import load_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely fill an external application form")
    parser.add_argument("--url", required=True, help="External application URL")
    parser.add_argument("--company", default="the company")
    parser.add_argument("--role", default="the role")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument(
        "--ai-policy",
        choices=("unknown", "allowed", "prohibited"),
        default="unknown",
    )
    args = parser.parse_args()

    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    missing = missing_application_details(
        profile,
        cfg["cv_path"],
        mode="external",
        target_url=args.url,
    )
    if missing:
        print("Before applying, please provide:")
        print("\n".join(f"- {item}" for item in missing))
        raise SystemExit(2)

    context = get_context(cfg["browser_data_dir"])
    page = context.new_page()
    page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
    detail = fill_generic_application_form(
        page,
        profile=profile,
        defaults=dict(cfg.get("defaults", {})),
        cv_path=cfg["cv_path"],
        job_context=f"{args.role} at {args.company}",
        company=args.company,
        role=args.role,
        use_ai=not args.no_ai and args.ai_policy == "allowed",
        dry_run=False,
        log=lambda message: print(message, flush=True),
        mode=RunMode.ASSISTED_REVIEW,
        ai_policy=(
            AiPolicy.PROHIBITED if args.no_ai else AiPolicy.parse(args.ai_policy)
        ),
    )
    print(f"Result: {detail.to_dict()}")
    print("The external form remains open. Review it and submit manually if ready.")


if __name__ == "__main__":
    main()
