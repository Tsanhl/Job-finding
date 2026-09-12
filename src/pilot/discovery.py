"""Bounded public vacancy discovery with persisted, normalized local results."""

from __future__ import annotations

import asyncio
import datetime
import html
import ipaddress
import json
import re
import socket
import time
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .store import digest, encode, uid

BRIGHT_NETWORK_ROOT = "https://www.brightnetwork.co.uk/graduate-jobs/"
PROVIDER_HOSTS = {
    "bright_network": ("brightnetwork.co.uk",),
    "workday": ("myworkdayjobs.com", "workday.com"),
    "allhires": ("allhires.com",),
    "apply4law": ("apply4law.com",),
}
GENERIC_QUERY_WORDS = {
    "career",
    "careers",
    "graduate",
    "graduates",
    "intern",
    "internship",
    "job",
    "jobs",
    "programme",
    "program",
    "role",
    "scheme",
    "trainee",
}


def provider_for(url):
    host = (urlsplit(str(url)).hostname or "").casefold()
    for provider, suffixes in PROVIDER_HOSTS.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return provider
    return ""


def validate_source(url, *, testing=False):
    parsed = urlsplit(str(url).strip())
    allowed = {"http", "https"} if testing else {"https"}
    if (
        parsed.scheme not in allowed
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Source must use a public HTTPS URL")
    return str(url).strip()


async def fetch_public(url, *, testing=False, transport=None):
    for _ in range(4):
        url = validate_source(url, testing=testing)
        parsed = urlsplit(url)
        if not testing:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                parsed.hostname,
                parsed.port or 443,
                type=socket.SOCK_STREAM,
            )
            if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
                raise ValueError("Non-public source rejected")
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            headers={"Accept": "application/json,text/html"},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect omitted its destination")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError("Source exceeds 2 MiB limit")
                    chunks.append(chunk)
                return b"".join(chunks).decode(errors="replace")
    raise ValueError("Too many redirects")


class Discovery:
    def __init__(self, store, *, testing=False, transport=None, browser=None):
        self.store = store
        self.testing = testing
        self.transport = transport
        self.browser = browser
        from .resources import Cache

        self.cache = Cache(store)

    async def discover(self, plan):
        """Compatibility entry point for the original plan-shaped API."""

        plan.require("discover")
        if not plan.discovery_budget:
            raise ValueError("Explicit discovery budget required")
        sources = [
            validate_source(source, testing=self.testing)
            for source in plan.research_sources[:10]
        ]
        rows = await self._sources(sources, plan.discovery_budget)
        self._persist("", "", plan.discovery_budget, sources, rows)
        return rows[: plan.discovery_budget]

    async def find(self, request):
        query = str(request.get("query") or "").strip()
        location = str(request.get("location") or "").strip()
        requested = request.get("requested", 10)
        if not query or len(query) > 300:
            raise ValueError("Enter role keywords of 1–300 characters")
        if len(location) > 200:
            raise ValueError("Location must be at most 200 characters")
        if type(requested) is not int or not 1 <= requested <= 100:
            raise ValueError("Requested job count must be an integer from 1 to 100")
        supplied = request.get("sources", [])
        if not isinstance(supplied, list) or len(supplied) > 10:
            raise ValueError("Supply at most ten public source URLs")
        sources = [validate_source(value, testing=self.testing) for value in supplied]
        if request.get("include_builtin", True):
            registered = [
                row["url"]
                for row in self.store.rows(
                    "SELECT url FROM source_roots WHERE enabled=1 ORDER BY created"
                )
            ]
            sources = [BRIGHT_NETWORK_ROOT, *registered, *sources]
        sources = list(dict.fromkeys(sources))[:10]
        if not sources:
            raise ValueError("Enable a built-in source or supply a public source URL")
        budget = min(100, max(requested * 5, 10))
        rows = await self._sources(sources, budget)
        words = _query_words(query)
        wanted_location = location.casefold()
        selected = []
        for row in rows:
            searchable = " ".join(
                str(row.get(key) or "")
                for key in ("role", "employer", "requirements", "employment_type")
            ).casefold()
            row_location = str(row.get("location") or "").casefold()
            if words and not any(_word_present(word, searchable) for word in words):
                continue
            if wanted_location and wanted_location not in row_location:
                continue
            selected.append(row)
            if len(selected) >= requested:
                break
        run_id = self._persist(query, location, requested, sources, selected)
        return {
            "run_id": run_id,
            "area": query,
            "requested": requested,
            "returned": len(selected),
            "jobs": selected,
        }

    def register_source(self, url, name=""):
        url = validate_source(url, testing=self.testing)
        provider = provider_for(url)
        if provider not in {"workday", "allhires", "apply4law"}:
            raise ValueError(
                "Register a public Workday, AllHires or Apply4Law career-site root"
            )
        source_id = digest([provider, url])
        self.store.db.execute(
            "INSERT INTO source_roots VALUES(?,?,?,?,1,?) ON CONFLICT(url) DO UPDATE SET name=excluded.name,enabled=1",
            (source_id, provider, str(name or provider), url, time.time()),
        )
        return {
            "id": source_id,
            "provider": provider,
            "name": str(name or provider),
            "url": url,
        }

    async def _read(self, source):
        async def producer():
            try:
                return await fetch_public(
                    source, testing=self.testing, transport=self.transport
                )
            except (httpx.HTTPError, ValueError):
                if not self.browser or not provider_for(source):
                    raise
                context = await self.browser.new_context(accept_downloads=False)
                try:
                    page = await context.new_page()
                    await page.goto(
                        source, wait_until="domcontentloaded", timeout=30000
                    )
                    validate_source(page.url, testing=self.testing)
                    if provider_for(page.url) != provider_for(source):
                        raise ValueError("Source redirected outside its provider")
                    await page.wait_for_timeout(750)
                    content = await page.content()
                    if len(content.encode()) > 2 * 1024 * 1024:
                        raise ValueError("Source exceeds 2 MiB limit")
                    return content
                finally:
                    await context.close()

        return await self.cache.derive(
            digest([source, "public-source-v2"]), "research", producer, ttl=300
        )

    async def _sources(self, sources, budget):
        rows = []
        row_indexes = {}
        remaining_detail_pages = min(100, max(10, budget * 2))
        for source in sources:
            try:
                text = await self._read(source)
            except Exception:
                # Each public source is independent; one unavailable adapter must
                # not prevent the remaining bounded sources from being checked.
                continue
            for entry in _entries(text):
                _add_row(rows, row_indexes, _normalize(entry, source))
            detail_urls = _detail_links(text, source)[:remaining_detail_pages]
            for detail_url in detail_urls:
                remaining_detail_pages -= 1
                try:
                    detail_text = await self._read(detail_url)
                except Exception:
                    continue
                for entry in _entries(detail_text):
                    _add_row(rows, row_indexes, _normalize(entry, detail_url))
                if remaining_detail_pages <= 0:
                    break
            if remaining_detail_pages <= 0 or len(rows) >= budget:
                break
        for row in rows:
            row["summary"] = format_job(row)
        return rows[:budget]

    def _persist(self, query, location, requested, sources, rows):
        run_id = uid()
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO discovery_runs VALUES(?,?,?,?,?,?)",
                (run_id, query, location, requested, encode(sources), time.time()),
            )
            for index, row in enumerate(rows):
                self.store.db.execute(
                    "INSERT INTO discovery_results VALUES(?,?,?,?)",
                    (run_id, index, row["identity"], encode(row)),
                )
        return run_id


