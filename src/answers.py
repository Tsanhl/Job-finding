from __future__ import annotations

import os
import re
from typing import Any

from .profile import profile_as_prompt_block


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


RULES: list[tuple[tuple[str, ...], str]] = [
    (("email", "e-mail"), "email"),
    (("phone", "mobile", "telephone"), "phone"),
    (("first name", "firstname", "given name"), "first_name"),
    (("middle name", "middle initial", "second name"), "middle_name"),
    (("last name", "surname", "family name"), "last_name"),
    (("full name", "your name"), "full_name"),
    (("city", "current location", "where do you live", "location"), "location"),
    (("linkedin",), "linkedin_url"),
    (("github",), "github_url"),
    (("how did you hear", "where did you hear"), "answers.how_did_you_hear"),
    (("authorized to work", "right to work", "eligible to work", "work authorization", "legal right to work"), "answers.right_to_work"),
    (("sponsorship", "visa sponsorship", "require sponsorship", "need sponsorship", "require a visa"), "answers.require_sponsorship"),
    (("visa type", "visa status", "what visa", "immigration status"), "answers.visa_type"),
    (("driving licence", "driving license", "driver's licence", "driver's license", "full licence", "full license"), "answers.has_driving_licence"),
    (("years of experience", "how many years"), "answers.years_of_experience"),
    (("disability", "reasonable adjustment"), "answers.disability"),
    (("education", "highest level of education", "degree"), "answers.highest_education"),
    (("start date", "when can you start", "availability"), "answers.start_date"),
    (("salary", "compensation", "expected pay", "desired salary", "expected salary"), "answers.salary_range"),
    (("relocate", "relocation"), "answers.willing_to_relocate"),
    (("preferred location", "where would you like", "desired location"), "answers.preferred_location"),
    (("language", "languages spoken", "fluent"), "answers.languages"),
    (("remote", "hybrid", "work from home"), "remote_preference"),
    (("notice", "notice period"), "notice_period"),
]


SECONDARY_TERMS = (
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
)


def _result_items(value: Any, *, name_key: str, result_key: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get(name_key) or item.get("name") or "").strip()
            result = str(item.get(result_key) or item.get("result") or "").strip()
            year = str(item.get("year") or "").strip()
        else:
            name = str(item).strip()
            result = ""
            year = ""
        if name or result:
            items.append({"name": name, "result": result, "year": year})
    return items


def _format_results(items: list[dict[str, str]]) -> str:
    formatted: list[str] = []
    for item in items:
        value = item["name"]
        if item["result"]:
            value = f"{value}: {item['result']}" if value else item["result"]
        if item["year"]:
            value = f"{value} ({item['year']})"
        if value:
            formatted.append(value)
    return "; ".join(formatted)


def _numbered_field_index(question: str, field: str) -> int | None:
    match = re.search(rf"{field}[^0-9]{{0,20}}(\d+)", question)
    if not match:
        return None
    number = int(match.group(1))
    return 0 if number == 0 else number - 1


def _indexed_result(
    question: str,
    items: list[dict[str, str]],
    *,
    fields: tuple[str, ...],
    value_key: str,
) -> str | None:
    for field in fields:
        index = _numbered_field_index(question, field)
        if index is not None and index < len(items):
            return items[index].get(value_key) or None
    return None


def _academic_answer(question: str, profile: dict[str, Any]) -> str | None:
    """Answer academic fields only from structured, candidate-confirmed facts."""

    education = profile.get("education") or {}
    secondary = profile.get("school_qualifications") or {}
    if not isinstance(education, dict):
        education = {}
    if not isinstance(secondary, dict):
        secondary = {}

    is_secondary = any(term in f" {question} " for term in SECONDARY_TERMS)
    secondary_results = _result_items(
        secondary.get("results"),
        name_key="subject",
        result_key="grade",
    )
    if is_secondary:
        if any(term in question for term in ("qualification type", "qualification system", "exam type")):
            return str(secondary.get("type") or "").strip() or None
        indexed_subject = _indexed_result(
            question,
            secondary_results,
            fields=("subject", "qualification"),
            value_key="name",
        )
        if indexed_subject:
            return indexed_subject
        indexed_grade = _indexed_result(
            question,
            secondary_results,
            fields=("grade", "mark", "result"),
            value_key="result",
        )
        if indexed_grade:
            return indexed_grade
        if "school" in question or "institution" in question:
            return str(secondary.get("school") or "").strip() or None
        if "country" in question:
            return str(secondary.get("country") or "").strip() or None
        if any(term in question for term in ("completion", "completed", "year awarded", "date awarded")):
            return str(secondary.get("completion_year") or "").strip() or None
        if "grading scale" in question:
            return str(secondary.get("grading_scale") or "").strip() or None
        if any(term in question for term in ("resit", "retake", "re-sit")):
            return str(secondary.get("resits") or "").strip() or None
        if any(term in question for term in ("predicted", "achieved", "status")):
            return str(secondary.get("status") or "").strip() or None
        if any(term in question for term in ("subject", "grade", "mark", "result", "qualification")):
            return _format_results(secondary_results) or None

    university_terms = (
        "university",
        "higher education",
        "undergraduate",
        "degree",
        "module",
        "course",
    )
    if any(term in question for term in university_terms):
        modules = _result_items(
            education.get("modules") or education.get("highlights"),
            name_key="name",
            result_key="mark",
        )
        if "module" in question:
            if any(term in question for term in ("grade", "mark", "result")):
                indexed_mark = _indexed_result(
                    question,
                    modules,
                    fields=("grade", "mark", "result"),
                    value_key="result",
                )
                if indexed_mark:
                    return indexed_mark
            else:
                indexed_module = _indexed_result(
                    question,
                    modules,
                    fields=("module", "subject"),
                    value_key="name",
                )
                if indexed_module:
                    return indexed_module
            return _format_results(modules) or None
        if "institution" in question or "university name" in question:
            return str(education.get("institution") or education.get("school") or "").strip() or None
        if "country" in question:
            return str(education.get("country") or "").strip() or None
        if "degree type" in question or "qualification type" in question:
            return str(education.get("degree_type") or education.get("degree") or "").strip() or None
        if "subject" in question or "course" in question:
            return str(education.get("subject") or education.get("degree") or "").strip() or None
        if "classification" in question or "degree grade" in question:
            return str(education.get("classification") or "").strip() or None
        if any(term in question for term in ("overall mark", "overall average", "percentage")):
            return str(education.get("overall_mark") or "").strip() or None
        if any(term in question for term in ("start date", "start year", "started")):
            return str(education.get("start") or "").strip() or None
        if any(term in question for term in ("end date", "end year", "graduation", "completion")):
            return str(education.get("end") or "").strip() or None
        if "status" in question or "predicted" in question:
            return str(education.get("status") or education.get("classification") or "").strip() or None
        if "degree" in question:
            return str(
                education.get("degree")
                or " ".join(
                    value
                    for value in (
                        str(education.get("degree_type") or "").strip(),
                        str(education.get("subject") or "").strip(),
                    )
                    if value
                )
            ).strip() or None

    return None


