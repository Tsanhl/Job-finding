from __future__ import annotations

import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit, quote
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import certifi

from .models import Job, Source, canonical_url, safe_url
from .countries import country_code

MAX_BYTES = 3_000_000
USER_AGENT = "JobSignal/0.2 (+operator-reviewed recruitment sources; read-only)"


class SourceError(RuntimeError):
    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.retry_after = retry_after


@dataclass
class Collection:
    jobs: list[Job]
    complete: bool = True


def public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise SourceError("non_public_destination")
    return addresses


def retry_seconds(header: str | None) -> int | None:
    if not header:
        return None
    if header.isdigit():
        return min(86400, max(1, int(header)))
    try:
        return min(86400, max(1, int((parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        return None


class NetworkReader:
    """Exact host allowlists + public-IP pinning + TLS hostname checks on every hop.

    No proxies, cookies, credentials, arbitrary user URLs, JavaScript or CAPTCHA bypass.
    A shared DB request budget coordinates hosts across local worker processes.
    """
    def __init__(self, source: Source, store=None, budget_seconds: float = 120):
        self.source, self.store = source, store
        self.deadline = time.monotonic()+budget_seconds
        self.robots = {}
        self.next_allowed = {}
        self.delays = {}

    def _check_budget(self):
        if time.monotonic() >= self.deadline:
            raise SourceError("source_time_budget_exceeded")

    def _pace(self, host: str):
        interval = max(self.source.min_request_interval, self.delays.get(host, 0))
        now = time.time()
        if self.store:
            with self.store.connection(write=True) as c:
                self.store.lock(c, "host:"+host)
                row = c.execute("SELECT next_allowed FROM host_pacing WHERE host=?", (host,)).fetchone()
                due = max(now, row[0] if row else 0)
                c.execute("INSERT INTO host_pacing VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_allowed=excluded.next_allowed", (host, due+interval))
        else:
            due = max(now, self.next_allowed.get(host, 0))
            self.next_allowed[host] = due+interval
        wait = max(0, due-now)
        if time.monotonic()+wait >= self.deadline:
            raise SourceError("host_pacing_exceeds_source_budget")
        if wait:
            time.sleep(wait)

    def _raw(self, url: str):
        self._check_budget()
        parsed = urlsplit(safe_url(url))
        host = parsed.hostname
        if host not in self.source.allowed_hosts:
            raise SourceError("unapproved_redirect_or_host")
        ips = public_addresses(host)
        self._pace(host)
        timeout = min(10, max(.5, self.deadline-time.monotonic()))
        context = ssl.create_default_context(cafile=certifi.where())
        raw = socket.create_connection((ips[0], 443), timeout=timeout)
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=context)
        try:
            conn.sock = context.wrap_socket(raw, server_hostname=host)
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                         "Accept": "application/json,text/html,text/plain"})
            response = conn.getresponse()
            if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
                raise SourceError("unexpected_compressed_response")
            body = bytearray()
            while True:
                self._check_budget()
                # read1 does not wait for a whole 64K chunk on a trickling response.
                chunk = response.read1(min(65536, MAX_BYTES+1-len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise SourceError("response_size_limit")
            return response.status, {k.lower(): v for k, v in response.getheaders()}, body.decode("utf-8", "replace")
        finally:
            conn.close()
            raw.close()

    def _robots_for(self, url: str):
        p = urlsplit(url)
        origin = f"https://{p.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        target = origin+"/robots.txt"
        for _ in range(4):
            status, headers, body = self._raw(target)
            if status in {301, 302, 303, 307, 308}:
                target = urljoin(target, headers.get("location", ""))
                continue
            if status == 404:
                parser = None
            elif status == 200:
                parser = RobotFileParser()
                parser.parse(body.splitlines())
                delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0
                rate = parser.request_rate(USER_AGENT) or parser.request_rate("*")
                if rate and rate.requests:
                    delay = max(delay, rate.seconds/rate.requests)
                self.delays[p.hostname] = delay
            else:
                raise SourceError(f"robots_unavailable_http_{status}", retry_seconds(headers.get("retry-after")))
            self.robots[origin] = parser
            return parser
        raise SourceError("robots_redirect_limit")

    def fetch(self, url: str) -> str:
        for _ in range(4):
            safe_url(url)
            if urlsplit(url).hostname not in self.source.allowed_hosts:
                raise SourceError("unapproved_redirect_or_host")
            robots = self._robots_for(url)
            if robots and not robots.can_fetch(USER_AGENT, url):
                raise SourceError("robots_disallowed")
            status, headers, body = self._raw(url)
            if status in {301, 302, 303, 307, 308}:
                if not headers.get("location"):
                    raise SourceError("redirect_without_location")
                url = urljoin(url, headers["location"])
                continue
            if status != 200:
                raise SourceError(f"source_http_{status}", retry_seconds(headers.get("retry-after")))
            content_type = headers.get("content-type", "").lower()
            if content_type and not any(x in content_type for x in ("json", "html", "text/plain")):
                raise SourceError("unsupported_content_type")
            return body
        raise SourceError("redirect_limit")


def plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(plain(v) for v in value)
    if isinstance(value, dict):
        return plain(value.get("name") or value.get("value") or value.get("description"))
    soup = BeautifulSoup(html.unescape(str(value)), "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def classify_title(title: str) -> tuple[list[str], list[str]]:
    from .matching import contains
    text = title.casefold()
    areas = []
    for label, words in {
        "Law": ["legal", "solicitor", "lawyer", "training contract", "vacation scheme"],
        "Consulting": ["consultant", "consulting"], "Technology": ["software", "developer", "cyber"],
        "Data & AI": ["data", "machine learning", "ai"], "Engineering": ["engineer", "engineering"],
        "Project management": ["project", "programme management", "program management"],
        "Banking & finance": ["banking", "finance", "investment"],
        "Accounting & audit": ["audit", "accounting", "tax"], "Public sector": ["public service", "police"],
        "Operations & supply chain": ["operations", "supply chain", "logistics", "procurement"],
        "Marketing & communications": ["marketing", "communications", "public relations", "content"],
        "Human resources": ["human resources", "people operations", "talent", "recruiter"],
        "Energy": ["energy", "power", "renewable"], "Healthcare": ["health", "clinical", "medical", "pharma"],
        "Research & academia": ["research", "phd", "scientist"], "Education": ["education", "teacher", "learning", "instructor"],
        "Sales": ["sales", "account executive", "business development", "customer success"],
        "Sustainability": ["sustainability", "climate", "environmental"], "Real estate": ["real estate", "property"],
    }.items():
        if any(contains(w, text) for w in words):
            areas.append(label)
    types = []
    for label, words in {
        "Training contract": ["training contract", "trainee solicitor"], "Vacation scheme": ["vacation scheme"],
        "Graduate programme": ["graduate"], "Internship": ["intern", "internship"],
        "Placement": ["placement", "industrial year"], "Research / PhD": ["phd", "postdoc"],
        "Apprenticeship": ["apprentice", "apprenticeship"], "Entry-level job": ["junior", "entry-level"],
    }.items():
        if any(contains(w, text) for w in words):
            types.append(label)
    junior_words = ["assistant", "coordinator", "analyst", "associate", "trainee", "new grad",
                    "early career", "school leaver", "officer", "representative"]
    senior_words = ["senior", "lead", "manager", "director", "principal", "head", "staff", "vp", "vice president"]
    if any(contains(w, text) for w in junior_words) and not any(contains(w, text) for w in senior_words):
        if "Entry-level job" not in types:
            types.append("Entry-level job")
    return areas, types


def source_accepts(source: Source, title: str, location: str) -> bool:
    """Apply the same bounded early-career and location filters to every ATS."""
    _, kinds = classify_title(title)
    return ((not source.job_types_filter or bool(set(kinds) & set(source.job_types_filter))) and
            (not source.location_keywords or any(word.casefold() in location.casefold()
                                                  for word in source.location_keywords)))


def milliseconds_iso(value) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def make_job(source: Source, **fields) -> Job:
    for key in ("title", "description", "requirements_text", "location", "employer"):
        if key in fields:
            fields[key] = plain(fields[key])[: {"title": 300,"description": 25000,"requirements_text": 16000,"location": 1000,"employer": 200}[key]]
    fields.setdefault("employer", source.employer)
    fields.setdefault("country", source.country)
    fields.setdefault("timezone", source.timezone)
    fields["source_id"] = source.id
    fields.setdefault("apply_url", fields["source_url"])
    allowed = set(source.allowed_hosts+source.link_hosts)
    for key in ("source_url", "apply_url"):
        if urlsplit(safe_url(fields[key])).hostname not in allowed:
            raise SourceError("unapproved_application_link")
    areas, kinds = classify_title(fields["title"])
    fields.setdefault("areas", areas)
    fields.setdefault("job_types", kinds)
    return Job(**fields)


def walk(value, depth=0):
    if depth > 10:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child, depth+1)
    elif isinstance(value, list):
        for child in value[:1000]:
            yield from walk(child, depth+1)


def location_text(value):
    if isinstance(value, list):
        return " / ".join(location_text(v) for v in value)
    if isinstance(value, dict):
        a = value.get("address", value)
        if isinstance(a, dict):
            return ", ".join(filter(None, [plain(a.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]))
    return plain(value)


def parse_jsonld(source: Source, url: str, body: str) -> list[Job]:
    soup = BeautifulSoup(body, "html.parser")
    jobs = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for item in walk(payload):
            types = item.get("@type", [])
            if "JobPosting" not in ([types] if isinstance(types, str) else types) or not item.get("title"):
                continue
            target = urljoin(url, item.get("url") or url)
            identifier = item.get("identifier")
            ext = identifier.get("value") if isinstance(identifier, dict) else identifier
            requirement_text = "\n".join(filter(None, [plain(item.get(k)) for k in
                                  ("qualifications", "educationRequirements", "experienceRequirements", "skills")]))
            locations = item.get("jobLocation", [])
            locations = locations if isinstance(locations, list) else [locations]
            location_countries = {country_code(plain(loc.get("address", {}).get("addressCountry", "")))
                                  for loc in locations if isinstance(loc, dict) and isinstance(loc.get("address", {}), dict)} - {""}
            restrictions = item.get("applicantLocationRequirements", [])
            restrictions = restrictions if isinstance(restrictions, list) else [restrictions]
            remote = [country_code(plain(r)) for r in restrictions]
            remote_known = bool(remote) and all(remote)
            geography_note = "Remote geographic restrictions need review" if restrictions and not remote_known else ""
            salary = item.get("baseSalary") or {}
            salary_value = salary.get("value", {}) if isinstance(salary, dict) else {}
            salary_fields = {}
            if isinstance(salary_value, dict) and salary_value.get("unitText", "").upper() == "YEAR":
                for target_field, source_field in (("annual_salary_min", "minValue"), ("annual_salary_max", "maxValue")):
                    value = salary_value.get(source_field, salary_value.get("value"))
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100000000:
                        salary_fields[target_field] = int(value)
                salary_fields["salary_currency"] = salary.get("currency", "")
            jobs.append(make_job(source, external_id=str(ext or canonical_url(target))[:200], title=item["title"],
                        employer=plain(item.get("hiringOrganization")) or source.employer,
                        location=location_text(item.get("jobLocation")), source_url=target,
                        country=next(iter(location_countries)) if len(location_countries)==1 else ("" if len(location_countries)>1 else source.country),
                        remote_countries=remote if remote_known else [], remote_restrictions_known=remote_known,
                        geography_note=geography_note,
                        description=item.get("description", ""), requirements_text=requirement_text,
                        posted=str(item.get("datePosted") or ""), closes=str(item.get("validThrough") or ""),
                        # datePosted is NOT application opening; non-standard opening fields are not assumed.
                        starts=str(item.get("jobStartDate") or ""),
                        work_mode="remote" if item.get("jobLocationType") == "TELECOMMUTE" else "unknown", **salary_fields))
    return jobs


def requirements_excerpt(content):
    soup=BeautifulSoup(html.unescape(content or ''),'html.parser')
    for heading in soup.find_all(['h2','h3','h4','strong','b']):
        title=heading.get_text(' ',strip=True).casefold()
        if len(title)<150 and any(term in title for term in ['requirements','what we are looking for','what you will bring',"what you'll bring",'you should apply if','about you','your experience']):
            container=heading.parent if heading.parent.name=='p' else heading
            chunks=[]
            for node in container.next_siblings:
                if getattr(node,'name','') in {'h1','h2','h3','h4'}:
                    break
                text=plain(str(node))
                if text:chunks.append(text)
            if chunks:return (' '.join(chunks))[:1600]+' — See the official advert for the full requirements.'
    text=plain(content)
    return ('Advert excerpt: '+text[:1200]+' — See the official advert for the full requirements.') if text else 'Read the official description for full eligibility requirements.'


def ats_geography(source,location):
    from .countries import COUNTRY_CODES
    normalized=location.casefold().replace('-',' ')
    remote='remote' in normalized or 'home based' in normalized
    mapped=next((code for label,code in source.location_country_map.items() if label.casefold()==location.casefold()),'')
    codes={country_code(part.strip()) for part in re.split(r'[,;()/|]|\bor\b',location,flags=re.I)}-{''}
    if mapped:codes.add(mapped)
    # Country abbreviations are accepted only as separate explicit location tokens.
    if re.search(r'\b(?:UK|United Kingdom)\b',location,re.I):codes.add('GB')
    worldwide=remote and 'worldwide' in normalized
    country=next(iter(codes)) if len(codes)==1 else ('' if len(codes)>1 else source.country)
    note=''
    if remote and not codes and not worldwide:
        note='Source remote region: '+location+'. Confirm that your country is eligible; region labels are not country-level permission.'
    return {'country':country,'work_mode':'remote' if remote else 'unknown',
            'remote_countries':sorted(COUNTRY_CODES) if worldwide else (sorted(codes) if remote else []),
            'remote_restrictions_known':remote and bool(codes or worldwide),'geography_note':note}


def collect(source: Source, reader: NetworkReader) -> Collection:
    if source.kind in {"newton", "pwc"}:
        from .employer_sources import collect_programme
        return collect_programme(source, reader)
    if source.kind == "greenhouse":
        data = json.loads(reader.fetch(source.url))
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("greenhouse_schema_changed")
        entries = data["jobs"]
        total = data.get("meta", {}).get("total", len(entries))
        selected=[j for j in entries if source_accepts(source, j.get('title', ''),
                                                       j.get('location', {}).get('name', ''))]
        limit=min(source.max_jobs,source.max_pages) if source.fetch_details else source.max_jobs
        jobs=[]
        for entry in selected[:limit]:
            j=entry
            if source.fetch_details:
                parsed=urlsplit(source.url)
                detail_url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path.rstrip('/')+'/'+quote(str(entry['id']),safe=''),'',''))
                j=json.loads(reader.fetch(detail_url))
                if not isinstance(j,dict) or str(j.get('id'))!=str(entry['id']):
                    raise SourceError('greenhouse_detail_schema_changed')
            location=j.get('location',{}).get('name','')
            jobs.append(make_job(source,external_id=str(j['id']),title=j['title'],
                         location=location,source_url=j['absolute_url'],description=j.get('content',''),
                         requirements_text=requirements_excerpt(j.get('content','')),
                         posted=j.get('first_published') or '',
                         closes=j.get('application_deadline') if isinstance(j.get('application_deadline'),str) else '',
                         **ats_geography(source,location)))
        return Collection(jobs,len(selected)<=limit and total<=len(entries))
    if source.kind == "lever":
        jobs, seen = [], set()
        for page in range(source.max_pages):
            p = urlsplit(source.url)
            query = {k: v for k, v in parse_qsl(p.query) if k not in {"skip", "limit", "mode"}}
            query.update(skip=str(page*100), limit="100", mode="json")
            url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))
            data = json.loads(reader.fetch(url))
            if not isinstance(data, list):
                raise SourceError("lever_schema_changed")
            for j in data:
                if j["id"] in seen:
                    raise SourceError("lever_pagination_did_not_advance")
                seen.add(j["id"])
                location=j.get("categories", {}).get("location", "")
                if not source_accepts(source, j.get("text", ""), location):
                    continue
                if len(jobs) >= source.max_jobs:
                    return Collection(jobs, False)
                jobs.append(make_job(source, external_id=str(j["id"]), title=j["text"],
                            source_url=j["hostedUrl"], apply_url=j.get("applyUrl") or j["hostedUrl"],
                            location=location,
                            description=j.get("descriptionPlain") or j.get("description", ""),
                            requirements_text="\n".join(plain(x.get("text", ""))+": "+plain(x.get("content", "")) for x in j.get("lists", [])),
                            posted=milliseconds_iso(j.get("createdAt")), **ats_geography(source, location)))
            if len(data) < 100:
                return Collection(jobs)
        return Collection(jobs, False)
    if source.kind == "ashby":
        data = json.loads(reader.fetch(source.url))
        if not isinstance(data, dict) or data.get("apiVersion") != "1" or not isinstance(data.get("jobs"), list):
            raise SourceError("ashby_schema_changed")
        jobs=[]
        for j in data["jobs"]:
            if not isinstance(j, dict) or not j.get("isListed", True):
                continue
            title, location = j.get("title", ""), j.get("location", "")
            if not title or not source_accepts(source, title, location):
                continue
            if len(jobs) >= source.max_jobs:
                return Collection(jobs, False)
            address = j.get("address", {}).get("postalAddress", {}) if isinstance(j.get("address"), dict) else {}
            country = country_code(plain(address.get("addressCountry", "")))
            workplace = str(j.get("workplaceType") or "").casefold()
            work_mode = "remote" if j.get("isRemote") or "remote" in workplace else ("hybrid" if "hybrid" in workplace else "onsite" if workplace else "unknown")
            compensation = j.get("compensation") if isinstance(j.get("compensation"), dict) else {}
            salary = {}
            for part in compensation.get("summaryComponents", []) if isinstance(compensation, dict) else []:
                if part.get("compensationType") == "Salary" and part.get("interval") == "1 YEAR":
                    if isinstance(part.get("minValue"), (int, float)):
                        salary["annual_salary_min"] = int(part["minValue"])
                    if isinstance(part.get("maxValue"), (int, float)):
                        salary["annual_salary_max"] = int(part["maxValue"])
                    if part.get("currencyCode"):
                        salary["salary_currency"] = str(part["currencyCode"]).upper()
                    break
            geo = ats_geography(source, location)
            geo.update(country=country or geo["country"], work_mode=work_mode)
            if work_mode == "remote" and country:
                geo.update(remote_countries=[country], remote_restrictions_known=True)
            content=j.get("descriptionHtml") or j.get("descriptionPlain") or ""
            jobs.append(make_job(source, external_id=str(j.get("id") or canonical_url(j["jobUrl"])),
                        title=title, source_url=j["jobUrl"], apply_url=j.get("applyUrl") or j["jobUrl"],
                        location=location, description=content, requirements_text=requirements_excerpt(content),
                        posted=str(j.get("publishedAt") or ""), **geo, **salary))
        return Collection(jobs)
    if source.kind == "programme":
        pages = {}
        unique_urls = list(dict.fromkeys(record.source_url for record in source.programmes))
        complete = len(unique_urls) <= source.max_pages
        for url in unique_urls[:source.max_pages]:
            body = reader.fetch(url)
            text = plain(body).casefold()
            if len(text) < 100 or not any(term in text for term in
                    ("vacation scheme", "training contract", "trainee", "internship", "placement",
                     "work experience", "early career", "graduate")):
                raise SourceError("programme_page_content_changed")
            pages[url] = body
        jobs=[]
        for record in source.programmes:
            if record.source_url not in pages:
                continue
            checks = ["The official page was reachable; JobSignal did not test application submission."]
            if record.date_evidence:
                checks.append("Date evidence in the supplied register: " + record.date_evidence + ".")
            if record.notes:
                checks.append(record.notes)
            description = "; ".join(filter(None, [
                "Intake / scheme year: " + record.intake if record.intake else "",
                "Application route: " + record.application_route if record.application_route else "",
                "Imported from the supplied UK law 2026–27 register and checked against this official employer page."
            ]))
            requirements = requirements_excerpt(pages[record.source_url])
            jobs.append(make_job(source, external_id=record.id, title=record.title, location=record.location,
                        country=source.country or "GB", source_url=record.source_url,
                        apply_url=record.apply_url or record.source_url, opens=record.opens, closes=record.closes,
                        starts=record.starts, areas=["Law"], job_types=[record.job_type],
                        requirements_text=requirements, description=description,
                        application_checks=checks, rolling="rolling" in (record.notes + " " + record.date_evidence).casefold()))
        if not jobs:
            raise SourceError("programme_page_no_records")
        return Collection(jobs[:source.max_jobs], complete and len(jobs) <= source.max_jobs)
    if source.kind == "jsonld":
        jobs = parse_jsonld(source, source.url, reader.fetch(source.url))
        if not jobs:
            raise SourceError("no_jobposting_extracted_not_proof_of_closure")
        return Collection(jobs[:source.max_jobs], len(jobs) <= source.max_jobs)
    if source.kind == "index":
        soup = BeautifulSoup(reader.fetch(source.url), "html.parser")
        links = []
        for a in soup.select(source.link_selector):
            if a.get("href"):
                target = canonical_url(urljoin(source.url, a["href"]))
                if urlsplit(target).hostname in source.allowed_hosts and target not in links:
                    links.append(target)
        if not links:
            raise SourceError("no_listing_links_extracted_review_selector")
        jobs = []
        for link in links[:source.max_pages]:
            found = parse_jsonld(source, link, reader.fetch(link))
            if not found:
                raise SourceError("detail_page_extraction_failed")
            jobs.extend(found)
        return Collection(jobs[:source.max_jobs], len(links) <= source.max_pages and len(jobs) <= source.max_jobs)
    raise SourceError("fixture_must_use_demo_seed")
