#!/usr/bin/env python3
"""Create a safe, reusable workspace for one law-firm application."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_APPLICATION_ROOT = Path(__file__).resolve().parent / "applications"
DEFAULT_CANDIDATE_CONFIG = Path(__file__).resolve().parent / "candidate.local.json"


def slugify(value: str) -> str:
    """Return a predictable filesystem-safe slug."""
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalised.lower()).strip("-")
    return slug


def _write_once(path: Path, content: str) -> None:
    """Create a starter file while preserving any existing work."""
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        pass


def _load_canonical_cv(candidate_config: str | Path | None) -> dict[str, Any]:
    """Load the local canonical-CV record without copying the source CV."""
    if candidate_config is None:
        return {}

    config_path = Path(candidate_config).expanduser().resolve()
    if not config_path.exists():
        return {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Candidate config could not be read: {config_path}") from exc

    canonical_cv = config.get("canonical_cv", {})
    if not isinstance(canonical_cv, dict):
        raise ValueError("candidate.local.json must contain a canonical_cv object.")
    return canonical_cv


def _sha256(path: Path) -> str:
    """Return the SHA-256 fingerprint for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_application_workspace(
    firm: str,
    programme: str,
    *,
    office: str = "",
    deadline: str = "",
    application_url: str = "",
    root: str | Path = DEFAULT_APPLICATION_ROOT,
    candidate_config: str | Path | None = DEFAULT_CANDIDATE_CONFIG,
) -> Path:
    """Create and return a non-destructive workspace for one application.

    Existing directories and files are reused without being overwritten. Firm,
    programme and office values are slugged before they become path components.
    """
    firm = firm.strip()
    programme = programme.strip()
    office = office.strip()
    if not firm:
        raise ValueError("Firm is required.")
    if not programme:
        raise ValueError("Programme is required.")

    parts = [slugify(firm), slugify(programme)]
    if office:
        parts.append(slugify(office))
    if any(not part for part in parts):
        raise ValueError("Firm, programme and office must contain letters or numbers.")

    workspace = Path(root).expanduser().resolve() / "--".join(parts)
    folders = [
        "intake",
        "research",
        "evidence",
        "drafts",
        "review",
        "final",
        "documents",
        "cv",
        "cv/draft",
        "cv/review",
        "cv/final",
    ]
    workspace.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        (workspace / folder).mkdir(exist_ok=True)

    created = date.today().isoformat()
    _write_once(
        workspace / "application.md",
        f"""# {firm} — {programme}

- **Office:** {office or "Requires confirmation"}
- **Application page:** {application_url or "Requires research"}
- **Deadline:** {deadline or "Requires research"}
- **Rolling:** Requires research
- **Eligibility:** Requires confirmation
- **Created:** {created}

## Status

Not started.

## Unresolved questions

- Confirm the official application instructions and AI-use policy.
- Confirm eligibility, work-authorisation and sponsorship requirements.
- Confirm all questions and word or character limits.

## Next action

Complete the intake and opportunity check before drafting.
""",
    )
    _write_once(
        workspace / "intake" / "questions.md",
        """# Application questions and instructions

Paste every question exactly as shown in the application portal. Record the word or character limit beside each question.
""",
    )
    _write_once(
        workspace / "research" / "firm_research.md",
        """# Firm research

Record current, application-relevant findings. Distinguish official facts from your analysis and note any conflicting information.
""",
    )
    _write_once(
        workspace / "research" / "sources.md",
        """# Research source log

| Page title | URL | Accessed | Claim supported |
|---|---|---|---|
""",
    )
    _write_once(
        workspace / "evidence" / "fact_ledger.md",
        """# Fact ledger

| Statement | Category | Source | Safe to use? | Notes |
|---|---|---|---|---|

Categories: confirmed candidate fact, confirmed firm fact, reasonable interpretation, or unknown/requires confirmation.
""",
    )
    _write_once(
        workspace / "evidence" / "evidence_map.md",
        """# Evidence map

| Question | Evidence | Competency or motivation | Why it fits | Confirmation needed |
|---|---|---|---|---|
""",
    )
    _write_once(
        workspace / "drafts" / "answers.md",
        """# Draft answers

Keep the exact question and limit above each working draft. Record the actual word count after each draft.
""",
    )
    _write_once(
        workspace / "drafts" / "cover_letter.md",
        """# Cover-letter draft

Use this file only if the firm requests a cover letter.
""",
    )
    _write_once(
        workspace / "review" / "truth_audit.md",
        """# Truth and risk audit

Audit each substantive sentence against the fact ledger before approving final copy.
""",
    )
    _write_once(
        workspace / "review" / "recruiter_review.md",
        """# Recruiter red-team review

Record the score, main strength, main weakness and required revision for each answer.
""",
    )
    _write_once(
        workspace / "final" / "README.md",
        """# Final approved materials

Place only truth-audited, user-approved clean copies here. Creating this folder does not authorise submission.
""",
    )
    _write_once(
        workspace / "documents" / "README.md",
        """# Supporting documents

Keep local supporting files or references here. Do not duplicate, upload or share sensitive documents unless expressly requested.
""",
    )

    canonical_cv = _load_canonical_cv(candidate_config)
    cv_path = str(canonical_cv.get("path", "")).strip()
    cv_hash = str(canonical_cv.get("sha256", "")).strip()
    cv_status = str(canonical_cv.get("status", "")).strip()
    cv_registered = str(canonical_cv.get("registered_on", "")).strip()
    resolved_cv_path = Path(cv_path).expanduser() if cv_path else None
    cv_exists = bool(resolved_cv_path and resolved_cv_path.is_file())
    actual_cv_hash = _sha256(resolved_cv_path) if cv_exists and resolved_cv_path else ""
    if cv_hash and actual_cv_hash:
        fingerprint_status = "Matches" if cv_hash == actual_cv_hash else "Mismatch - confirm the current CV"
    elif actual_cv_hash:
        fingerprint_status = "Source available, but no registered fingerprint"
    else:
        fingerprint_status = "Not verified"
    _write_once(
        workspace / "documents" / "cv_source.md",
        f"""# Canonical CV source

- **Local path:** {cv_path or "Not registered"}
- **Registered:** {cv_registered or "Requires confirmation"}
- **Status:** {cv_status or "Requires confirmation"}
- **Registered SHA-256:** {cv_hash or "Not recorded"}
- **Current SHA-256:** {actual_cv_hash or "Could not be calculated"}
- **Fingerprint check:** {fingerprint_status}
- **Available when workspace was created:** {"Yes" if cv_exists else "No"}

Read the canonical CV before tailoring. Treat it as evidence, keep it unchanged,
and save all firm-specific versions under `cv/`. If the source path or hash has
changed, stop and confirm which CV is current before drafting.
""",
    )
    _write_once(
        workspace / "cv" / "requirements.md",
        """# Firm and programme CV requirements

Record the portal's exact document instructions, permitted format, page limit,
file-size limit, anonymous-recruitment rules, requested competencies and relevant
practice areas. Keep verified firm facts separate from interpretation.
""",
    )
    _write_once(
        workspace / "cv" / "tailoring_map.md",
        """# CV tailoring map

| Firm requirement or priority | Candidate evidence | Evidence source | Proposed CV change | Verification needed |
|---|---|---|---|---|

Map requirements to existing evidence before rewriting. A proposed change may
reorder, select, condense or clarify verified content; it may not create a new fact.
""",
    )
    _write_once(
        workspace / "cv" / "change_log.md",
        """# CV change log

| Date | Source CV | Tailored version | Changes and rationale | Approval status |
|---|---|---|---|---|
""",
    )
    _write_once(
        workspace / "cv" / "draft" / "README.md",
        """# Firm-specific CV drafts

Keep editable working versions here. Draft filenames should identify the firm,
programme and version. Never overwrite the canonical CV.
""",
    )
    _write_once(
        workspace / "cv" / "review" / "checklist.md",
        """# Tailored CV review checklist

- [ ] Every factual claim is supported by the canonical CV or another confirmed source.
- [ ] Dates, grades, titles, organisation names and links match the evidence.
- [ ] The profile and ordering reflect this firm and programme.
- [ ] Legal analysis, commercial awareness, communication and teamwork are prioritised for a conventional TC.
- [ ] Technology is used as a relevant differentiator, not as the entire narrative.
- [ ] Firm keywords are used naturally and only where supported.
- [ ] The document follows the portal's format, page and file-size rules.
- [ ] The rendered PDF has no clipping, overlap, broken links or inconsistent spacing.
- [ ] The candidate has approved the clean version.
""",
    )
    _write_once(
        workspace / "cv" / "final" / "README.md",
        """# Approved tailored CV

Place only the candidate-approved DOCX and/or PDF for this exact application
here. Approval of a CV does not authorise submission.
""",
    )
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a structured workspace for one UK law-firm application."
    )
    parser.add_argument("--firm", required=True)
    parser.add_argument("--programme", required=True)
    parser.add_argument("--office", default="")
    parser.add_argument("--deadline", default="")
    parser.add_argument("--url", default="", dest="application_url")
    parser.add_argument("--root", default=str(DEFAULT_APPLICATION_ROOT))
    parser.add_argument(
        "--candidate-config",
        default=str(DEFAULT_CANDIDATE_CONFIG),
        help="Local JSON file that records the canonical CV path and hash.",
    )
    args = parser.parse_args()

    path = create_application_workspace(
        args.firm,
        args.programme,
        office=args.office,
        deadline=args.deadline,
        application_url=args.application_url,
        root=args.root,
        candidate_config=args.candidate_config,
    )
    print(f"Application workspace ready: {path}")


if __name__ == "__main__":
    main()
