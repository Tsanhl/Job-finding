import json
import pytest
from pydantic import ValidationError
from src.pilot.job_sources.collectors import Collection,NetworkReader,SourceError,collect,parse_jsonld,public_addresses,retry_seconds,plain,classify_title
from src.pilot.job_sources.models import Source,safe_url,canonical_url


def src(kind='greenhouse',**kw):
    return Source(id='example',name='Example',employer='Example',kind=kind,
        url='https://example.com/jobs',allowed_hosts=['example.com'],link_hosts=['jobs.example.com'],**kw)

class Reader:
    def __init__(self,pages):self.pages=list(pages);self.urls=[]
    def fetch(self,url):self.urls.append(url);return self.pages.pop(0)

@pytest.mark.parametrize('url',[
 'http://example.com','file:///etc/passwd','https://user:password@example.com/a',
 'https://example.com:8443','https://127.0.0.1/jobs','https://169.254.169.254/',
 'https://[::1]/','https://localhost/','https://foo.internal/','https://example.com/\nheader',
 'https://example.com\\@evil.example/a'
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):safe_url(url)

def test_canonical_retains_job_id_and_removes_tracking():
    assert canonical_url('https://Example.com/apply?utm_source=x&job_id=7&lang=en#top')=='https://example.com/apply?job_id=7&lang=en'

def test_admin_sources_need_review_before_enabled():
    with pytest.raises(ValidationError):src(enabled=True)

def test_no_wildcard_allowlist():
    with pytest.raises(ValidationError):
        Source(id='bad',name='Bad',employer='Bad',kind='jsonld',url='https://example.com/a',allowed_hosts=['*.example.com'])

def test_html_sanitized_not_executed():
    assert plain('<script>steal()</script><p>Hello</p>')=='Hello'

def test_greenhouse_fixture_and_no_updated_at_opening():
    data={'jobs':[{'id':1,'title':'Graduate Consultant','absolute_url':'https://example.com/jobs/1','location':{'name':'London'},'content':'<p>Requirements here</p>','updated_at':'2026-09-10T00:00:00Z'}],'meta':{'total':1}}
    result=collect(src(),Reader([json.dumps(data)]))
    assert result.complete
    assert result.jobs[0].opens=='' and result.jobs[0].posted==''
    assert result.jobs[0].description=='Requirements here'
    assert result.jobs[0].areas==['Consulting']

def test_greenhouse_incomplete_list_reported():
    assert not collect(src(),Reader([json.dumps({'jobs':[],'meta':{'total':100}})])).complete

def test_changed_schema_is_failure_not_empty_success():
    with pytest.raises(SourceError,match='schema_changed'):
        collect(src(),Reader(['{"error":"unexpected"}']))

def test_unapproved_application_host_fails():
    data={'jobs':[{'id':1,'title':'Graduate','absolute_url':'https://malicious.example/a'}]}
    with pytest.raises(SourceError,match='unapproved_application_link'):
        collect(src(),Reader([json.dumps(data)]))

def test_lever_pagination():
    def batch(first,count):return [{'id':str(i),'text':'Graduate analyst','hostedUrl':f'https://example.com/jobs/{i}','categories':{'location':'London'}} for i in range(first,first+count)]
    reader=Reader([json.dumps(batch(0,100)),json.dumps(batch(100,2))])
    result=collect(src('lever'),reader)
    assert len(result.jobs)==102 and result.complete
    assert 'skip=100' in reader.urls[1]

def test_lever_repeating_pages_fails():
    page=json.dumps([{'id':str(i),'text':'Graduate','hostedUrl':f'https://example.com/{i}'} for i in range(100)])
    with pytest.raises(SourceError,match='did_not_advance'):
        collect(src('lever'),Reader([page,page]))

def test_jsonld_posting_date_is_not_opening():
    page='<script type="application/ld+json">'+json.dumps({'@context':'https://schema.org','@type':'JobPosting','title':'Graduate','datePosted':'2026-09-01','validThrough':'2026-10-01','url':'https://example.com/jobs/1','qualifications':'Any subject'})+'</script>'
    job=parse_jsonld(src('jsonld'),'https://example.com/jobs/1',page)[0]
    assert job.posted=='2026-09-01' and job.opens=='' and job.closes=='2026-10-01'
    assert job.requirements_text=='Any subject'
    assert not job.evidence # unreviewed text is never converted into hard eligibility constraints

def test_empty_login_page_not_open_job():
    with pytest.raises(SourceError,match='no_jobposting'):
        collect(src('jsonld'),Reader(['<h1>Candidate login</h1>']))

def test_empty_index_not_proof_of_closed():
    with pytest.raises(SourceError,match='no_listing_links'):
        collect(src('index'),Reader(['<h1>Oops</h1>']))

def test_dns_private_rebinding_rejected(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(SourceError,match='non_public'):
        public_addresses('example.com')

def test_redirect_host_checked_before_destination_fetch(monkeypatch):
    r=NetworkReader(src('jsonld'));calls=[]
    def raw(url):
        calls.append(url)
        if url.endswith('/robots.txt'):return (404,{},'')
        return (302,{'location':'https://evil.example/private'},'')
    monkeypatch.setattr(r,'_raw',raw)
    with pytest.raises(SourceError,match='unapproved_redirect'):
        r.fetch('https://example.com/jobs')
    assert not any('evil.example' in x for x in calls)

def test_robots_denial_not_bypassed(monkeypatch):
    r=NetworkReader(src('jsonld'));calls=[]
    def raw(url):calls.append(url);return (200,{},'User-agent: *\nDisallow: /jobs\n')
    monkeypatch.setattr(r,'_raw',raw)
    with pytest.raises(SourceError,match='robots_disallowed'):r.fetch('https://example.com/jobs')
    assert calls==['https://example.com/robots.txt']

def test_robots_failure_fails_closed(monkeypatch):
    r=NetworkReader(src('jsonld'));monkeypatch.setattr(r,'_raw',lambda u:(503,{'retry-after':'600'},''))
    with pytest.raises(SourceError) as e:r.fetch('https://example.com/jobs')
    assert e.value.retry_after==600

def test_rate_limit_retry_after():
    assert retry_seconds('300')==300
    assert retry_seconds('bogus') is None
    assert retry_seconds('999999')==86400

def test_classification_is_not_substring_law_or_ai():
    areas,_=classify_title('Retail specialist with flawless execution')
    assert 'Law' not in areas and 'Data & AI' not in areas
