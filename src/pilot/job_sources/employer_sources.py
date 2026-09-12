"""Small reviewed programme-page adapters; never invent a missing opening year."""
import re
from datetime import datetime
from .collectors import Collection, SourceError, make_job, plain
from .models import Evidence, Requirements

PWC_GRADUATE = "https://www.pwc.co.uk/careers/early-careers/graduate.html"


def collect_programme(source, reader):
    text = plain(reader.fetch(source.url))
    if source.kind == "newton":
        opening = re.search(r"Applications\s+open\s+(\d{2}/\d{2}/\d{4})", text, re.I)
        if not opening or "Graduate Consultant" not in text:
            raise SourceError("newton_programme_markup_or_opening_changed")
        opens = datetime.strptime(opening[1], "%d/%m/%Y").date().isoformat()
        required = ("penultimate", "final year", "degree background", "AAB", "travel in the UK")
        if not all(word.casefold() in text.casefold() for word in required):
            raise SourceError("newton_requirements_changed_review_needed")
        # Short factual summaries, not a copy of the article or an inferred legal ruling.
        summary = "Any degree subject; penultimate/final-year students or recent graduates. A-level benchmark AAB or equivalent, with contextual assessment. UK travel and periods away from home required. Work permission and sponsorship need employer confirmation."
        evidence = [Evidence(field="study_stages", excerpt="penultimate or final year", url=source.url, reviewed=True),
                    Evidence(field="any_subject", excerpt="any degree background", url=source.url, reviewed=True)]
        job = make_job(source, external_id="graduate-consultant", title="Graduate Consultant Programme",
                       source_url=source.url, location="UK", country="GB", opens=opens,
                       requirements_text=summary, description="Official graduate programme announcement.",
                       requirements=Requirements(any_subject=True, study_stages=["penultimate", "final", "graduate"]),
                       application_checks=["Check school-grade equivalence and contextual exceptions; confirm what counts as a recent graduate.",
                                           "Opening time and application deadline are not published in this announcement."],
                       evidence=evidence, areas=["Consulting"], job_types=["Graduate programme"],
                       rolling="rolling basis" in text.casefold(),
                       portal_state="closed" if re.search(r"applications (?:are )?(?:currently )?closed", text, re.I) else "unverified")
        return Collection([job])
    graduate = plain(reader.fetch(PWC_GRADUATE))
    opening = re.search(r"Graduate opportunities will be available to apply to from\s+(\d{1,2}\s+[A-Za-z]+(?:\s+20\d{2})?)", text, re.I)
    if not opening or "Graduate" not in graduate or "one graduate programme per recruitment year" not in graduate:
        raise SourceError("pwc_programme_or_application_rule_changed")
    checks = ["One graduate programme application per recruitment year; choose your pathway carefully.",
              "This is a programme overview. Verify the individual vacancy's grade, work-permission and location requirements."]
    if not re.search(r"20\d{2}", opening[1]):
        checks.append("The FAQ omits the opening year. Recruitment cycle and opening time need confirmation.")
    return Collection([make_job(source, external_id="graduate-overview", title="Graduate opportunities — programme overview",
                source_url=PWC_GRADUATE, location="UK", country="GB", opens=opening[1],
                requirements_text="Many degree subjects accepted. A degree pass is sufficient for many pathways; individual roles can add requirements. Sponsorship varies by role and office.",
                application_checks=checks, description="Opening-date evidence: "+source.url,
                areas=["Consulting", "Accounting & audit", "Law", "Technology", "Banking & finance"],
                job_types=["Graduate programme"], rolling=True)])
