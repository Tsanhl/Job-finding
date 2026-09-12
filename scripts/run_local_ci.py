#!/usr/bin/env python3
"""Run the full synthetic suite locally and write an evidence report. No remote CI."""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "output" / "upgrade-validation"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="applypilot-ci-") as directory:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-s",
            "--junitxml=" + str(out / "results.xml"),
        ]
        environment = {
            **os.environ,
            "APPLYPILOT_RUNTIME_DIR": directory,
            "OPENAI_API_KEY": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (out / "pytest.log").write_text(result.stdout)
        print(result.stdout)
        suites = (
            ET.parse(out / "results.xml").getroot()
            if (out / "results.xml").exists()
            else None
        )
        stats = (
            {
                k: sum(int(s.get(k, 0)) for s in suites.iter("testsuite"))
                for k in ("tests", "failures", "errors", "skipped")
            }
            if suites is not None
            else {}
        )
        concurrency = []
        for line in result.stdout.splitlines():
            if '{"workers":' in line:
                try:
                    concurrency.append(json.loads(line[line.index('{"workers":') :]))
                except json.JSONDecodeError:
                    pass
        pytest_summary = next(
            (
                line.strip()
                for line in reversed(result.stdout.splitlines())
                if " passed" in line or " failed" in line or " error" in line
            ),
            "pytest summary unavailable",
        )
        import platform

        import apsw

        report = {
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "python": sys.version,
            "platform": platform.platform(),
            "sqlite": apsw.sqlitelibversion(),
            "command": command,
            "exit_code": result.returncode,
            "junit": stats,
            "pytest_summary": pytest_summary,
            "concurrency": concurrency,
            "live_verified": False,
        }
        sys.path.insert(0, str(ROOT))
        from src.pilot.qualification import source_digest

        report["source_digest"] = source_digest()
        names = (
            {
                case.get("name")
                for case in suites.iter("testcase")
                if case.find("failure") is None and case.find("error") is None
            }
            if suites is not None
            else set()
        )
        report["qualified_workflows"] = (
            ["workday:submit"]
            if result.returncode == 0
            and "test_workday_submission_contract_requires_identity_and_receipt"
            in names
            else []
        )
        if result.returncode == 0:
            for provider in ("allhires", "apply4law"):
                if f"test_family_save_review_submit_and_receipt[{provider}]" in names:
                    report["qualified_workflows"].append(provider + ":submit")
        (out / "report.json").write_text(json.dumps(report, indent=2))
        lines = [
            "# ApplyPilot local test report",
            "",
            f"Recorded: {report['recorded_at']}",
            f"Python: {platform.python_version()}; SQLite: {apsw.sqlitelibversion()}",
            "",
            "Command: `.venv-upgrade/bin/python -m pytest -q -s --junitxml=output/upgrade-validation/results.xml`",
            "",
            f"Exit code: {result.returncode}",
            f"Pytest summary: {pytest_summary}",
            f"JUnit counts: {stats}",
            "",
            "## Measured browser concurrency",
            "",
        ]
        lines += [
            f"- Requested {r['workers']}: measured {r['measured_browser_upload_overlap']} overlapping browser uploads; maximum {r['maximum_active']} active workers."
            for r in concurrency
        ]
        lines += [
            "",
            "Tests use disposable SQLite databases, synthetic profiles, loopback HTTP fixtures or intercepted browser requests. OAuth uses simulated provider responses and an in-memory secret backend. No native Keychain, real mailbox, employer registration or live submission was exercised.",
            "",
            "The original baseline was 86 passed plus 6 subtests. Added coverage includes the four function requests, first-run readiness, target-scoped final actions, four-source discovery persistence, bounded listing-to-detail crawling, area filtering, CHECK PORTAL formatting, exact link counts, external-draft redaction and a multi-step LinkedIn review-ready flow that never submits. No tests were deselected.",
            "",
            "Detailed local logs: `output/upgrade-validation/pytest.log`; JUnit XML: `output/upgrade-validation/results.xml`.",
        ]
        (ROOT / "docs" / "TEST_REPORT.md").write_text("\n".join(lines) + "\n")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
