"""Ported JobSignal collectors feeding the one ApplyPilot discovery ledger."""

import asyncio
import json
import time
from pathlib import Path

from .discovery import Discovery, _query_words, _word_present, format_job
from .job_sources.collectors import collect, NetworkReader
from .job_sources.models import Source


def source_list(workspace):
    return workspace.setting(
        "jobsignal_sources",
        json.loads((Path(__file__).parent / "job_sources/sources.json").read_text()),
    )


def save_source(workspace, raw):
    source = Source.model_validate(raw)
    rows = source_list(workspace)
    rows = [r for r in rows if r["id"] != source.id]
    if len(rows) >= 20:
        raise ValueError("Limit the registered JobSignal sources to twenty")
    rows.append(source.model_dump(mode="json"))
    workspace.set_setting("jobsignal_sources", rows)
    return {"id": source.id}


async def find(runtime, request):
    from .candidate_matching import match, criteria

    criteria(request)  # validate filters before making external requests
    profile = runtime.workspace.profile()["payload"]
    discovery = Discovery(
        runtime.store, testing=runtime.testing, browser=runtime.browser
    )
    result = await discovery.find(request)
    rows = []
    checked = []
    failures = []
    for row in result["jobs"]:
        row["matching"] = match(row, profile, request)
        if row["matching"]["outcome"] != "NOT_MATCH":
            rows.append(row)
    if request.get("include_builtin", True):
        sources = [
            Source.model_validate(r)
            for r in source_list(runtime.workspace)
            if r.get("enabled")
        ][:10]
        for source in sources:
            if len(rows) >= request.get("requested", 10):
                break
            source = source.model_copy(
                update={
                    "max_pages": min(source.max_pages, 3),
                    "max_jobs": min(source.max_jobs, 100),
                }
            )

            def read():
                reader = NetworkReader(source)
                return collect(source, reader)

            try:
                collection = await asyncio.to_thread(read)
                checked.append(source.id)
                for j in collection.jobs:
                    words = _query_words(request["query"])
                    text = " ".join(
                        [j.title, j.employer, j.description, j.requirements_text]
                    ).casefold()
                    if words and not any(_word_present(w, text) for w in words):
                        continue
                    if (
                        request.get("location")
                        and request["location"].casefold() not in j.location.casefold()
                    ):
                        continue
                    job = {
                        "identity": source.id + ":" + j.external_id,
                        "employer": j.employer,
                        "role": j.title,
                        "url": j.apply_url or j.source_url,
                        "source": j.source_url,
                        "location": j.location,
                        "country": j.country,
                        "employment_type": ", ".join(j.job_types),
                        "opening_date": j.opens,
                        "closing_date": j.closes,
                        "requirements": j.requirements_text or j.description,
                        "pay": f"{j.salary_currency} {j.annual_salary_min}"
                        if j.annual_salary_min is not None
                        else "unknown",
                        "checked": time.time(),
                        "listing_kind": "programme_announcement"
                        if source.kind in {"newton", "pwc", "programme"}
                        else "vacancy",
                    }
                    job["opening"] = j.opens or "Unknown"
                    job["deadline"] = j.closes or "Unknown"
                    job["summary"] = format_job(job)
                    job["matching"] = match(job, profile, request, j)
                    if job["matching"]["outcome"] == "NOT_MATCH":
                        continue
                    if all(r["url"] != job["url"] for r in rows):
                        rows.append(job)
                    if len(rows) >= request.get("requested", 10):
                        break
            except Exception:
                failures.append(source.id)
    rows = rows[: request.get("requested", 10)]
    if rows != result["jobs"]:
        result["run_id"] = discovery._persist(
            request["query"],
            request.get("location", ""),
            request.get("requested", 10),
            result.get("sources", []) + checked,
            rows,
        )
    result.update(
        jobs=rows,
        returned=len(rows),
        checked_jobsignal_sources=checked,
        unavailable_jobsignal_sources=failures,
    )
    runtime.workspace.ingest(rows)
    return result
