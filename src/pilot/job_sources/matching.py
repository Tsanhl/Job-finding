from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .models import Job, Profile, Search
from .countries import country_name
from ..store import digest, encode

UTC = timezone.utc
STATUS_ORDER = {"URGENT": 0, "OPEN": 1, "UPCOMING": 2, "CHECK PORTAL": 3, "CLOSED": 4}
LEVELS = {"undergraduate": 1, "postgraduate": 2, "phd": 3, "other": 0}
GRADES = {"first": 5, "2:1": 4, "2:2": 3, "third": 2, "pass": 1}


def parse_date(raw: str) -> date | datetime | None:
    """No missing year/timezone inference, and never silently discard a stated time."""
    raw = raw.strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", raw):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value if value.tzinfo is not None else None
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", raw):
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
    return None


def display_date(raw: str) -> str:
    value = parse_date(raw)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return f"{value:%d %B %Y}; exact time unpublished"
    return (raw + " — date/time not normalised") if raw else "Unpublished / not extracted"


def local_date(value: date | datetime, zone: str) -> date:
    return value.astimezone(ZoneInfo(zone)).date() if isinstance(value, datetime) else value


def classify_status(row: dict, now: datetime | None = None) -> tuple[str, list[str]]:
    now = now or datetime.now(UTC)
    j: Job = row["job"]
    opens, closes = parse_date(j.opens), parse_date(j.closes)
    day = now.astimezone(ZoneInfo(j.timezone)).date()
    notes = []
    expired = (now >= closes if isinstance(closes, datetime) else day > closes) if closes else False
    future = (now < opens if isinstance(opens, datetime) else day < opens) if opens else False
    if j.conflict:
        return "CHECK PORTAL", [j.conflict]
    if opens and closes:
        impossible = opens > closes if type(opens) is type(closes) else local_date(opens, j.timezone) > local_date(closes, j.timezone)
        if impossible:
            return "CHECK PORTAL", ["Opening and closing evidence is inconsistent"]
    if j.portal_state == "accepting" and (expired or future):
        return "CHECK PORTAL", ["Portal acceptance evidence conflicts with published dates"]
    if j.portal_state == "closed" and closes and not expired:
        return "CHECK PORTAL", ["Portal closed evidence conflicts with an unexpired deadline"]
    if expired:
        return "CLOSED", ["Published deadline has passed; not a tested application submission"]
    if j.portal_state == "closed":
        return "CLOSED", ["Source reports the portal closed"]
    if not row.get("present", True):
        return "CHECK PORTAL", ["No longer present in the latest complete source listing; closure not inferred"]
    if now.timestamp() - row["last_seen"] > row.get("fresh_seconds", 2700):
        return "CHECK PORTAL", ["Source evidence is stale; do not treat as a confirmed opening"]
    if row.get("source_state") in {"FAILED", "PARTIAL"}:
        notes.append("Latest source collection failed or was incomplete; last successful evidence is shown")
    if (j.opens and not opens) or (j.closes and not closes):
        return "CHECK PORTAL", notes + ["A stated opening/deadline cannot be safely normalised"]
    if future:
        return "UPCOMING", notes
    urgent = (timedelta(0) < closes-now <= timedelta(days=3)) if isinstance(closes, datetime) else (closes and 0 <= (closes-day).days <= 3)
    if urgent:
        return "URGENT", notes + (["Deadline within 72 hours"] if isinstance(closes,datetime) else ["Deadline within three calendar days; exact time is unpublished"])
    return "OPEN", notes + ["Recently source-listed; application acceptance has not been tested"]


def contains(term: str, text: str) -> bool:
    # Word boundaries prevent 'law' matching 'flaw' and 'AI' matching 'retail'.
    return bool(re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()))