def _entries(text):
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = []
        for script in re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            text,
            re.DOTALL | re.IGNORECASE,
        ):
            try:
                raw.append(json.loads(script))
            except json.JSONDecodeError:
                continue
    pending = raw.get("jobs", [raw]) if isinstance(raw, dict) else raw
    pending = list(pending) if isinstance(pending, list) else []
    jobs = []
    while pending:
        entry = pending.pop(0)
        if not isinstance(entry, dict):
            continue
        graph = entry.get("@graph")
        if isinstance(graph, list):
            pending.extend(graph)
        kind = entry.get("@type", "JobPosting")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds or ("role" in entry and "employer" in entry):
            jobs.append(entry)
    return jobs


def _detail_links(text, source):
    provider = provider_for(source)
    links = []
    for href in re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE
    ):
        url = urljoin(source, html.unescape(href))
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != urlsplit(source).hostname
        ):
            continue
        path = parsed.path.casefold()
        relevant = (
            provider == "bright_network"
            and path.startswith("/graduate-jobs/")
            and path.count("/") >= 3
        ) or (provider == "workday" and "/job/" in path) or (
            provider in {"allhires", "apply4law"}
            and any(part in path for part in ("job", "vacan", "opportun", "apply"))
        ) or (
            not provider
            and any(
                part in path
                for part in ("job", "vacan", "career", "position", "opening", "role")
            )
        )
        if relevant:
            links.append(url)
    return list(dict.fromkeys(links))


def _normalize(entry, source):
    role = _text(entry.get("title") or entry.get("role"))[:300]
    organisation = entry.get("hiringOrganization") or entry.get("employer")
    employer = _text(organisation)[:300]
    url = str(entry.get("url") or entry.get("portal") or "").strip()
    if not role or not employer or not url:
        return None
    identifier = entry.get("identifier") or ""
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or identifier.get("name") or ""
    identity = str(entry.get("identity") or identifier).strip()
    if not identity:
        identity = "reviewed-url-" + digest([employer, role, url])[:20]
    salary = _text(entry.get("baseSalary") or entry.get("pay"))[:500]
    return {
        "url": urljoin(source, url),
        "employer": employer,
        "role": role,
        "identity": identity,
        "eligibility": "unknown",
        "pay": salary or "Unknown",
        "deadline": _text(
            entry.get("validThrough")
            or entry.get("closingDate")
            or entry.get("deadline")
        )
        or "Unknown",
        "opening": _text(entry.get("openingDate"))
        or "Unknown",
        "posted": _text(entry.get("datePosted")) or "Unknown",
        "requirements": _requirements(entry)[:4000] or "Not stated in source",
        "location": _location(entry)[:500] or "Unknown",
        "employment_type": _text(
            entry.get("employmentType") or entry.get("employment_type")
        )
        or "Unknown",
        "status_note": _text(
            entry.get("status_note") or entry.get("actionRequired")
        ),
        "portal_check": "CHECK PORTAL",
        "source": source,
        "provider": provider_for(source) or "supplied",
        "checked_at": time.time(),
    }


