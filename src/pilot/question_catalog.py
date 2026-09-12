"""Versioned public question definitions; never stores candidate answers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load():
    path = Path(__file__).resolve().parents[2] / "data" / "preset_questions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("questions"), list):
        raise ValueError("Unsupported preset-question catalogue")
    return payload


def match(question):
    normalized = " ".join(str(question).casefold().split())
    for item in load()["questions"]:
        terms = item.get("match_terms") or []
        if terms and all(term.casefold() in normalized for term in terms):
            return item
    return None


def answer(profile, question, *, employer=""):
    """Return a confirmed reusable answer; application-specific choices stay blank."""
    item = match(question)
    if not item or item.get("reuse") == "application_specific":
        return ""
    path = item.get("profile_path") or ""
    value = profile
    for part in path.split(".") if path else ():
        value = value.get(part) if isinstance(value, dict) else None
    if item["id"] == "previously_worked_for_employer" and employer:
        overrides = profile.get("employer_answers", {})
        override = overrides.get(employer.casefold(), {}).get(item["id"])
        if override not in {None, ""}:
            value = override
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return str(value or "")
