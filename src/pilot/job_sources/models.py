from __future__ import annotations

import ipaddress
import re
from datetime import date
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .countries import COUNTRY_CODES

AREAS = ["Law", "Consulting", "Banking & finance", "Accounting & audit", "Technology",
         "Data & AI", "Engineering", "Project management", "Operations & supply chain",
         "Marketing & communications", "Human resources", "Public sector", "Energy",
         "Healthcare", "Research & academia", "Education", "Sales", "Sustainability",
         "Real estate", "Other"]
JOB_TYPES = ["Graduate programme", "Internship", "Placement", "Vacation scheme",
             "Training contract", "Entry-level job", "Research / PhD", "Apprenticeship"]


def safe_url(value: str) -> str:
    """Syntax-only HTTPS validation. Fetching additionally pins a public DNS address."""
    if not value or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("Invalid HTTPS URL")
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("A credential-free HTTPS URL is required")
    host = p.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Private host is not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private IP is not allowed")
    return value


def canonical_url(value: str) -> str:
    p = urlsplit(safe_url(value))
    pairs = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"})
    return urlunsplit(("https", p.netloc.lower(), p.path or "/", urlencode(pairs), ""))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)


class Education(Model):
    level: Literal["undergraduate", "postgraduate", "phd", "other"] = "undergraduate"
    subject: str = Field(default="", max_length=200)
    institution: str = Field(default="", max_length=200)
    status: Literal["current", "completed", "planned"] = "current"
    study_year: int | None = Field(default=None, ge=1, le=12)
    course_years: int | None = Field(default=None, ge=1, le=12)
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    grade: Literal["first", "2:1", "2:2", "third", "pass", "other", "unknown"] = "unknown"
    grade_is_predicted: bool = True

    @model_validator(mode="after")
    def valid_study_year(self):
        if self.study_year and self.course_years and self.study_year > self.course_years:
            raise ValueError("Current study year cannot exceed total course years")
        return self


class WorkRight(Model):
    country: str = Field(default="GB", pattern=r"^[A-Z]{2}$")
    status: Literal["unrestricted", "time_limited", "needs_sponsorship", "unknown"] = "unknown"
    expires_on: date | None = None
    sponsorship_later: bool | None = None
    continuous_residence_years: float | None = Field(default=None, ge=0, le=100)


class Profile(Model):
    display_name: str = Field(default="", max_length=120)
    education: list[Education] = Field(default_factory=list, max_length=8)
    work_rights: list[WorkRight] = Field(default_factory=list, max_length=10)
    experience_months: int | None = Field(default=None, ge=0, le=720)
    available_from: date | None = None
    skills: list[str] = Field(default_factory=list, max_length=50)
    languages: list[str] = Field(default_factory=list, max_length=20)
    driving_licence_countries: list[str] = Field(default_factory=list, max_length=10)
    portfolio_links: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("portfolio_links")
    @classmethod
    def check_links(cls, values):
        return [safe_url(v) for v in values]

    @model_validator(mode="after")
    def check_lists(self):
        if any(len(x) > 100 for x in self.skills + self.languages):
            raise ValueError("Skill/language names must be at most 100 characters")
        countries = [w.country for w in self.work_rights]
        if len(set(countries)) != len(countries):
            raise ValueError("Use one work-right record per country")
        return self


