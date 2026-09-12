"""Fail-closed checks performed immediately before review or submission."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .field_manifest import FieldKind, classify_field


AI_RESTRICTION_PATTERNS = (
    r"(?:must|may)\s+not\s+(?:use|be written with).{0,50}(?:generative\s+)?ai",
    r"(?:should|do)\s+not\s+use.{0,30}(?:chatgpt|artificial intelligence|generative\s+ai|\bai\b)",
    r"(?:use|using)\s+of\s+(?:generative\s+)?ai\s+(?:is\s+)?(?:not permitted|prohibited|forbidden)",
    r"do\s+not\s+permit.{0,40}(?:use|using)\s+of\s+(?:generative\s+)?ai",
    r"answers?\s+must\s+be\s+(?:your|the candidate'?s)\s+own\s+work",
    r"do\s+not\s+use\s+(?:chatgpt|artificial intelligence|generative ai)",
    r"without\s+(?:the\s+)?(?:use|assistance)\s+of\s+(?:generative\s+)?ai",
    r"\bno\s+(?:generative\s+)?ai\s+(?:use|assistance|tools?)?\b",
)

ATTESTATION_PATTERNS = (
    r"\bi\s+(?:hereby\s+)?certify\s+that\b",
    r"\bi\s+(?:hereby\s+)?declare\s+that\b",
    r"\bi\s+(?:hereby\s+)?attest\s+that\b",
    r"\bi\s+confirm\s+that\s+the\s+information\b",
    r"\bby\s+submitting\s+(?:this\s+)?application.{0,100}\b(?:certify|declare|confirm|attest)\b",
    r"\belectronic\s+signature\b",
    r"\btype\s+(?:your|my)\s+(?:full\s+)?name\s+as\s+(?:your|my)\s+signature\b",
    r"\bi\s+(?:have\s+)?not\s+used.{0,40}(?:generative\s+)?ai\b",
)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    blockers: tuple[str, ...] = ()
    checked_fields: int = 0
    uploaded_files: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers


def ai_rewrite_required(text: str, *, use_ai: bool) -> bool:
    if not use_ai:
        return False
    return ai_policy_prohibited(text)


def ai_policy_prohibited(text: str) -> bool:
    """Detect an explicit prohibition independently of the requested AI setting."""
    lowered = " ".join((text or "").lower().split())
    return any(re.search(pattern, lowered) for pattern in AI_RESTRICTION_PATTERNS)


def attestation_blockers(text: str) -> tuple[str, ...]:
    lowered = " ".join((text or "").lower().split())
    if any(re.search(pattern, lowered) for pattern in ATTESTATION_PATTERNS):
        return ("A declaration, attestation or signature requires the candidate",)
    return ()


def _normalise_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def verify_application_ready(
    page: Any,
    *,
    profile: dict[str, Any],
    cv_path: str,
    root_selector: str = "form, main, body",
) -> VerificationReport:
    """Inspect native required controls and verify key populated values."""
    script = """
    (ignored, roots) => {
      const visible = (el) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      };
      const root = document.querySelector(roots) || document.body;
      const fields = [...root.querySelectorAll(
        'input, textarea, select, [contenteditable="true"], [role="combobox"], '
        + '[role="textbox"], [role="checkbox"], [role="radio"]'
      )];
      return fields.map((el) => {
        const id = el.id || '';
        const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
        const wrapper = el.closest('label,fieldset,[role="group"],div');
        const label = [
          explicit?.innerText || '',
          el.getAttribute('aria-label') || '',
          el.getAttribute('placeholder') || '',
          wrapper?.querySelector('legend,label')?.innerText || '',
          el.name || '',
          id
        ].filter(Boolean).join(' ').slice(0, 300);
        const type = (
          el.getAttribute('type') || el.getAttribute('role') || el.tagName || ''
        ).toLowerCase();
        const files = el.files ? [...el.files].map((file) => file.name) : [];
        const value = el.isContentEditable
          ? (el.innerText || '')
          : (el.value || el.getAttribute('aria-valuetext') || el.getAttribute('data-value') || '');
        return {
          tag: el.tagName.toLowerCase(), type, name: el.name || '', label,
          required: !!el.required || el.getAttribute('aria-required') === 'true' || /\\*/.test(label),
          visible: visible(el), disabled: !!el.disabled,
          value,
          checked: !!el.checked || el.getAttribute('aria-checked') === 'true', files
        };
      });
    }
    """
    try:
        fields = page.locator("body").evaluate(script, root_selector)
    except Exception as exc:
        return VerificationReport(
            blockers=(f"Final field verification could not run ({type(exc).__name__})",)
        )

    blockers: list[str] = []
    uploads: list[str] = []
    required_radio_groups: dict[str, list[dict[str, Any]]] = {}
    expected_email = str(profile.get("email") or "").strip().lower()
    expected_phone = _normalise_phone(str(profile.get("phone") or ""))
    expected_names = {
        FieldKind.FIRST_NAME: str(profile.get("first_name") or "").strip(),
        FieldKind.MIDDLE_NAME: str(profile.get("middle_name") or "").strip(),
        FieldKind.LAST_NAME: str(profile.get("last_name") or "").strip(),
        FieldKind.FULL_NAME: str(profile.get("full_name") or "").strip(),
    }
    checked_fields = 0

    for field in fields or []:
        if field.get("disabled"):
            continue
        field_type = str(field.get("type") or "").lower()
        label = " ".join(str(field.get("label") or "").split()) or "unnamed field"
        value = str(field.get("value") or "").strip()
        files = tuple(str(name) for name in field.get("files") or [])
        uploads.extend(files)
        required = bool(field.get("required"))
        visible = bool(field.get("visible"))

        if field_type == "radio" and required:
            required_radio_groups.setdefault(str(field.get("name") or label), []).append(field)
            continue
        if field_type == "file":
            if required and not files:
                blockers.append(f"Required upload is empty: {label[:100]}")
            continue
        if not visible or field_type in {"hidden", "submit", "button", "reset"}:
            continue
        checked_fields += 1
        if required and field_type == "checkbox" and not field.get("checked"):
            blockers.append(f"Required checkbox is unresolved: {label[:100]}")
        elif required and field_type != "checkbox" and not value:
            blockers.append(f"Required field is empty: {label[:100]}")

        lowered_label = label.lower()
        identity_kind = classify_field(
            label,
            field_id=str(field.get("name") or ""),
            tag=str(field.get("tag") or "input"),
            input_type=field_type,
        )
        expected_name = expected_names.get(identity_kind, "")
        if expected_name and value and " ".join(value.split()).casefold() != " ".join(expected_name.split()).casefold():
            blockers.append(
                f"{identity_kind.value.replace('_', ' ').title()} does not match the verified profile"
            )
        if expected_email and (field_type == "email" or "email" in lowered_label) and value:
            if value.lower() != expected_email:
                blockers.append("Application email does not match the verified profile")
        if expected_phone and any(term in lowered_label for term in ("phone", "mobile", "telephone")):
            actual_phone = _normalise_phone(value)
            if actual_phone and not (
                actual_phone.endswith(expected_phone[-9:])
                or expected_phone.endswith(actual_phone[-9:])
            ):
                blockers.append("Application phone does not match the verified profile")

    for name, radios in required_radio_groups.items():
        if not any(bool(radio.get("checked")) for radio in radios):
            blockers.append(f"Required choice is unresolved: {name[:100]}")

    cv_name = Path(cv_path).name.lower()
    if uploads and cv_name not in {name.lower() for name in uploads}:
        blockers.append(f"Uploaded document does not include the approved CV: {cv_name}")

    return VerificationReport(
        blockers=tuple(dict.fromkeys(blockers)),
        checked_fields=checked_fields,
        uploaded_files=tuple(dict.fromkeys(uploads)),
    )
