"""Versioned public question definitions; never stores candidate answers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ANSWER_TYPES = {"yes_no", "choice", "text", "date_or_text", "signature"}
_ASK_POLICIES = {"first_setup", "optional_profile", "when_encountered", "required_only"}
_REUSE_POLICIES = {
    "application_specific",
    "country_specific",
    "default_with_employer_override",
    "reusable_after_confirmation",
    "reusable_until_changed",
}


def _validate(payload):
    if payload.get("version") != 2 or not isinstance(payload.get("questions"), list):
        raise ValueError("Unsupported preset-question catalogue")
    seen = set()
    for item in payload["questions"]:
        if not isinstance(item, dict):
            raise TypeError("Preset-question entries must be objects")
        question_id = item.get("id")
        if not isinstance(question_id, str) or not question_id or question_id in seen:
            raise ValueError("Preset-question ids must be unique non-empty strings")
        seen.add(question_id)
        terms = item.get("match_terms")
        if not isinstance(terms, list) or not terms or not all(
            isinstance(term, str) and term.strip() for term in terms
        ):
            raise ValueError(f"Invalid match terms for {question_id}")
        if not isinstance(item.get("category"), str) or not item["category"].strip():
            raise ValueError(f"Missing category for {question_id}")
        if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
            raise ValueError(f"Missing prompt for {question_id}")
        if item.get("answer_type") not in _ANSWER_TYPES:
            raise ValueError(f"Invalid answer type for {question_id}")
        if item.get("ask_policy") not in _ASK_POLICIES:
            raise ValueError(f"Invalid ask policy for {question_id}")
        if item.get("reuse") not in _REUSE_POLICIES:
            raise ValueError(f"Invalid reuse policy for {question_id}")
        path = item.get("profile_path")
        if not isinstance(path, str):
            raise TypeError(f"Invalid profile path for {question_id}")
        if not isinstance(item.get("setup_required"), bool):
            raise TypeError(f"Invalid setup flag for {question_id}")
        if item.get("setup_required") is True and (
            not path or item.get("ask_policy") != "first_setup"
        ):
            raise ValueError(f"Invalid setup requirement for {question_id}")
        if item.get("reuse") == "application_specific" and path:
            raise ValueError(f"Application-specific question has profile path: {question_id}")
        if item.get("answer_type") == "choice" and path:
            choices = item.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError(f"Reusable choice question has no choices: {question_id}")
        if "answer" in item or "default_answer" in item:
            raise ValueError(f"Tracked candidate answer found for {question_id}")
    return payload


@lru_cache(maxsize=1)
def load():
    path = Path(__file__).resolve().parents[2] / "data" / "preset_questions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _validate(payload)


def match(question):
    normalized = " ".join(str(question).casefold().split())
    matches = []
    for item in load()["questions"]:
        terms = item.get("match_terms") or []
        if terms and all(term.casefold() in normalized for term in terms):
            matches.append((len(terms), sum(len(term) for term in terms), item))
    return max(matches, default=(0, 0, None), key=lambda candidate: candidate[:2])[2]


def answer(profile, question, *, employer=""):
    """Return a confirmed reusable answer; application-specific choices stay blank."""
    item = match(question)
    if not item or item.get("reuse") == "application_specific":
        return ""
    path = item.get("profile_path") or ""
    if not path:
        return ""
    value = profile
    for part in path.split(".") if path else ():
        value = value.get(part) if isinstance(value, dict) else None
    if item.get("reuse") == "default_with_employer_override" and employer:
        overrides = profile.get("employer_answers", {})
        override = overrides.get(employer.casefold(), {}).get(item["id"])
        if override not in {None, ""}:
            value = override
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return str(value or "")