class Search(Model):
    name: str = Field(default="My opportunities", min_length=1, max_length=100)
    areas: list[str] = Field(default_factory=list, max_length=len(AREAS))
    job_types: list[str] = Field(default_factory=list, max_length=len(JOB_TYPES))
    countries: list[str] = Field(default_factory=lambda: ["GB"], max_length=249)
    locations: list[str] = Field(default_factory=list, max_length=30)
    work_modes: list[Literal["onsite", "hybrid", "remote"]] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    excluded_employers: list[str] = Field(default_factory=list, max_length=50)
    max_required_experience_months: int | None = Field(default=24, ge=0, le=720)
    min_annual_salary: int | None = Field(default=None, ge=0, le=1000000)
    salary_currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")
    include_unknown: bool = True
    include_upcoming: bool = True
    exclude_applied: bool = True
    include_in_alerts: bool = True
    candidate: Profile | None = None  # Matching answers belong to this saved search.

    @model_validator(mode="after")
    def valid_options(self):
        if set(self.areas) - set(AREAS) or set(self.job_types) - set(JOB_TYPES):
            raise ValueError("Unknown area or job type")
        if set(self.countries) - COUNTRY_CODES:
            raise ValueError("Choose a country from the country list")
        if any(len(x) > 150 for x in self.locations + self.keywords + self.excluded_employers):
            raise ValueError("Preference text too long")
        return self


class AlertSettings(Model):
    enabled: bool = False
    consent: bool = False
    cadence: Literal["immediate", "hourly", "daily"] = "immediate"
    timezone: str = "Europe/London"
    daily_hour: int = Field(default=8, ge=0, le=23)
    daily_minute: int = Field(default=0, ge=0, le=59)
    quiet_enabled: bool = False
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=7, ge=0, le=23)
    urgent_overrides_quiet: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, v):
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError("Unknown IANA timezone") from e
        return v

    @model_validator(mode="after")
    def valid_consent(self):
        if self.enabled and not self.consent:
            raise ValueError("Explicit consent is required before enabling alerts")
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValueError("Quiet hours must have different start and end hours")
        return self


class Evidence(Model):
    field: str = Field(max_length=100)
    excerpt: str = Field(min_length=1, max_length=2000)
    url: str
    reviewed: bool = False

    @field_validator("url")
    @classmethod
    def valid_url(cls, v):
        return safe_url(v)


class Requirements(Model):
    sponsorship: Literal["yes", "no", "unknown"] = "unknown"
    unrestricted_right_required: bool | None = None
    degree_level: Literal["undergraduate", "postgraduate", "phd"] | None = None
    subjects: list[str] = Field(default_factory=list)
    any_subject: bool | None = None
    minimum_grade: Literal["first", "2:1", "2:2", "third", "pass"] | None = None
    graduation_year_from: int | None = None
    graduation_year_to: int | None = None
    minimum_experience_months: int | None = Field(default=None, ge=0)
    driving_licence_required: bool | None = None
    residence_years: float | None = Field(default=None, ge=0)
    study_stages: list[Literal["first", "penultimate", "final", "graduate"]] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list, max_length=50)
    required_languages: list[str] = Field(default_factory=list, max_length=20)


class Job(Model):
    source_id: str
    external_id: str = Field(min_length=1, max_length=200)
    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(default="", max_length=1000)
    country: str = Field(default="", max_length=2)
    remote_countries: list[str] = Field(default_factory=list, max_length=249)
    remote_restrictions_known: bool = False
    geography_note: str = Field(default="", max_length=1000)
    areas: list[str] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"] = "unknown"
    description: str = Field(default="", max_length=25000)
    requirements_text: str = Field(default="", max_length=16000)
    application_checks: list[str] = Field(default_factory=list, max_length=20)
    requirements: Requirements = Field(default_factory=Requirements)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    source_url: str
    apply_url: str
    opens: str = Field(default="", max_length=200)
    closes: str = Field(default="", max_length=200)
    starts: str = Field(default="", max_length=200)
    posted: str = Field(default="", max_length=200)
    timezone: str = "Europe/London"
    annual_salary_min: int | None = Field(default=None, ge=0)
    annual_salary_max: int | None = Field(default=None, ge=0)
    salary_currency: str = "GBP"
    portal_state: Literal["unverified", "accepting", "closed"] = "unverified"
    conflict: str = Field(default="", max_length=2000)
    rolling: bool = False
    demo: bool = False

    @field_validator("source_url", "apply_url")
    @classmethod
    def valid_url(cls, v):
        return safe_url(v)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        ZoneInfo(v)
        return v

    @field_validator("remote_countries")
    @classmethod
    def valid_remote_countries(cls, values):
        if set(values) - COUNTRY_CODES:
            raise ValueError("Unknown remote country")
        return sorted(set(values))

    def reviewed(self, field: str) -> bool:
        return any(e.field == field and e.reviewed for e in self.evidence)


