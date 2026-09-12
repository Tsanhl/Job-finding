"""Extract, classify and resolve application fields before writing to a portal."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable

from .application_models import FieldEvidence


CONTROL_SELECTOR = (
    "input, textarea, select, [contenteditable='true'], [role='combobox'], "
    "[role='textbox'], [role='checkbox'], [role='radio']"
)


class FieldKind(str, Enum):
    FIRST_NAME = "first_name"
    MIDDLE_NAME = "middle_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    EMPLOYER_NAME = "employer_name"
    REFEREE = "referee"
    RECORD_FIELD = "record_field"
    LINKEDIN = "linkedin"
    PORTFOLIO = "portfolio"
    ELIGIBILITY = "eligibility"
    SPONSORSHIP = "sponsorship"
    COVER_LETTER = "cover_letter"
    MOTIVATION = "motivation"
    COMPETENCY = "competency"
    RESPONSIBILITIES = "responsibilities"
    ADDITIONAL_INFORMATION = "additional_information"
    NARRATIVE = "narrative"
    DEMOGRAPHIC = "demographic"
    DECLARATION = "declaration"
    CONSENT = "consent"
    SIGNATURE = "signature"
    CV_UPLOAD = "cv_upload"
    COVER_LETTER_UPLOAD = "cover_letter_upload"
    TRANSCRIPT_UPLOAD = "transcript_upload"
    OTHER_UPLOAD = "other_upload"
    CUSTOM_DROPDOWN = "custom_dropdown"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ManifestField:
    index: int
    field_id: str
    section: str
    question: str
    tag: str
    input_type: str
    role: str
    options: tuple[str, ...]
    required: bool
    visible: bool
    disabled: bool
    observed_value: str
    checked: bool
    max_characters: int | None
    max_words: int | None
    kind: FieldKind = FieldKind.UNKNOWN

    @property
    def has_observed_value(self) -> bool:
        if self.input_type in {"checkbox", "radio"} or self.role in {
            "checkbox",
            "radio",
        }:
            return self.checked
        if self.tag == "select":
            normalized = _normalise(self.observed_value)
            return normalized not in {
                "",
                "0",
                "select",
                "select an option",
                "please select",
                "choose",
                "choose an option",
            }
        return bool(self.observed_value.strip() or self.checked)

    def evidence(
        self, *, resolution: str = "unresolved", source: str = ""
    ) -> FieldEvidence:
        return FieldEvidence(
            field_id=_sanitize_evidence_text(self.field_id, limit=120),
            section=_sanitize_evidence_text(self.section, limit=160),
            question=_sanitize_evidence_text(self.question, limit=300),
            field_type=self.input_type or self.role or self.tag,
            classification=self.kind.value,
            required=self.required,
            options=tuple(
                _sanitize_evidence_text(option, limit=120)
                for option in self.options[:50]
            ),
            max_characters=self.max_characters,
            max_words=self.max_words,
            had_observed_value=self.has_observed_value,
            resolution=resolution,
            source=source,
        )


@dataclass(frozen=True, slots=True)
class ProposedAnswer:
    value: str = ""
    source: str = ""
    blocker: str = ""


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _sanitize_evidence_text(value: str, *, limit: int) -> str:
    cleaned = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[redacted-email]",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split())[:limit]


def _contains_phrase(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {_normalise(text)} "
    return any(f" {_normalise(phrase)} " in padded for phrase in phrases)


def classify_field(
    question: str,
    *,
    field_id: str = "",
    section: str = "",
    tag: str = "input",
    input_type: str = "text",
    role: str = "",
) -> FieldKind:
    """Classify using section-aware phrases; ordering is intentionally strict."""
    label = _normalise(" ".join((question, field_id)))
    section_text = _normalise(section)
    combined = f"{section_text} {label}".strip()
    compact = label.replace(" ", "")

    if input_type == "file":
        if _contains_phrase(combined, ("cover letter", "motivation letter")):
            return FieldKind.COVER_LETTER_UPLOAD
        if _contains_phrase(combined, ("transcript", "academic record")):
            return FieldKind.TRANSCRIPT_UPLOAD
        if _contains_phrase(combined, ("resume", "résumé", "curriculum vitae", "cv")):
            return FieldKind.CV_UPLOAD
        return FieldKind.OTHER_UPLOAD

    if _contains_phrase(
        combined, ("electronic signature", "type your name as signature", "signature")
    ):
        return FieldKind.SIGNATURE
    if _contains_phrase(
        combined, ("i certify", "i declare", "i attest", "declaration", "attestation")
    ):
        return FieldKind.DECLARATION
    if _contains_phrase(
        combined,
        (
            "consent",
            "agree to",
            "privacy terms",
            "data processing",
            "marketing",
            "newsletter",
            "job alerts",
        ),
    ):
        return FieldKind.CONSENT
    if _contains_phrase(
        combined, ("referee", "reference contact", "reference details")
    ):
        return FieldKind.REFEREE
    if _contains_phrase(
        section_text,
        ("employer", "employment", "work experience", "work history", "education"),
    ) and _contains_phrase(label, ("email", "phone", "first name", "last name")):
        return FieldKind.RECORD_FIELD
    if role == "combobox" and tag != "select":
        return FieldKind.CUSTOM_DROPDOWN
    if _contains_phrase(
        combined,
        ("employer name", "company name", "organisation name", "organization name"),
    ) or any(
        token in compact
        for token in (
            "employername",
            "companyname",
            "organisationname",
            "organizationname",
        )
    ):
        return FieldKind.EMPLOYER_NAME
    if _contains_phrase(label, ("first name", "given name", "forename")) or any(
        token in compact for token in ("firstname", "givenname")
    ):
        return FieldKind.FIRST_NAME
    if _contains_phrase(label, ("middle name", "middle initial")):
        return FieldKind.MIDDLE_NAME
    if _contains_phrase(label, ("last name", "family name", "surname")) or any(
        token in compact for token in ("lastname", "familyname")
    ):
        return FieldKind.LAST_NAME
    if (
        _contains_phrase(
            label, ("full name", "applicant name", "candidate name", "your name")
        )
        or "fullname" in compact
    ):
        return FieldKind.FULL_NAME
    if label == "name" and _contains_phrase(
        section_text, ("personal information", "applicant", "candidate")
    ):
        return FieldKind.FULL_NAME
    if _contains_phrase(label, ("email", "e mail")):
        return FieldKind.EMAIL
    if _contains_phrase(label, ("phone", "mobile", "telephone")):
        return FieldKind.PHONE
    if _contains_phrase(label, ("linkedin",)):
        return FieldKind.LINKEDIN
    if _contains_phrase(label, ("portfolio", "personal website", "github")):
        return FieldKind.PORTFOLIO
    if _contains_phrase(
        label,
        (
            "work authorization",
            "work authorisation",
            "right to work",
            "authorized to work",
            "authorised to work",
            "eligible to work",
        ),
    ):
        return FieldKind.ELIGIBILITY
    if _contains_phrase(label, ("sponsorship", "require a visa", "need a visa")):
        return FieldKind.SPONSORSHIP
    if _contains_phrase(
        combined,
        (
            "gender",
            "race",
            "ethnicity",
            "veteran",
            "disability",
            "sexual orientation",
            "religion",
        ),
    ):
        return FieldKind.DEMOGRAPHIC
    if _contains_phrase(label, ("cover letter", "covering letter")):
        return FieldKind.COVER_LETTER
    if _contains_phrase(
        label,
        (
            "responsibilities",
            "job duties",
            "role description",
            "employment description",
        ),
    ):
        return FieldKind.RESPONSIBILITIES
    if _contains_phrase(
        label,
        (
            "why do you want",
            "why are you interested",
            "motivation",
            "why this role",
            "why this company",
            "why this firm",
        ),
    ):
        return FieldKind.MOTIVATION
    if _contains_phrase(
        label,
        (
            "competency",
            "tell us about a time",
            "give an example",
            "describe a situation",
        ),
    ):
        return FieldKind.COMPETENCY
    if _contains_phrase(
        label, ("additional information", "anything else", "supporting information")
    ):
        return FieldKind.ADDITIONAL_INFORMATION
    if tag == "textarea":
        return FieldKind.NARRATIVE
    if _contains_phrase(
        label, ("city", "current location", "home location", "address location")
    ):
        return FieldKind.LOCATION
    return FieldKind.UNKNOWN


def _integer_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _limit_from_text(text: str, unit: str) -> int | None:
    match = re.search(
        rf"(?:maximum|max(?:imum)? of|up to|limit(?: of)?|no more than)?\s*(\d{{1,6}})\s*{unit}",
        text.lower(),
    )
    return int(match.group(1)) if match else None


def extract_field_manifest(
    page: Any, *, root_selector: str = "form, main, body"
) -> tuple[ManifestField, ...]:
    """Read the complete visible form shape once before a step is mutated."""
    script = r"""
    (root, selector) => {
      const visible = (el) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      };
      return [...root.querySelectorAll(selector)].map((el, index) => {
        const id = el.id || '';
        const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
        const group = el.closest('fieldset,[role="group"],section,li,div');
        const section = el.closest('section,[role="group"],fieldset');
        const question = [
          explicit?.innerText || '', el.getAttribute('aria-label') || '',
          el.getAttribute('placeholder') || '', group?.querySelector('legend,label')?.innerText || '',
          el.name || '', id
        ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().slice(0, 500);
        const sectionText = [
          section?.querySelector('h1,h2,h3,h4,legend')?.innerText || '',
          section?.getAttribute('aria-label') || ''
        ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().slice(0, 200);
        const options = el.tagName.toLowerCase() === 'select'
          ? [...el.options].map((option) => (option.innerText || '').trim()).filter(Boolean)
          : [];
        const value = el.isContentEditable
          ? (el.innerText || '')
          : (el.value || el.getAttribute('aria-valuetext') || el.getAttribute('data-value') || '');
        return {
          index, field_id: id || el.name || `field-${index}`, section: sectionText,
          question, tag: el.tagName.toLowerCase(),
          input_type: (el.getAttribute('type') || '').toLowerCase(),
          role: (el.getAttribute('role') || '').toLowerCase(), options,
          required: !!el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(question),
          visible: visible(el), disabled: !!el.disabled,
          observed_value: value, checked: !!el.checked || el.getAttribute('aria-checked') === 'true',
          max_characters: el.maxLength > 0 ? el.maxLength : null
        };
      });
    }
    """
    root = page.locator(root_selector).first
    raw_fields = root.evaluate(script, CONTROL_SELECTOR)
    fields: list[ManifestField] = []
    for raw in raw_fields or []:
        question = str(raw.get("question") or "")
        max_characters = _integer_or_none(
            raw.get("max_characters")
        ) or _limit_from_text(question, r"(?:characters?|chars?)")
        max_words = _limit_from_text(question, r"words?")
        field = ManifestField(
            index=int(raw.get("index") or 0),
            field_id=str(raw.get("field_id") or ""),
            section=str(raw.get("section") or ""),
            question=question,
            tag=str(raw.get("tag") or ""),
            input_type=str(raw.get("input_type") or ""),
            role=str(raw.get("role") or ""),
            options=tuple(str(option) for option in raw.get("options") or ()),
            required=bool(raw.get("required")),
            visible=bool(raw.get("visible")),
            disabled=bool(raw.get("disabled")),
            observed_value=str(raw.get("observed_value") or ""),
            checked=bool(raw.get("checked")),
            max_characters=max_characters,
            max_words=max_words,
        )
        fields.append(
            replace(
                field,
                kind=classify_field(
                    field.question,
                    field_id=field.field_id,
                    section=field.section,
                    tag=field.tag,
                    input_type=field.input_type,
                    role=field.role,
                ),
            )
        )
    return tuple(fields)


def _profile_answer(profile: dict[str, Any], *keys: str) -> str:
    answers = profile.get("answers") or {}
    if not isinstance(answers, dict):
        answers = {}
    for key in keys:
        value = answers.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _exact_question_answer(field: ManifestField, profile: dict[str, Any]) -> str:
    answers = profile.get("answers") or {}
    if not isinstance(answers, dict):
        return ""
    normalized_question = _normalise(field.question)
    normalized_id = _normalise(field.field_id)
    for key, value in answers.items():
        normalized_key = _normalise(str(key))
        if normalized_key and normalized_key in {normalized_question, normalized_id}:
            return str(value or "").strip()
    return ""


def resolve_deterministic_answer(
    field: ManifestField,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
) -> ProposedAnswer:
    """Resolve only candidate-confirmed facts; narrative generation happens elsewhere."""
    direct: dict[FieldKind, tuple[str, str]] = {
        FieldKind.FIRST_NAME: (
            str(profile.get("first_name") or "").strip(),
            "profile.first_name",
        ),
        FieldKind.MIDDLE_NAME: (
            str(profile.get("middle_name") or "").strip(),
            "profile.middle_name",
        ),
        FieldKind.LAST_NAME: (
            str(profile.get("last_name") or "").strip(),
            "profile.last_name",
        ),
        FieldKind.FULL_NAME: (
            str(profile.get("full_name") or "").strip(),
            "profile.full_name",
        ),
        FieldKind.EMAIL: (str(profile.get("email") or "").strip(), "profile.email"),
        FieldKind.PHONE: (str(profile.get("phone") or "").strip(), "profile.phone"),
        FieldKind.LOCATION: (
            str(profile.get("location") or defaults.get("location") or "").strip(),
            "profile.location",
        ),
        FieldKind.LINKEDIN: (
            str(profile.get("linkedin_url") or "").strip(),
            "profile.linkedin_url",
        ),
        FieldKind.PORTFOLIO: (
            str(profile.get("github_url") or "").strip(),
            "profile.github_url",
        ),
        FieldKind.ELIGIBILITY: (
            _profile_answer(
                profile, "authorized_to_work", "right_to_work", "work_authorization"
            ),
            "profile.answers.authorized_to_work",
        ),
        FieldKind.SPONSORSHIP: (
            _profile_answer(profile, "require_sponsorship"),
            "profile.answers.require_sponsorship",
        ),
    }
    if field.kind in direct:
        value, source = direct[field.kind]
        return ProposedAnswer(value=value, source=source)
    if field.kind == FieldKind.EMPLOYER_NAME:
        return ProposedAnswer(
            blocker="Employer fields require a specific work-history record"
        )
    if field.kind in {
        FieldKind.DEMOGRAPHIC,
        FieldKind.DECLARATION,
        FieldKind.CONSENT,
        FieldKind.SIGNATURE,
        FieldKind.RESPONSIBILITIES,
        FieldKind.COVER_LETTER,
        FieldKind.MOTIVATION,
        FieldKind.COMPETENCY,
        FieldKind.ADDITIONAL_INFORMATION,
        FieldKind.NARRATIVE,
        FieldKind.UNKNOWN,
    }:
        value = _exact_question_answer(field, profile)
        if value:
            return ProposedAnswer(value=value, source="profile.answers.exact_question")
        if field.kind in {
            FieldKind.DEMOGRAPHIC,
            FieldKind.DECLARATION,
            FieldKind.CONSENT,
            FieldKind.SIGNATURE,
            FieldKind.RESPONSIBILITIES,
        }:
            return ProposedAnswer(
                blocker=f"Candidate choice required: {field.question or field.field_id}"
            )
    return ProposedAnswer()


def answer_limit_blocker(field: ManifestField, value: str) -> str:
    if field.max_characters is not None and len(value) > field.max_characters:
        return (
            f"Answer for {field.question or field.field_id} is {len(value)} characters; "
            f"limit is {field.max_characters}"
        )
    word_count = len(value.split())
    if field.max_words is not None and word_count > field.max_words:
        return (
            f"Answer for {field.question or field.field_id} is {word_count} words; "
            f"limit is {field.max_words}"
        )
    return ""


def exact_option(options: Iterable[str], value: str) -> str:
    wanted = _normalise(value)
    if not wanted:
        return ""
    return next((option for option in options if _normalise(option) == wanted), "")
