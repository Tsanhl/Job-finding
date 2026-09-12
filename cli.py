#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from src.answers import answer_many
from src.application_flow import (
    direct_application_intake_questions,
    missing_academic_details,
    missing_application_details,
    readiness_message,
)
from src.application_models import AiPolicy, RunMode
from src.application_workers import (
    load_application_tasks,
    run_application_batch,
    validate_application_batch,
)
from src.browser_session import get_context
from src.config import ensure_dirs, load_config
from src.cover_letter import generate_cover_letter, save_cover_letter
from src.external_apply import fill_generic_application_form
from src.full_automation import (
    FullAutomationRequest,
    load_full_automation_request,
    run_full_automation,
)
from src.linkedin_apply import run_linkedin_auto_apply, slugify
from src.profile import load_profile


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "runtime":
        from src.pilot.cli import main as runtime_main
        runtime_main(sys.argv[2:])
        return
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
    p_li.add_argument(
        "--submit",
        action="store_true",
        help="Legacy flag: returns submission-disabled without opening the browser",
    )
    p_li.add_argument(
        "--mode",
        choices=("local-preview", "assisted-review"),
        default="local-preview",
    )
    p_li.add_argument(
        "--ai-policy",
        choices=("unknown", "allowed", "prohibited"),
        default="unknown",
    )
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
    p_ext.add_argument(
        "--mode",
        choices=("local-preview", "assisted-review"),
        default="assisted-review",
    )
    p_ext.add_argument(
        "--ai-policy",
        choices=("unknown", "allowed", "prohibited"),
        default="unknown",
    )

    p_batch = sub.add_parser(
        "batch",
        help="Prepare multiple direct applications in separate tabs of a shared browser context",
    )
    p_batch.add_argument("--applications-file", required=True)
    p_batch.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Concurrent worker limit (default: one per application, up to 10)",
    )
    p_batch.add_argument("--confirm", action="store_true")
    p_batch.add_argument("--no-ai", action="store_true")
    p_batch.add_argument(
        "--mode",
        choices=("local-preview", "assisted-review"),
        default="assisted-review",
    )

    p_full = sub.add_parser(
        "full-auto",
        help="Search for suitable jobs and complete several applications concurrently",
    )
    p_full.add_argument("--requirements-file", default="")
    p_full.add_argument("--workers", type=int, default=None)
    p_full.add_argument(
        "--submission-policy",
        choices=("review", "auto-submit"),
        default=None,
    )
    p_full.add_argument("--accept-required-terms", action="store_true")
    p_full.add_argument("--confirm", action="store_true")
    p_full.add_argument("--no-ai", action="store_true")
    p_full.add_argument(
        "--mode",
        choices=("local-preview", "assisted-review"),
        default="assisted-review",
    )
    p_full.add_argument(
        "--ai-policy",
        choices=("unknown", "allowed", "prohibited"),
        default="unknown",
    )

    args = parser.parse_args()
    if args.cmd in {"linkedin", "external", "batch", "full-auto"}:
        parser.error("Application execution now uses the shared runtime: cli.py runtime run --plan reviewed-plan.json")
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
        if args.submit:
            print(
                "submission-disabled: automated final submission is disabled; "
                "no browser was opened"
            )
            return
        ai_policy = AiPolicy.PROHIBITED if args.no_ai else AiPolicy.parse(args.ai_policy)
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
            dry_run=args.mode == "local-preview",
            use_ai=ai_policy == AiPolicy.ALLOWED,
            defaults=defaults,
            profile=profile,
            output_dir=cfg["output_dir"],
            mode=RunMode.parse(args.mode),
            ai_policy=ai_policy,
        )
        print(summary.to_dict())

    elif args.cmd == "external":
        ai_policy = AiPolicy.PROHIBITED if args.no_ai else AiPolicy.parse(args.ai_policy)
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
            use_ai=ai_policy == AiPolicy.ALLOWED,
            dry_run=args.mode == "local-preview",
            log=print,
            mode=RunMode.parse(args.mode),
            ai_policy=ai_policy,
        )
        print(json.dumps(detail.to_dict(), indent=2, ensure_ascii=False))

    elif args.cmd == "batch":
        tasks = load_application_tasks(args.applications_file)
        blockers = validate_application_batch(
            tasks,
            profile=profile,
            default_cv_path=cfg["cv_path"],
            mode=RunMode.parse(args.mode),
        )
        if blockers:
            print("Batch intake is incomplete:")
            for task_id, questions in blockers.items():
                print(f"\n{task_id}:")
                print("\n".join(f"- {question}" for question in questions))
            return
        if not args.confirm:
            print(
                f"{len(tasks)} applications are ready. Re-run with --confirm to open "
                "the local workers. Final submission remains manual."
            )
            return
        summary = run_application_batch(
            tasks,
            profile=profile,
            defaults=defaults,
            default_cv_path=cfg["cv_path"],
            worker_count=args.workers,
            use_ai=not args.no_ai,
            mode=RunMode.parse(args.mode),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    elif args.cmd == "full-auto":
        if args.requirements_file:
            request = load_full_automation_request(args.requirements_file)
        else:
            keywords = tuple(
                value.strip()
                for value in input("Target roles or keywords (comma-separated): ").split(",")
                if value.strip()
            )
            default_locations = profile.get("preferred_locations") or [profile.get("location", "")]
            location_default = "; ".join(value for value in default_locations if value)
            locations_value = input(
                f"Target locations (semicolon-separated) [{location_default}]: "
            ).strip() or location_default
            locations = [value.strip() for value in locations_value.split(";") if value.strip()]
            maximum_value = input("Number of applications [10]: ").strip() or "10"
            paid_only = input("Paid employment only? [Y/n]: ").strip().lower() not in {
                "n",
                "no",
            }
            experience_limit = (
                input("Maximum years of required experience [1]: ").strip() or "1"
            )
            excluded_employers = [
                value.strip()
                for value in input(
                    "Employers already completed or to exclude (comma-separated) [none]: "
                ).split(",")
                if value.strip()
            ]
            policy_value = (
                input("Submission policy (review or auto-submit) [review]: ").strip().lower()
                or "review"
            )
            accept_terms = False
            request = FullAutomationRequest.from_mapping(
                {
                    "keywords": list(keywords),
                    "locations": locations,
                    "max_applications": maximum_value,
                    "paid_roles_only": paid_only,
                    "max_required_experience_years": experience_limit,
                    "excluded_employers": excluded_employers,
                    "submission_policy": policy_value,
                    "accept_required_terms": accept_terms,
                }
            )

        if args.submission_policy:
            request = replace(request, submission_policy=args.submission_policy)
        if args.accept_required_terms:
            request = replace(request, accept_required_terms=True)
        print("Full-automation request:")
        print(json.dumps(request.public_dict(), indent=2, ensure_ascii=False))
        if request.submission_policy == "auto-submit":
            print(
                "submission-disabled: automated final submission is disabled; "
                "no browser was opened"
            )
            return
        if not args.confirm:
            if args.mode == "local-preview":
                print("Re-run with --confirm to inspect matching applications without portal changes.")
            else:
                print(
                    "Re-run with --confirm to provide the application email, choose the "
                    "employer-credential mode, and start the assisted-review workers."
                )
            return

        profile_email = str(profile.get("email") or "").strip()
        email = profile_email
        credentials = None
        if args.mode == "assisted-review":
            email = input(f"Application email [{profile_email}]: ").strip() or profile_email
            print(
                "Create accounts, enter passwords and complete verification yourself "
                "in the managed browser. The application will pause and resume without "
                "reading or storing the password."
            )
        run_profile = dict(profile)
        run_profile["email"] = email
        summary = run_full_automation(
            request,
            credentials=credentials,
            profile=run_profile,
            defaults=defaults,
            cv_path=cfg["cv_path"],
            browser_data_dir=cfg["browser_data_dir"],
            output_dir=cfg["output_dir"],
            worker_count=args.workers,
            use_ai=(
                not args.no_ai and AiPolicy.parse(args.ai_policy) == AiPolicy.ALLOWED
            ),
            log=print,
            ai_policy=(
                AiPolicy.PROHIBITED if args.no_ai else AiPolicy.parse(args.ai_policy)
            ),
            mode=RunMode.parse(args.mode),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