class ProgrammeRecord(Model):
    """Operator-reviewed programme facts attached to an official employer page."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,119}$")
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(default="", max_length=1000)
    opens: str = Field(default="", max_length=200)
    closes: str = Field(default="", max_length=200)
    starts: str = Field(default="", max_length=200)
    intake: str = Field(default="", max_length=200)
    application_route: str = Field(default="", max_length=300)
    date_evidence: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=2000)
    source_url: str
    apply_url: str = ""
    job_type: Literal["Vacation scheme", "Training contract", "Internship", "Placement"]

    @field_validator("source_url")
    @classmethod
    def valid_source_url(cls, value):
        return safe_url(value)

    @field_validator("apply_url")
    @classmethod
    def valid_apply_url(cls, value):
        return safe_url(value) if value else value


class Source(Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    employer: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    kind: Literal["greenhouse", "lever", "ashby", "programme", "jsonld", "index", "fixture", "newton", "pwc"]
    url: str = ""
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    link_hosts: list[str] = Field(default_factory=list, max_length=20)
    country: str = ""
    timezone: str = "Europe/London"
    poll_seconds: int = Field(default=900, ge=60, le=86400)
    fresh_seconds: int = Field(default=2700, ge=60, le=604800)
    min_request_interval: float = Field(default=1, ge=0.5, le=60)
    max_pages: int = Field(default=20, ge=1, le=50)
    max_jobs: int = Field(default=2000, ge=1, le=10000)
    job_types_filter: list[str] = Field(default_factory=list, max_length=len(JOB_TYPES))
    location_keywords: list[str] = Field(default_factory=list, max_length=30)
    location_country_map: dict[str,str] = Field(default_factory=dict)
    fetch_details: bool = False
    link_selector: str = Field(default="a.job-link", max_length=200)
    group: Literal["free_ats", "law_register", "supplemental", "demo"] = "supplemental"
    coverage_areas: list[str] = Field(default_factory=list, max_length=len(AREAS))
    programmes: list[ProgrammeRecord] = Field(default_factory=list, max_length=50)
    reviewed_by: str = Field(default="", max_length=150)
    terms_checked: bool = False
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_source(self):
        if set(self.job_types_filter)-set(JOB_TYPES):
            raise ValueError('Unknown source opportunity filter')
        if set(self.coverage_areas)-set(AREAS):
            raise ValueError('Unknown source coverage area')
        if len(self.location_country_map)>100 or any(len(k)>1000 or v not in COUNTRY_CODES for k,v in self.location_country_map.items()):
            raise ValueError('Invalid reviewed location/country mapping')
        if any(len(k)>150 for k in self.location_keywords):
            raise ValueError('Location filters are too long')
        ZoneInfo(self.timezone)
        if self.kind != "fixture":
            safe_url(self.url)
            if urlsplit(self.url).hostname not in self.allowed_hosts:
                raise ValueError("Source URL must be on an exact allowed host")
            if self.enabled and (not self.reviewed_by or not self.terms_checked):
                raise ValueError("Enabled sources require operator review and terms check")
        for host in self.allowed_hosts + self.link_hosts:
            if host != host.lower() or "/" in host or "*" in host or ":" in host:
                raise ValueError("Use exact lower-case hostnames without wildcards")
        if self.kind == "programme" and not self.programmes:
            raise ValueError("Programme sources require at least one reviewed record")
        if self.kind != "programme" and self.programmes:
            raise ValueError("Programme records are only valid for programme sources")
        approved = set(self.allowed_hosts + self.link_hosts)
        for record in self.programmes:
            for value in (record.source_url, record.apply_url):
                if value and urlsplit(value).hostname not in approved:
                    raise ValueError("Programme links must use an exact approved host")
        return self
