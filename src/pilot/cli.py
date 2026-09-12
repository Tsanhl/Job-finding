"""Foreground runtime commands; inspect/help never loads live profile or secrets."""

import argparse
import asyncio
import json
from pathlib import Path

from .resources import legacy_preview
from .runtime import client, serve
from .store import default_home


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cli.py runtime")
    parser.add_argument("--home", default=str(default_home()))
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--cdp", default="http://127.0.0.1:9333")
    for name in (
        "status",
        "doctor",
        "tabs",
        "profiles",
        "documents",
        "setup-status",
        "source-roots",
    ):
        sub.add_parser(name)
    find_jobs = sub.add_parser("find-jobs")
    find_jobs.add_argument(
        "--query", "--area", dest="query", required=True, help="Job area or role"
    )
    find_jobs.add_argument("--location", default="")
    find_jobs.add_argument("--count", type=int, default=10)
    find_jobs.add_argument("--source", action="append", default=[])
    find_jobs.add_argument("--no-built-in", action="store_true")
    open_links = sub.add_parser("open-job-links")
    open_links.add_argument("urls", nargs="+")
    open_links.add_argument("--count", type=int, required=True)
    linkedin = sub.add_parser("linkedin-easy-apply")
    linkedin.add_argument("--keywords", required=True)
    linkedin.add_argument("--location", required=True)
    linkedin.add_argument("--count", type=int, default=1)
    linkedin.add_argument("--profile", required=True)
    linkedin.add_argument("--document", action="append", default=[])
    linkedin_resume = sub.add_parser("linkedin-resume")
    linkedin_resume.add_argument("batch_id")
    source = sub.add_parser("register-source")
    source.add_argument("url")
    source.add_argument("--name", default="")
    run = sub.add_parser("run")
    run.add_argument("--plan", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--plan", required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("backup")
    restore_parser.add_argument("--approve-live-restore", action="store_true")
    for op in ("pause", "stop", "resume"):
        p = sub.add_parser(op)
        p.add_argument("run_id")
    for op in ("submitted", "open", "review", "detect-edits"):
        p = sub.add_parser(op)
        p.add_argument("application_id")
    for operation in ("backup-workspace", "restore-workspace"):
        command = sub.add_parser(operation)
        command.add_argument("bundle")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    preview = sub.add_parser("migration-preview")
    preview.add_argument("paths", nargs="+")
    preview.add_argument("--output")
    migrate = sub.add_parser("import")
    migrate.add_argument("--reviewed-preview", required=True)
    migrate.add_argument("--approve-live-import", action="store_true")
    migrate.add_argument("--backup", required=True)
    document = sub.add_parser("document")
    document.add_argument("path")
    document.add_argument(
        "--kind",
        required=True,
        choices=["cv", "transcript", "cover_letter", "writing_sample", "other"],
    )
    document.add_argument("--approve", action="store_true")
    sub.add_parser("cache-clear")
    qualification = sub.add_parser("qualify")
    qualification.add_argument("--adapter", required=True)
    qualification.add_argument(
        "--level", choices=["SYNTHETIC", "SUPERVISED", "UNATTENDED"], required=True
    )
    qualification.add_argument("--evidence", required=True)
    qualification.add_argument("--confirm", action="store_true")
    audit = sub.add_parser("audit")
    audit.add_argument("tab_id")
    proposal = sub.add_parser("plan")
    proposal.add_argument("--request", required=True)
    proposal.add_argument("--targets", required=True)
    proposal.add_argument("--profile", required=True)
    proposal.add_argument("--output", required=True)
    gmail = sub.add_parser("gmail")
    gmail.add_argument(
        "action",
        choices=["connect", "status", "disconnect", "revoke", "connections", "job"],
    )
    gmail.add_argument("--email")
    gmail.add_argument("--config")
    gmail.add_argument("--job-id")
    args = parser.parse_args(argv)
    if args.command == "start":
        asyncio.run(serve(args.home, args.cdp))
        return
    if args.command == "audit":
        print(
            json.dumps(
                client({"op": "portal_audit", "tab_id": args.tab_id}, args.home),
                indent=2,
            )
        )
        return
    if args.command == "plan":
        from .intent import propose

        result = propose(
            args.request,
            targets=json.loads(Path(args.targets).read_text()),
            profile_version=args.profile,
        )
        destination = Path(args.output)
        with destination.open("x") as stream:
            destination.chmod(0o600)
            stream.write(json.dumps(result, indent=2))
        print(json.dumps({"saved": str(destination), "requires_confirmation": True}))
        return
    if args.command == "gmail":
        request = {"op": "gmail_" + args.action}
        if args.action == "job":
            if not args.job_id:
                parser.error("--job-id is required")
            request["job_id"] = args.job_id
        elif args.action != "connections":
            if not args.email:
                parser.error("--email is required")
            request["email" if args.action == "connect" else "connection_id"] = (
                args.email.casefold()
            )
        if args.action == "connect":
            if not args.config:
                parser.error("--config is required")
            request["config_path"] = args.config
        print(json.dumps(client(request, args.home), indent=2))
        return
    if args.command in {"backup-workspace", "restore-workspace"}:
        from getpass import getpass
        from .recovery import backup_workspace, restore_workspace

        secret = getpass("Recovery passphrase (not saved): ")
        try:
            if args.command == "backup-workspace":
                if getpass("Confirm recovery passphrase: ") != secret:
                    raise ValueError("Passphrases do not match")
                result = backup_workspace(args.home, args.bundle, secret)
            else:
                result = restore_workspace(args.bundle, args.home, secret)
            print(json.dumps(result))
        finally:
            secret = None
        return
    if args.command == "restore":
        from .maintenance import restore

        print(
            json.dumps(
                restore(args.backup, args.home, confirmed=args.approve_live_restore)
            )
        )
        return
    if args.command == "migration-preview":
        result = legacy_preview(args.paths)
        if args.output:
            path = Path(args.output)
            if path.exists():
                raise ValueError("Preview destination exists")
            path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            path.chmod(0o600)
        print(
            json.dumps(
                {
                    "source_count": len(result["sources"]),
                    "conflicts": result["conflicts"],
                    "confirmation_required": True,
                }
            )
        )
        return
    if args.command == "import":
        if not args.approve_live_import:
            raise PermissionError(
                "Review the dry run and explicitly approve live import"
            )
        # Runtime owns writes. Backup completes before import request.
        client({"op": "backup", "destination": args.backup}, args.home)
        result = client(
            {
                "op": "import_legacy",
                "preview": json.loads(Path(args.reviewed_preview).read_text()),
                "confirmed": True,
            },
            args.home,
        )
    else:
        op = args.command.replace("-", "_")
        request = {"op": op}
        if op == "find_jobs":
            request.update(
                query=args.query,
                location=args.location,
                requested=args.count,
                sources=args.source,
                include_builtin=not args.no_builtin,
            )
        if op == "open_job_links":
            request.update(urls=args.urls, requested=args.count)
        if op == "linkedin_easy_apply":
            request.update(
                keywords=args.keywords,
                location=args.location,
                requested=args.count,
                profile_version=args.profile,
                documents=args.document,
            )
        if op == "linkedin_resume":
            request.update(batch_id=args.batch_id)
        if op == "register_source":
            request.update(url=args.url, name=args.name)
        if hasattr(args, "run_id"):
            request["run_id"] = args.run_id
        if hasattr(args, "application_id"):
            request["application_id"] = args.application_id
        if op == "run":
            request = {"op": "start", "plan": json.loads(Path(args.plan).read_text())}
        if op == "discover":
            request = {
                "op": "discover",
                "plan": json.loads(Path(args.plan).read_text()),
            }
        if op == "document":
            request = {
                "op": "register_document",
                "path": args.path,
                "kind": args.kind,
                "approved": args.approve,
            }
        if op == "qualify":
            request = {
                "op": "qualification",
                "adapter": args.adapter,
                "level": args.level,
                "evidence": args.evidence,
                "confirmed": args.confirm,
            }
        if op == "backup":
            request["destination"] = args.destination
        result = client(request, args.home)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