def _get_path(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def answer_from_profile(
    question: str,
    profile: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> str | None:
    defaults = defaults or {}
    q = _norm(question)
    # Location-specific sponsorship overrides (e.g. USA) live in defaults
    merged = {**profile, **{k: v for k, v in defaults.items() if not str(k).startswith("_")}}
    if "require_sponsorship" in defaults:
        answers = dict(merged.get("answers") or {})
        answers["require_sponsorship"] = defaults["require_sponsorship"]
        if "work_authorization" in defaults:
            answers["authorized_to_work"] = (
                "Yes" if "yes" in str(defaults["work_authorization"]).lower()[:3] else "No"
            )
        merged["answers"] = answers

    academic = _academic_answer(q, merged)
    if academic:
        return academic

    for keys, path in RULES:
        if any(k in q for k in keys):
            val = _get_path(merged, path) if "." in path else merged.get(path)
            if val is None and path in defaults:
                val = defaults[path]
            if val is not None and str(val).strip():
                return str(val)

    # Yes/no heuristics only use explicit profile facts. Unknown questions stay
    # blank so the browser flow can pause instead of making a risky guess.
    if q.startswith("are you") or q.startswith("do you") or "yes/no" in q or q.endswith("?"):
        if any(k in q for k in ("driving licence", "driving license", "driver's licence", "driver's license")):
            return str(profile.get("answers", {}).get("has_driving_licence", "")) or None
        if "sponsorship" in q or "sponsor" in q:
            return str(profile.get("answers", {}).get("require_sponsorship", "")) or None
        if any(k in q for k in ("authorize", "eligible", "right to work")):
            return str(
                profile.get("answers", {}).get("authorized_to_work", "")
                or profile.get("answers", {}).get("right_to_work", "")
            ) or None
        if any(k in q for k in ("commute", "relocate")):
            return str(
                profile.get("answers", {}).get("willing_to_commute", "")
                or profile.get("answers", {}).get("willing_to_relocate", "")
            ) or None

    return None


def answer_question(
    question: str,
    profile: dict[str, Any],
    *,
    job_context: str = "",
    defaults: dict[str, Any] | None = None,
    use_ai: bool = True,
) -> str:
    direct = answer_from_profile(question, profile, defaults)
    if direct:
        return direct

    if use_ai and os.getenv("OPENAI_API_KEY"):
        try:
            return _answer_with_openai(question, profile, job_context=job_context)
        except Exception:
            pass

    # Safe fallback — never invent facts when a question is not in the profile.
    q = _norm(question)
    if any(k in q for k in ("gender", "race", "ethnicity", "disability", "veteran")):
        return "Prefer not to say"
    if "cover letter" in q or "why do you want" in q or "why are you interested" in q:
        summary = str(profile.get("summary", "")).strip()
        return summary if summary else ""
    # Short / factual unknown questions — leave blank so Easy Apply validation
    # surfaces them and we PING the user instead of guessing.
    if len(q) < 120 and not any(
        k in q for k in ("why", "describe", "tell us", "summary", "additional", "cover")
    ):
        return ""
    return ""


def _answer_with_openai(question: str, profile: dict[str, Any], *, job_context: str) -> str:
    from openai import OpenAI

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = f"""Answer this job-application question for the candidate.
Rules:
- Be concise (1-4 sentences, or a short phrase if the field is short).
- Do not invent facts.
- For sensitive demographic questions, answer "Prefer not to say" unless profile has an explicit answer.
- If visa/sponsorship is unclear, say the candidate should confirm before submitting.

Question: {question}

Job context:
{job_context[:2000]}

Candidate profile:
{profile_as_prompt_block(profile)}
"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You complete job application screening questions accurately."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def answer_many(
    questions: list[str],
    profile: dict[str, Any],
    *,
    job_context: str = "",
    defaults: dict[str, Any] | None = None,
    use_ai: bool = True,
) -> dict[str, str]:
    return {
        q: answer_question(
            q,
            profile,
            job_context=job_context,
            defaults=defaults,
            use_ai=use_ai,
        )
        for q in questions
    }
