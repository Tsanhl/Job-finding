#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from src.answers import answer_many
from src.application_flow import (
    direct_application_intake_questions,
    missing_academic_details,
    missing_application_details,
    readiness_message,
)
from src.browser_session import get_context
from src.config import ensure_dirs, load_config
from src.cover_letter import generate_cover_letter, save_cover_letter
from src.external_apply import fill_generic_application_form
from src.linkedin_apply import run_linkedin_auto_apply, slugify
from src.profile import load_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="ApplyPilot — job application automation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_letter = sub.add_parser("cover-letter", help="Generate a cover letter")
    p_letter.add_argument("--company", required=True)
    p_letter.add_argument("--role", required=True)
    p_letter.add_argument("--location", default="")
    p_letter.add_argument("--jd-file", default="")
    p_letter.add_argument("--no-ai", action="store_true")

    p_ans = sub.add_parser("answer", help="Answer screening questions from a text file")
    p_ans.add_argument("--questions-file", required=True)
    p_ans.add_argument("--no-ai", action="store_true")

    p_li = sub.add_parser("linkedin", help="Run LinkedIn Easy Apply automation")
    p_li.add_argument("--keywords", required=True)
    p_li.add_argument("--location", default="United Kingdom")
    p_li.add_argument("--max", type=int, default=5)
    p_li.add_argument("--submit", action="store_true", help="Actually submit (default is dry-run)")
    p_li.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm the pre-application checklist before opening the browser",
    )
    p_li.add_argument("--no-ai", action="store_true")

    p_pre = sub.add_parser("preflight", help="Show details required before browser automation")
    p_pre.add_argument("--keywords", default="")
    p_pre.add_argument("--location", default="")

    p_ext = sub.add_parser("external", help="Open and safely fill one external application")
    p_ext.add_argument("--url", required=True)
    p_ext.add_argument("--company", default="")
    p_ext.add_argument("--role", default="")
    p_ext.add_argument(
        "--application-type",
        choices=("law", "graduate", "other"),
        default="other",
    )
    p_ext.add_argument("--questions-file", default="")
    p_ext.add_argument("--job-description-file", default="")
    p_ext.add_argument("--confirm", action="store_true")
    p_ext.add_argument("--no-ai", action="store_true")

    args = parser.parse_args()
    cfg = load_config()
    ensure_dirs(cfg)
    profile = load_profile()
    defaults = dict(cfg.get("defaults", {}))

    if args.cmd == "preflight":
        print(readiness_message(profile, cfg["cv_path"], keywords=args.keywords, location=args.location))
        print("\nBefore browser work, confirm your profile facts, CV, AI-drafting choice, and submit policy.")
        return

    if args.cmd == "cover-letter":
        jd = Path(args.jd_file).read_text(encoding="utf-8") if args.jd_file else ""
        letter = generate_cover_letter(
            profile,
            company=args.company,
            role=args.role,
            location=args.location,
            job_description=jd,
            use_ai=not args.no_ai,
        )
        path = save_cover_letter(
            letter,
            cfg["output_dir"],
            f"cover_letter_{slugify(args.company)}_{slugify(args.role)}.txt",
        )
        print(letter)
        print(f"\nSaved: {path}")

    elif args.cmd == "answer":
        questions = [
            q.strip()
            for q in Path(args.questions_file).read_text(encoding="utf-8").splitlines()
            if q.strip()
        ]
        answers = answer_many(
            questions,
            profile,
            defaults=defaults,
            use_ai=not args.no_ai,
        )
        for q, a in answers.items():
            print(f"Q: {q}\nA: {a}\n")

    elif args.cmd == "linkedin":
        missing = missing_application_details(
            profile,
            cfg["cv_path"],
            keywords=args.keywords,
            location=args.location,
            mode="linkedin",
        )
        if missing:
            print("Before applying, please provide:")
            print("\n".join(f"- {item}" for item in missing))
            return
        if not args.confirm:
            print("Preflight passed. Re-run with --confirm after reviewing the profile and CV.")
            return
        summary = run_linkedin_auto_apply(
            keywords=args.keywords,
            location=args.location,
            cv_path=cfg["cv_path"],
            browser_data_dir=cfg["browser_data_dir"],
            max_applications=args.max,
            delay_seconds=float(cfg.get("linkedin", {}).get("delay_seconds_between_apps", 8)),
            easy_apply_only=True,
            headless=False,
            dry_run=not args.submit,
            use_ai=not args.no_ai,
            defaults=defaults,
            profile=profile,
            output_dir=cfg["output_dir"],
        )
        print(summary.to_dict())

    elif args.cmd == "external":
        portal_questions = (
            Path(args.questions_file).read_text(encoding="utf-8")
            if args.questions_file
            else ""
        )
        job_description = (
            Path(args.job_description_file).read_text(encoding="utf-8")
            if args.job_description_file
            else ""
        )
        missing = missing_application_details(
            profile,
            cfg["cv_path"],
            mode="external",
            target_url=args.url,
        )
        if not args.company.strip():
            missing.append("Please provide the employer's exact name with --company.")
        if not args.role.strip():
            missing.append("Please provide the programme or role with --role.")
        missing.extend(
            missing_academic_details(
                profile,
                application_text=f"{job_description}\n{portal_questions}",
                require_all=False,
            )
        )
        intake = direct_application_intake_questions(
            profile,
            cfg["cv_path"],
            application_type=args.application_type,
            target_url=args.url,
        )
        print("Direct-application intake questions:")
        print("\n".join(f"- {item}" for item in intake))
        if missing:
            print("\nBefore applying, please provide:")
            print("\n".join(f"- {item}" for item in missing))
            return
        if not args.confirm:
            print(
                "\nAnswer the intake questions, review the profile and approved CV, then "
                "re-run with --confirm."
            )
            return
        context = get_context(cfg["browser_data_dir"])
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        detail = fill_generic_application_form(
            page,
            profile=profile,
            defaults=defaults,
            cv_path=cfg["cv_path"],
            job_context=(
                f"{args.application_type} application: {args.role} at {args.company}\n"
                f"{job_description}\nPortal questions and limits:\n{portal_questions}"
            ),
            company=args.company,
            role=args.role,
            use_ai=not args.no_ai,
            dry_run=False,
            log=print,
        )
        print(detail)


if __name__ == "__main__":
    main()
