"""Application intake and safety checks shared by the UI and CLI.

The browser workers should only run after the candidate has supplied the facts
needed to complete an application.  This module deliberately reports missing
information instead of guessing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


DIRECT_APPLICATION_TYPES = {
    "law": "law firm programme",
    "graduate": "graduate scheme",
    "other": "direct application",
}


def _value(profile: dict[str, Any], dotted: str) -> str:
    current: Any = profile
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    if isinstance(current, list):
        return " | ".join(str(item).strip() for item in current if str(item).strip())
    return str(current or "").strip()


def missing_application_details(
    profile: dict[str, Any],
    cv_path: str | Path,
    *,
    keywords: str = "",
    location: str = "",
    mode: str = "linkedin",
    target_url: str = "",
) -> list[str]:
    """Return the intake questions that must be answered before browser work."""

    missing: list[str] = []
    required = (
        ("full_name", "your full name"),
        ("email", "your application email"),
        ("phone", "your phone number"),
        ("location", "your current location"),
        ("answers.authorized_to_work", "whether you are authorised to work in the target country"),
        ("answers.require_sponsorship", "whether you require sponsorship"),
        ("answers.start_date", "your start date or availability"),
    )
    for field, prompt in required:
        if not _value(profile, field):
            missing.append(f"Please provide {prompt}.")

    cv = Path(cv_path).expanduser() if str(cv_path).strip() else None
    if cv is None or not cv.is_file() or cv.suffix.lower() != ".pdf":
        missing.append("Please provide the path to an existing CV PDF.")

    if mode == "external":
        if not target_url.strip():
            missing.append("Please provide the external application URL.")
    else:
        if not keywords.strip():
            missing.append("Please provide job keywords or a target role.")
        if not location.strip():
            missing.append("Please provide the job-search location.")

    return missing


def missing_academic_details(
    profile: dict[str, Any],
    *,
    application_text: str = "",
    require_all: bool = True,
) -> list[str]:
    """Return academic facts missing from the local profile.

    ``require_all`` supports profile setup and the comprehensive question list.
    Live direct-application gates pass the portal text with ``require_all=False``
    so academic facts are required only when that application asks for them.
    """

    missing: list[str] = []
    education = profile.get("education") or {}
    secondary = profile.get("school_qualifications") or {}

    if not isinstance(education, dict):
        education = {}
    if not isinstance(secondary, dict):
        secondary = {}

    text = " ".join(application_text.lower().split())
    broad_academic_request = any(
        term in text
        for term in (
            "academic history",
            "education history",
            "full education",
            "all qualifications",
            "academic qualifications",
        )
    )
    university_institution_requested = require_all or broad_academic_request or any(
        term in text for term in ("university", "higher-education institution", "higher education institution")
    )
    degree_requested = require_all or broad_academic_request or any(
        term in text for term in ("degree", "undergraduate qualification", "degree subject")
    )
    university_dates_requested = require_all or broad_academic_request or any(
        term in text
        for term in (
            "university start",
            "degree start",
            "university end",
            "degree end",
            "graduation date",
            "graduation year",
            "degree completion",
        )
    )
    university_result_requested = require_all or broad_academic_request or any(
        term in text
        for term in (
            "degree classification",
            "degree grade",
            "overall average",
            "overall mark",
            "university result",
            "predicted degree",
        )
    )
    modules_requested = require_all or broad_academic_request or "module" in text

    secondary_mentioned = any(
        term in f" {text} "
        for term in (
            "a level",
            "a-level",
            "alevel",
            "international baccalaureate",
            " ib ",
            "hkdse",
            "dse",
            "gcse",
            "secondary school",
            "school qualification",
            "school result",
        )
    )
    secondary_type_requested = require_all or broad_academic_request or secondary_mentioned
    secondary_school_requested = require_all or broad_academic_request or any(
        term in text for term in ("secondary school", "school name", "school attended")
    )
    secondary_year_requested = require_all or broad_academic_request or any(
        term in text
        for term in (
            "qualification year",
            "year awarded",
            "date awarded",
            "school completion",
            "year completed",
        )
    )
    secondary_results_requested = require_all or broad_academic_request or (
        secondary_mentioned
        and any(term in text for term in ("subject", "grade", "mark", "result"))
    )

    if university_institution_requested:
        if not str(education.get("institution") or education.get("school") or "").strip():
            missing.append("Please provide your university or higher-education institution.")
    if degree_requested:
        if not str(
            education.get("degree")
            or education.get("degree_type")
            or education.get("subject")
            or ""
        ).strip():
            missing.append(
                "Please provide your degree type and subject, or state that this is not applicable."
            )
    if university_dates_requested:
        if not str(education.get("start") or "").strip():
            missing.append("Please provide your university start month and year.")
        if not str(education.get("end") or "").strip():
            missing.append("Please provide your graduation or expected completion month and year.")
    if university_result_requested:
        if not str(
            education.get("classification")
            or education.get("overall_mark")
            or education.get("status")
            or ""
        ).strip():
            missing.append(
                "Please provide your achieved or predicted degree classification/overall mark, or mark it pending."
            )
    if modules_requested:
        if not (education.get("modules") or education.get("highlights")):
            missing.append(
                "Please provide every university module and achieved/predicted mark requested by this form."
            )

    if secondary_type_requested:
        if not str(secondary.get("type") or "").strip():
            missing.append(
                "Please identify your school qualification system: A levels, IB, HKDSE, GCSEs, or another qualification."
            )
    if secondary_school_requested:
        if not str(secondary.get("school") or "").strip():
            missing.append("Please provide the school where those qualifications were completed.")
    if secondary_year_requested:
        if not str(secondary.get("completion_year") or "").strip():
            missing.append("Please provide the completion year for your school qualifications.")
    if secondary_results_requested:
        if not secondary.get("results"):
            missing.append(
                "Please provide every school subject and its achieved/predicted grade or mark."
            )

    return missing


def application_intake_questions(
    profile: dict[str, Any],
    cv_path: str | Path,
    *,
    keywords: str = "",
    location: str = "",
    mode: str = "linkedin",
    target_url: str = "",
) -> list[str]:
    """Return user-facing prompts for the pre-application confirmation step."""

    questions = missing_application_details(
        profile,
        cv_path,
        keywords=keywords,
        location=location,
        mode=mode,
        target_url=target_url,
    )
    questions.extend(
        [
            "Please confirm the profile facts and CV that may be used for this application.",
            "Please confirm whether OpenAI may be used for drafting; never use it to invent facts.",
            "Please confirm whether to run a dry run or allow Easy Apply submission.",
            "Please confirm that any unknown question, CAPTCHA, consent, or login wall must pause for you.",
        ]
    )
    return questions


def direct_application_intake_questions(
    profile: dict[str, Any],
    cv_path: str | Path,
    *,
    application_type: str = "other",
    target_url: str = "",
) -> list[str]:
    """Return the questions that must be reviewed before a direct application.

    The questions are intentionally user-facing.  They form a mandatory intake
    step for external law, graduate-scheme, and other direct applications; the
    browser worker must not infer the answers.
    """

    kind = application_type.strip().lower()
    if kind not in DIRECT_APPLICATION_TYPES:
        kind = "other"

    questions = missing_application_details(
        profile,
        cv_path,
        mode="external",
        target_url=target_url,
    )
    questions.extend(
        [
            "What is the employer's exact name, programme or role title, office, recruitment cycle, and deadline?",
            "Do you meet every stated eligibility rule, and is any point uncertain?",
            "Please confirm the personal details, education history, grades, and employment dates that this application may use.",
            "Which school qualification system applies: A levels, International Baccalaureate, HKDSE, GCSEs, or another national qualification?",
            "Please list every school qualification subject with its achieved or predicted grade/mark, completion year, grading scale, and any resits the form requires.",
            "Please list every university module with its year and achieved or predicted mark, plus the degree classification and overall average where requested.",
            "Does the employer require qualification equivalencies or tariff points? Use only an official conversion supplied by the employer or awarding body; never invent one.",
            "Please confirm your right-to-work, visa or sponsorship position for this specific office and start date.",
            "Please paste every application question exactly as shown, including each word or character limit.",
            "Which verified experiences should support the motivation and competency answers, and is any detail still unconfirmed?",
            "Have you previously applied to, worked with, been referred to, or declared a conflict involving this employer?",
            "Do you want to disclose any reasonable-adjustment needs or choose 'Prefer not to say' for optional monitoring questions?",
            "Which documents are requested (for example CV, cover letter, transcript, references, or portfolio), and are the approved versions ready?",
            "What does the employer say about using AI in the application, and may AI be used only within that policy?",
            "Should this run only prepare or fill a draft? Final submission, declarations, signatures, tests, CAPTCHAs, and consent always remain with you.",
        ]
    )

    if kind == "law":
        questions.extend(
            [
                "Why this firm and programme, which practice areas genuinely interest you, and what verified evidence supports those reasons?",
                "Does the form require complete module marks, overseas-qualification equivalents, mitigating circumstances, or SQE details?",
                "Which commercial-awareness topic can you discuss authentically, and how is it relevant to the firm?",
            ]
        )
    elif kind == "graduate":
        questions.extend(
            [
                "Why this organisation, scheme, and business area, and what verified evidence supports those reasons?",
                "Which competency examples best demonstrate the scheme's stated strengths or behaviours?",
                "Does the process include online tests, a video interview, or an assessment centre that must be completed personally?",
            ]
        )
    else:
        questions.extend(
            [
                "Why this organisation and role, and what verified evidence supports those reasons?",
                "Which competency examples best match the role's essential criteria?",
            ]
        )

    # Preserve order while avoiding a duplicate prompt if requirements overlap.
    return list(dict.fromkeys(questions))


def readiness_message(
    profile: dict[str, Any],
    cv_path: str | Path,
    *,
    keywords: str = "",
    location: str = "",
    mode: str = "linkedin",
    target_url: str = "",
) -> str:
    missing = missing_application_details(
        profile,
        cv_path,
        keywords=keywords,
        location=location,
        mode=mode,
        target_url=target_url,
    )
    if not missing:
        return "Ready for assisted application flow. Unknown fields and final external submission still require your review."
    return "Before applying, please answer:\n" + "\n".join(f"- {item}" for item in missing)
