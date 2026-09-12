"""Privacy-safe first-run readiness for application-changing functions."""

from __future__ import annotations

from .question_catalog import load as load_questions


def _value(profile, path):
    current = profile
    for part in path.split("."):
        current = current.get(part) if isinstance(current, dict) else None
    return current


def _present(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _has_education(profile):
    if profile.get("education_none_confirmed") is True:
        return True
    value = profile.get("education")
    records = value if isinstance(value, list) else [value]
    return any(
        isinstance(record, dict)
        and _present(record.get("institution") or record.get("school"))
        and _present(record.get("degree") or record.get("degree_type"))
        for record in records
    )


def _has_work_history(profile):
    if profile.get("work_experience_none_confirmed") is True:
        return True
    records = profile.get("work_experience")
    return bool(
        isinstance(records, list)
        and any(
            isinstance(record, dict)
            and _present(record.get("employer"))
            and _present(record.get("position") or record.get("title"))
            for record in records
        )
    )


def profile_gaps(profile):
    """Return labels only; callers must not expose existing private values."""

    missing = []
    name_ready = _present(profile.get("full_name")) or (
        _present(profile.get("first_name")) and _present(profile.get("last_name"))
    )
    required = (
        (name_ready, "legal name"),
        (_present(profile.get("email")), "application email"),
        (_present(profile.get("phone")), "phone number"),
        (_present(profile.get("location")), "current location or address"),
        (_has_education(profile), "education history or an explicit none"),
        (_has_work_history(profile), "work history or an explicit none"),
        (
            _present(_value(profile, "answers.authorized_to_work"))
            or _present(_value(profile, "answers.right_to_work")),
            "country-specific right to work",
        ),
        (
            _present(_value(profile, "answers.require_sponsorship")),
            "sponsorship requirement",
        ),
    )
    missing.extend(label for ready, label in required if not ready)
    reusable = [
        item["profile_path"]
        for item in load_questions()["questions"]
        if item.get("profile_path")
    ]
    if any(not _present(_value(profile, path)) for path in reusable):
        missing.append("reusable screening questions")
    return missing


def setup_status(store, documents, version=""):
    if not version:
        row = store.one(
            "SELECT id FROM profile_versions ORDER BY created DESC LIMIT 1"
        )
        version = row["id"] if row else ""
    if not version:
        return {
            "ready": False,
            "profile_version": "",
            "missing": ["reusable profile", "approved CV"],
        }
    try:
        profile = store.load_profile(version)
    except ValueError:
        return {
            "ready": False,
            "profile_version": "",
            "missing": ["available reusable profile", "approved CV"],
        }
    missing = profile_gaps(profile)
    approved_cv = store.one(
        "SELECT id FROM documents WHERE kind='cv' AND approved=1 ORDER BY created DESC LIMIT 1"
    )
    if not approved_cv:
        missing.append("approved CV")
    return {
        "ready": not missing,
        "profile_version": version,
        "missing": missing,
    }
