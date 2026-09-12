"""Synthetic page fragments test source drift and uncertain recruitment years."""
import pytest
from src.pilot.job_sources.collectors import collect, SourceError
from src.pilot.job_sources.employer_sources import PWC_GRADUATE
from src.pilot.job_sources.models import Source


class Reader:
    def __init__(self, pages): self.pages = pages
    def fetch(self, url): return self.pages[url]


def config(kind):
    return Source(id=kind, name=kind, employer=kind.title(), kind=kind,
                  url='https://example.com/programme', country='GB', allowed_hosts=['example.com'], link_hosts=['www.pwc.co.uk'])


NEWTON = '<h1>Graduate Consultant</h1><p>Applications open 14/09/2026. penultimate and final year. degree background. AAB. travel in the UK.</p>'


def test_newton_explicit_date_and_reviewed_study_stage():
    s = config('newton')
    job = collect(s, Reader({s.url: NEWTON})).jobs[0]
    assert job.opens == '2026-09-14'
    assert job.requirements.study_stages == ['penultimate', 'final', 'graduate']
    assert job.requirements_text and job.application_checks


@pytest.mark.parametrize('old,new', [('14/09/2026', 'soon'), ('AAB', 'ABB')])
def test_newton_source_change_requires_review(old, new):
    s = config('newton')
    with pytest.raises(SourceError): collect(s, Reader({s.url: NEWTON.replace(old, new)}))


@pytest.mark.parametrize('opening,uncertain', [('17 September', True), ('17 September 2026', False)])
def test_pwc_never_invents_missing_year(opening, uncertain):
    s = config('pwc')
    job = collect(s, Reader({s.url: 'Graduate opportunities will be available to apply to from '+opening+'.',
                            PWC_GRADUATE: 'Graduate. one graduate programme per recruitment year'})).jobs[0]
    assert job.opens == opening
    assert any('omits the opening year' in check for check in job.application_checks) == uncertain
    assert '2027' not in job.title