def match_job(job: Job, profile: Profile, search: Search) -> dict:
    reasons, checks, failures = [], [], []
    score = 40

    def preference(wanted, available, label):
        nonlocal score
        if not wanted:
            return
        if not available:
            (checks if search.include_unknown else failures).append(f"{label} not classified/published")
        elif set(wanted) & set(available):
            reasons.append(f"{label} matches your selection")
            score += 10
        else:
            failures.append(f"Outside selected {label.lower()}")

    preference(search.areas, job.areas, "Job area")
    preference(search.job_types, job.job_types, "Opportunity type")
    work_countries = job.remote_countries if job.work_mode == "remote" and job.remote_restrictions_known else ([job.country] if job.country else [])
    preference(search.countries, work_countries, "Country")
    if job.work_mode == "remote" and not job.remote_restrictions_known:
        checks.append("Permitted remote-work countries are not confirmed; remote does not mean worldwide")
    if job.geography_note:
        checks.append(job.geography_note)
    preference(search.work_modes, [job.work_mode] if job.work_mode != "unknown" else [], "Work mode")
    if search.locations:
        if not job.location:
            (checks if search.include_unknown else failures).append("Location not published")
        elif any(contains(x, job.location) for x in search.locations):
            reasons.append("Location matches")
            score += 10
        else:
            failures.append("Outside selected locations; remote work is not assumed worldwide")
    text = f"{job.title} {job.description} {job.requirements_text}"
    if search.keywords:
        if any(contains(x, text) for x in search.keywords):
            reasons.append("Keyword matches")
            score += 10
        else:
            failures.append("No selected keyword matched")
    if any(job.employer.casefold() == x.casefold() for x in search.excluded_employers):
        failures.append("Employer excluded by you")
    if search.min_annual_salary is not None:
        if job.annual_salary_max is None or job.salary_currency != search.salary_currency:
            checks.append("Comparable annual salary not published; no currency/period conversion inferred")
        elif job.annual_salary_max < search.min_annual_salary:
            failures.append("Published salary range is below your minimum")

    req = job.requirements
    # Hard employer constraints only use operator-reviewed field-specific evidence.
    def supported(field: str) -> bool:
        return job.reviewed(field)

    if job.requirements_text and not any(e.reviewed for e in job.evidence):
        checks.append("Advert requirements have not been reviewed as structured rules; check the full requirements")
    if req.study_stages:
        if not supported("study_stages"):
            checks.append("Study-stage requirement needs source review")
        else:
            stages, uncertain = set(), not bool(profile.education)
            for education in profile.education:
                if education.status == "completed":
                    stages.add("graduate")
                elif education.status == "current" and education.study_year and education.course_years:
                    if education.study_year == 1:
                        stages.add("first")
                    if education.study_year == education.course_years - 1:
                        stages.add("penultimate")
                    if education.study_year == education.course_years:
                        stages.add("final")
                else:
                    uncertain = True
            if stages.intersection(req.study_stages):
                reasons.append("Your study stage matches the reviewed requirement")
                score += 5
            elif uncertain:
                checks.append("Add current study year and total course years to check study-stage requirements")
            else:
                failures.append("Your study stage is outside the reviewed requirement")
    for field, supplied, label in (("required_skills", profile.skills, "skills"),
                                    ("required_languages", profile.languages, "languages")):
        required = getattr(req, field)
        if not required:
            continue
        if not supported(field):
            checks.append(f"Required {label} need source review")
            continue
        matched = [s for s in required if any(s.casefold() == p.casefold() for p in supplied)]
        missing = [s for s in required if s not in matched]
        if matched:
            reasons.append(f"Required {label} in your search answers: "+", ".join(matched))
            score += min(10, len(matched)*2)
        if missing:
            checks.append(f"Confirm required {label} not listed in your search answers: "+", ".join(missing))

    if req.minimum_experience_months is not None and supported("minimum_experience_months"):
        if search.max_required_experience_months is not None and req.minimum_experience_months > search.max_required_experience_months:
            failures.append("Role requires more experience than your selected maximum")
        if profile.experience_months is None:
            checks.append("Your relevant experience needs checking")
        elif profile.experience_months < req.minimum_experience_months:
            failures.append("Stated experience is below the reviewed minimum")
    if req.degree_level and supported("degree_level"):
        if not profile.education:
            checks.append("Education not supplied")
        elif not any(LEVELS[e.level] >= LEVELS[req.degree_level] for e in profile.education):
            failures.append("Education level is below the reviewed requirement")
        elif any(e.status != "completed" for e in profile.education):
            checks.append("Confirm qualification will be completed by the required start date")
    if req.subjects and not req.any_subject and supported("subjects"):
        if not any(e.subject for e in profile.education):
            checks.append("Study subject not supplied")
        elif not any(contains(subject, e.subject) for e in profile.education for subject in req.subjects):
            checks.append("Subject does not exactly match; employer-equivalence review required")
    if req.any_subject and supported("any_subject"):
        reasons.append("Reviewed advert accepts any degree subject")
    if req.minimum_grade and supported("minimum_grade"):
        eligible_education = [e for e in profile.education if e.level == (req.degree_level or "undergraduate")]
        known = [GRADES[e.grade] for e in eligible_education if e.grade in GRADES]
        if not known:
            checks.append("Comparable degree classification not supplied; equivalence not inferred")
        elif max(known) < GRADES[req.minimum_grade]:
            failures.append("Reported/predicted grade is below the reviewed minimum")
        if any(e.grade_is_predicted for e in eligible_education):
            checks.append("Degree grade is predicted, not a confirmed final result")
    years = [e.graduation_year for e in profile.education if e.level == "undergraduate" and e.graduation_year]
    if (req.graduation_year_from is not None or req.graduation_year_to is not None) and supported("graduation_window"):
        if not years:
            checks.append("Undergraduate graduation year not supplied")
        elif not any((req.graduation_year_from is None or y >= req.graduation_year_from) and
                     (req.graduation_year_to is None or y <= req.graduation_year_to) for y in years):
            failures.append("Outside the reviewed undergraduate graduation-year window")

    possible = set(work_countries) & set(search.countries) if search.countries else set(work_countries)
    rights = [w for w in profile.work_rights if w.country in possible]
    right = min(rights, key=lambda w: {"unrestricted":0,"time_limited":1,"needs_sponsorship":2,"unknown":3}[w.status]) if rights else None
    if not work_countries:
        checks.append("Work country unknown; work-right matching not evaluated")
    elif not right or (right.status == "unknown" and right.authorized_to_work is not True):
        checks.append("Your work rights for this country need checking")
    else:
        if right.status == "unknown":
            reasons.append("You confirmed current permission to work in this country")
            checks.append("Permission duration and any programme-specific work-right conditions still need checking")
        if right.require_sponsorship is False:
            reasons.append("You reported no current sponsorship requirement")
        if right.status == "needs_sponsorship":
            if req.sponsorship == "no" and supported("sponsorship"):
                failures.append("You need sponsorship; reviewed advert states it is unavailable")
            elif req.sponsorship != "yes" or not supported("sponsorship"):
                checks.append("Sponsorship availability is unconfirmed")
            else:
                reasons.append("Advert states sponsorship is available; individual eligibility still requires employer review")
        if req.unrestricted_right_required and supported("unrestricted_right_required"):
            if right.status != "unrestricted":
                failures.append("Reviewed advert requires unrestricted work rights")
        if right.sponsorship_later and req.sponsorship == "no" and supported("sponsorship"):
            checks.append("Future sponsorship needs conflict with stated no-sponsorship policy; check programme duration")
        if right.status == "time_limited":
            start = parse_date(job.starts)
            if not right.expires_on or not start:
                checks.append("Check permission expiry against the full programme dates")
            elif right.expires_on < local_date(start, job.timezone):
                checks.append("Current permission expires before job start; renewal/sponsorship must be confirmed")
        if req.residence_years is not None and supported("residence_years"):
            if right.continuous_residence_years is None:
                checks.append("Residence/security-clearance condition needs confirmation")
            elif right.continuous_residence_years < req.residence_years:
                failures.append("Reported continuous residence is below the reviewed requirement")
    if req.driving_licence_required and supported("driving_licence_required") and not set(work_countries).intersection(profile.driving_licence_countries):
        checks.append("Required driving licence not confirmed; check geographic exceptions")
    start = parse_date(job.starts)
    if start and profile.available_from and profile.available_from > local_date(start, job.timezone):
        failures.append("Your availability is after the published start date")
    if not job.requirements_text:
        checks.append("Full requirements were not extracted; read official source")
    outcome = "NOT_MATCH" if failures else ("CHECK_REQUIRED" if checks else "PREFERENCE_MATCH")
    return {"outcome": outcome, "score": min(100, score), "reasons": reasons,
            "checks": list(dict.fromkeys(checks)), "failures": list(dict.fromkeys(failures)),
            "notice": "Relevance screening only; never a confirmation of legal or employer eligibility."}


def material_fingerprint(job: Job) -> str:
    data = job.model_dump(mode="json")
    for field in ("source_id", "external_id", "source_url", "evidence", "demo"):
        data.pop(field, None)
    return digest(encode(data))