def _text(value):
    if isinstance(value, str):
        return " ".join(re.sub(r"<[^>]+>", " ", value).split())
    if isinstance(value, list):
        return "; ".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        if value.get("value") is not None:
            unit = _text(value.get("unitText") or value.get("unitCode"))
            return " ".join(filter(None, (_text(value.get("value")), unit)))
        return _text(value.get("name") or value.get("text") or "")
    if value is None:
        return ""
    return str(value)


def _requirements(entry):
    values = [
        entry.get("requirements"),
        entry.get("qualifications"),
        entry.get("educationRequirements"),
        entry.get("experienceRequirements"),
        entry.get("skills"),
    ]
    explicit = "; ".join(filter(None, (_text(value) for value in values)))
    if explicit:
        return explicit
    description = _text(entry.get("description"))
    if not description:
        return ""
    marker = re.search(
        r"(?i)\b(requirements?|qualifications?|what (?:you(?:'|’)ll|we) need|skills?|experience)\b",
        description,
    )
    excerpt = description[marker.start() :] if marker else description
    end = re.search(
        r"(?i)\b(benefits?|what we offer|about (?:us|the company)|how to apply)\b",
        excerpt[80:],
    )
    if end:
        excerpt = excerpt[: 80 + end.start()]
    return excerpt[:2000].strip()


def _query_words(query):
    words = [
        word.casefold()
        for word in re.findall(r"[\w+#.-]+", query, re.UNICODE)
        if len(word) > 1
    ]
    specific = [word for word in words if word not in GENERIC_QUERY_WORDS]
    return specific or words


def _word_present(word, searchable):
    if any(character in word for character in "+#.-"):
        return word in searchable
    return bool(re.search(rf"\b{re.escape(word)}\w*\b", searchable))


def _unknown(value):
    return not value or str(value).casefold() in {
        "unknown",
        "unspecified",
        "not stated in source",
    }


def _add_row(rows, indexes, row):
    if not row:
        return
    keys = [
        ("identity", row["employer"].casefold(), row["identity"].casefold()),
        ("url", row["url"].casefold()),
    ]
    existing_index = next((indexes[key] for key in keys if key in indexes), None)
    if existing_index is None:
        existing_index = len(rows)
        rows.append(row)
    else:
        current = rows[existing_index]
        for key, value in row.items():
            if _unknown(current.get(key)) and not _unknown(value):
                current[key] = value
            elif key == "requirements" and len(str(value)) > len(
                str(current.get(key) or "")
            ):
                current[key] = value
        current["checked_at"] = max(current["checked_at"], row["checked_at"])
    for key in keys:
        indexes[key] = existing_index


def _display_date(value):
    if _unknown(value):
        return ""
    raw = str(value).strip()
    try:
        parsed = datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return raw
    return f"{parsed.day} {parsed:%b %Y}"


def _markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", str(value))


def format_job(row, *, markdown=False):
    """Render one public result without claiming its application status is known."""

    opening = _display_date(row.get("opening"))
    deadline = _display_date(row.get("deadline"))
    timing = str(row.get("status_note") or "").strip()
    if not timing:
        if opening and deadline:
            timing = f"Opened {opening}; deadline {deadline}"
        elif deadline:
            timing = f"Deadline {deadline}"
        elif opening:
            timing = f"Opened {opening}; closing date unverified"
        else:
            timing = "Opening and closing times unverified"
    url = str(row.get("url") or "")
    portal = url
    if markdown:
        escaped = quote(url, safe="/:?&=%#;+,@~")
        portal = f"[{_markdown_text(url)}]({escaped})"
    display = _markdown_text if markdown else str
    values = [
        display(row.get("employer") or "Unknown employer"),
        display(row.get("role") or "Unknown role"),
        display(timing),
        "CHECK PORTAL",
        "Portal: " + portal,
        "Location: " + display(row.get("location") or "Unknown"),
        "Type: " + display(row.get("employment_type") or "Unknown"),
        "Pay: " + display(row.get("pay") or "Unknown"),
        "Requirements: "
        + display(str(row.get("requirements") or "Not stated in source")[:2000]),
    ]
    return " — ".join(values)


def _location(entry):
    locations = entry.get("jobLocation") or entry.get("applicantLocationRequirements")
    if not isinstance(locations, list):
        locations = [locations]
    values = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address", location)
        if isinstance(address, dict):
            values.append(
                ", ".join(
                    filter(
                        None,
                        (
                            _text(address.get("addressLocality")),
                            _text(address.get("addressRegion")),
                            _text(address.get("addressCountry")),
                        ),
                    )
                )
            )
    return "; ".join(filter(None, values))
