"""Exact confirmed personal mappings; no nationality or date-precision inference."""

from ..field_manifest import FieldKind, resolve_deterministic_answer

ALIASES = {
    "uk": "GB",
    "united kingdom": "GB",
    "gb": "GB",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "hong kong": "HK",
    "hk": "HK",
}


def resolve(field, profile, target):
    from .question_catalog import answer, match

    definition = match(field.question)
    preset = answer(profile, field.question, employer=getattr(target, "employer", ""))
    if preset:
        return preset
    if definition and definition.get("reuse") == "application_specific":
        return ""
    if field.kind in {FieldKind.ELIGIBILITY, FieldKind.SPONSORSHIP}:
        country = ALIASES.get(target.country.casefold(), target.country.upper())
        records = profile.get("work_rights", {})
        if isinstance(records, list):
            matching = [
                r
                for r in records
                if isinstance(r, dict) and r.get("country") == country
            ]
            rights = matching[0] if country and len(matching) == 1 else {}
        else:
            rights = (
                records.get(country, {})
                if country and isinstance(records, dict)
                else {}
            )
        key = (
            "authorized_to_work"
            if field.kind == FieldKind.ELIGIBILITY
            else "require_sponsorship"
        )
        value = rights.get(key)
        return str(value) if value is not None else ""
    text = (field.question + " " + field.field_id).casefold()
    if any(
        term in text
        for term in (
            "driving licence",
            "driving license",
            "driver's license",
            "drivers licence",
            "driving_licence",
        )
    ):
        for section in (
            "answers",
            "application_defaults",
            "general_application_defaults",
        ):
            answer = profile.get(section, {}).get("has_driving_licence")
            if answer is not None and answer != "":
                return (
                    "Yes"
                    if answer is True
                    else "No"
                    if answer is False
                    else str(answer)
                )
    if "extension" in text and field.kind == FieldKind.PHONE:
        return str(profile.get("phone_extension") or "")
    if "country phone code" in text:
        return (
            ""  # Preserve the portal's selected country; never insert the main number.
        )
    if "salary" in text:
        policy = next(
            (
                profile.get(section, {}).get("salary_expectation_policy")
                for section in (
                    "answers",
                    "application_defaults",
                    "general_application_defaults",
                )
                if profile.get(section, {}).get("salary_expectation_policy")
            ),
            None,
        )
        if policy == "ask_when_required":
            return ""  # Exact application answers remain available through the engine.
    proposal = resolve_deterministic_answer(field, profile=profile, defaults={})
    if proposal.value:
        return proposal.value
    if field.kind not in {FieldKind.UNKNOWN, FieldKind.CUSTOM_DROPDOWN}:
        return ""
    question = " ".join(field.question.casefold().strip(" ?*:").split())
    paths = {
        "date of birth": ("date_of_birth",),
        "preferred name": ("preferred_name",),
        "address": ("address", "formatted"),
        "home address": ("address", "formatted"),
        "street address": ("address", "street"),
        "address line 1": ("address", "street"),
        "building": ("address", "building"),
        "flat / unit": ("address", "unit"),
        "city": ("address", "city"),
        "postcode": ("address", "postcode"),
        "postal code": ("address", "postcode"),
        "country": ("address", "country"),
        "country of residence": ("address", "country"),
        "availability": ("answers", "start_date"),
        "when can you start": ("answers", "start_date"),
        "salary expectation": ("answers", "salary_expectation"),
        "university": ("education", "institution"),
        "degree": ("education", "degree"),
        "degree classification": ("education", "classification"),
    }
    value = profile
    for part in paths.get(question, ()):
        value = value.get(part) if isinstance(value, dict) else None
    return (
        str(value)
        if paths.get(question) and isinstance(value, (str, int, float))
        else ""
    )
