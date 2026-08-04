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
