#!/usr/bin/env python3
"""Fail closed when a push could contain local candidate data or credentials."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEYS = {
    "address",
    "address_line_1",
    "address_line_2",
    "email",
    "first_name",
    "full_name",
    "last_name",
    "linkedin_url",
    "middle_name",
    "phone",
    "postal_code",
    "postcode",
    "preferred_name",
}
TEXT_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal email": re.compile(
        rb"(?i)\b[A-Z0-9._%+-]+@(?!example\.(?:com|test)|localhost)[A-Z0-9.-]+\.[A-Z]{2,}\b"
    ),
}
CREDENTIAL = re.compile(
    rb"(?i)(?:api[_-]?key|client[_-]?secret|refresh[_-]?token|password)\s*[:=]\s*[\"']([A-Za-z0-9_./+\-=]{20,})[\"']"
)
TOKEN_PREFIX = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})")
PRIVATE_SUFFIXES = {".doc", ".docx", ".pdf", ".rtf"}


def git(*args, input_data=None):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _collect_values(value, *, key=""):
    found = set()
    if isinstance(value, dict):
        for child_key, child in value.items():
            found.update(_collect_values(child, key=str(child_key).casefold()))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_values(child, key=key))
    elif key in PRIVATE_KEYS and isinstance(value, str):
        normalized = value.strip()
        if (
            len(normalized) >= 5
            and "example.test" not in normalized.casefold()
            and normalized.casefold() not in {"unknown", "prefer not to say"}
        ):
            found.add(normalized.encode())
    return found


def local_private_values():
    values = set()
    candidates = [
        ROOT / "data/profile.local.json",
        ROOT / "data/candidate_profile.json",
        ROOT / "data/cv_extracted.json",
        ROOT / "application_profile.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            values.update(_collect_values(json.loads(path.read_text())))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    database = Path.home() / "Library/Application Support/ApplyPilot/applypilot.sqlite3"
    if database.is_file():
        try:
            import apsw

            connection = apsw.Connection(str(database), flags=apsw.SQLITE_OPEN_READONLY)
            try:
                for (payload,) in connection.execute("SELECT payload FROM profile_versions"):
                    values.update(_collect_values(json.loads(payload)))
            finally:
                connection.close()
        except Exception:
            pass
    return values


def scan_blob(label, path, content, private_values, findings):
    suffix = Path(path).suffix.casefold()
    if suffix in PRIVATE_SUFFIXES:
        findings.add((label, path, "tracked private-document type"))
    if len(content) > 5 * 1024 * 1024:
        findings.add((label, path, "blob exceeds privacy scan size limit"))
        return
    folded_content = content.decode("utf-8", errors="ignore").casefold()
    folded_path = path.casefold()
    for value in private_values:
        folded_value = value.decode("utf-8", errors="ignore").casefold()
        if folded_value and folded_value in folded_content:
            findings.add((label, path, "known local private value"))
        if folded_value and folded_value in folded_path:
            findings.add((label, path, "known local private value in path"))
    is_fixture = path.startswith("tests/") or ".example." in path
    if not is_fixture:
        for reason, pattern in TEXT_PATTERNS.items():
            if pattern.search(content):
                findings.add((label, path, reason))
        if CREDENTIAL.search(content) or TOKEN_PREFIX.search(content):
            findings.add((label, path, "probable credential"))


def main():
    findings = set()
    private_values = local_private_values()
    tracked = [
        relative for relative in git("ls-files", "-z").decode().split("\0") if relative
    ]
    for relative in tracked:
        path = ROOT / relative
        if path.is_file():
            scan_blob(
                "working-tree", relative, path.read_bytes(), private_values, findings
            )
    staged = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    for relative in filter(None, staged.decode().split("\0")):
        try:
            content = git("show", f":{relative}")
        except subprocess.CalledProcessError:
            findings.add(("index", relative, "staged content could not be inspected"))
            continue
        scan_blob("index", relative, content, private_values, findings)
    for line in git("rev-list", "--objects", "--all").decode().splitlines():
        object_id, _, path = line.partition(" ")
        if not path or git("cat-file", "-t", object_id).strip() != b"blob":
            continue
        size = int(git("cat-file", "-s", object_id))
        if size > 5 * 1024 * 1024:
            findings.add(("history", path, "blob exceeds privacy scan size limit"))
            continue
        scan_blob(
            "history:" + object_id[:12],
            path,
            git("cat-file", "blob", object_id),
            private_values,
            findings,
        )
    for object_id in git("rev-list", "--all").decode().splitlines():
        scan_blob(
            "commit-message:" + object_id[:12],
            "commit-message.txt",
            git("show", "-s", "--format=%B", object_id),
            private_values,
            findings,
        )
    if findings:
        print("Privacy gate blocked the push:", file=sys.stderr)
        for label, path, reason in sorted(findings):
            print(f"- {label} {path}: {reason}", file=sys.stderr)
        return 1
    print(
        f"Privacy gate passed: {len(tracked)} tracked paths, staged objects, "
        "reachable Git blobs, and commit messages checked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
