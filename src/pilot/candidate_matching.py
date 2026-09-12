"""Read-only projection of the owner's confirmed facts into JobSignal matching."""

import re

from .job_sources.models import Profile, Education, WorkRight, Search, Job
from .job_sources.matching import match_job


def candidate(payload):
    confirmed = payload.get("_confirmed_fields")
    if isinstance(confirmed, list):
        payload = {k: v for k, v in payload.items() if k in confirmed}
    result = Profile()
    education = payload.get("education", [])
    for record in education if isinstance(education, list) else [education]:
        if not isinstance(record, dict):
            continue
        fields = {
            k: v
            for k, v in record.items()
            if k in Education.model_fields and v not in ("", None)
        }
        if fields.get("grade") not in {
            "first",
            "2:1",
            "2:2",
            "third",
            "pass",
            "other",
            "unknown",
        }:
            fields.pop("grade", None)
        try:
            result.education.append(Education.model_validate(fields))
        except ValueError:
            continue  # preserve the source fact; uncertainty stays a check
    for record in payload.get("work_rights", []):
        if not isinstance(record, dict):
            continue
        fields = {
            k: v
            for k, v in record.items()
            if k in WorkRight.model_fields and v not in ("", None)
        }
        if not fields.get("country"):
            continue  # Never default an unspecified right to GB.
        try:
            result.work_rights.append(WorkRight.model_validate(fields))
        except ValueError:
            continue
    for key in ("skills", "languages"):
        values = payload.get(key, [])
        if isinstance(values, list):
            setattr(result, key, [v[:100] for v in values if isinstance(v, str)][:20])
    return result


def criteria(request):
    raw = request.get("filters", {})
    if not isinstance(raw, dict):
        raise ValueError("Search filters must be an object")
    # Portfolios refer to the canonical profile, never a nested competing candidate.
    return Search.model_validate(
        {
            "countries": [],
            **{
                k: v
                for k, v in raw.items()
                if k not in {"candidate", "include_in_alerts"}
            },
        }
    )


def project(row):
    return Job(
        source_id="public",
        external_id=row["identity"][:200],
        employer=row["employer"],
        title=row["role"],
        location=row.get("location", ""),
        source_url=row["url"],
        apply_url=row["url"],
        country=row.get("country", ""),
        requirements_text=row.get("requirements", ""),
        opens=row.get("opening", ""),
        closes=row.get("deadline", ""),
    )


def match(row, payload, request, source_job=None):
    return match_job(source_job or project(row), candidate(payload), criteria(request))
