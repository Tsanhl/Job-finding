from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from playwright.sync_api import Page

from .answers import answer_question
from .application_models import (
    AiPolicy,
    FieldEvidence,
    OutcomeStatus,
    RunMode,
)
from .cover_letter import generate_cover_letter
from .field_manifest import (
    CONTROL_SELECTOR,
    FieldKind,
    ManifestField,
    answer_limit_blocker,
    exact_option,
    extract_field_manifest,
    resolve_deterministic_answer,
)
from .profile import load_profile
from .submission_guard import (
    ai_policy_prohibited,
    attestation_blockers,
    verify_application_ready,
)
from .urlutil import normalize_job_url


LogFn = Callable[[str], None]


@dataclass
class ApplyResult:
    title: str
    company: str
    url: str
    status: OutcomeStatus | str
    detail: str = ""
    blockers: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    field_evidence: tuple[FieldEvidence, ...] = ()

    def __post_init__(self) -> None:
        self.status = OutcomeStatus.parse(self.status)
        self.blockers = tuple(dict.fromkeys(self.blockers))
        self.actions = tuple(self.actions)
        self.field_evidence = tuple(self.field_evidence)

    @property
    def legacy_status(self) -> str | None:
        from .application_models import FillOutcome

        return FillOutcome(self.status).legacy_status

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "status": self.status.value,
            "detail": self.detail,
            "blockers": list(self.blockers),
            "actions": list(self.actions),
            "field_evidence": [item.to_dict() for item in self.field_evidence],
        }
        if self.legacy_status:
            data["legacy_status"] = self.legacy_status
        return data


@dataclass
class RunSummary:
    searched_keywords: str
    location: str
    results: list[ApplyResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "searched_keywords": self.searched_keywords,
            "location": self.location,
            "results": [r.to_dict() for r in self.results],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }


def build_search_url(
    keywords: str,
    location: str,
    *,
    easy_apply_only: bool = True,
    entry_level: bool = True,
    past_week: bool = True,
) -> str:
    """Build LinkedIn jobs search URL with useful filters for intern/grad hunting."""
    params: dict[str, str] = {
        "keywords": keywords,
        "location": location,
    }
    if easy_apply_only:
        params["f_AL"] = "true"
    # f_E: 1=Internship, 2=Entry level
    if entry_level:
        params["f_E"] = "1,2"
    # f_TPR: past week
    if past_week:
        params["f_TPR"] = "r604800"
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def job_id_from_url(url: str) -> str:
    """Extract numeric LinkedIn job id from a /jobs/view/ URL."""
    m = re.search(r"/jobs/view/(\d+)", url or "")
    return m.group(1) if m else ""


def open_job_detail(page: Page, job_href: str, *, search_url: str | None = None) -> bool:
    """
    Open a job so the Apply control is interactable.

    Direct /jobs/view/ pages often use a hashed <a> Easy Apply link (no
    button.jobs-apply-button). Search results with currentJobId hydrate the
    classic top-card Apply button — prefer that path.
    """
    href = normalize_job_url(job_href)
    job_id = job_id_from_url(href)

    if search_url and job_id:
        # Prefer staying on search UI: click card if present, else currentJobId URL
        card = page.locator(f"a[href*='/jobs/view/{job_id}']").first
        try:
            if "jobs/search" in (page.url or "") and card.count() and card.is_visible(timeout=1500):
                card.scroll_into_view_if_needed(timeout=2000)
                card.click(timeout=4000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass

        sep = "&" if "?" in search_url else "?"
        target = f"{search_url}{sep}currentJobId={job_id}"
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)
            return True
        except Exception:
            pass

    page.goto(href, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    return True


# Selectors covering classic search pane buttons AND standalone SDUI <a> Apply links
APPLY_BUTTON_SELECTORS = (
    "button.jobs-apply-button",
    "a.jobs-apply-button",
    "button[aria-label*='Easy Apply' i]",
    "a[aria-label*='Easy Apply' i]",
    "button[aria-label*='Apply' i]",
    "a[aria-label*='Apply' i]",
    "a[href*='openSDUIApplyFlow']",
    "a[href*='/apply/'][href*='/jobs/view/']",
    "button:has-text('Easy Apply')",
    "button:has-text('Continue applying')",
    "button:has-text('Apply')",
)

EASY_APPLY_ROOT_SELECTOR = (
    ".jobs-easy-apply-modal, .jobs-easy-apply-content, "
    "div[aria-labelledby*='jobs-apply']"
)

LINKEDIN_PROGRESS_LABELS = (
    "Review your application",
    "Review",
    "Continue to next step",
    "Next",
)


def _log(msg: str, log: LogFn | None) -> None:
    if log:
        log(msg)
    else:
        print(msg)


def _safe_text(page: Page, selector: str) -> str:
    loc = page.locator(selector).first
    if loc.count() == 0:
        return ""
    try:
        return (loc.inner_text(timeout=1500) or "").strip()
    except Exception:
        return ""


def _click_if_visible(page: Page, selectors: list[str], *, timeout: float = 2500) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=800):
                loc.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


def _narrative_value(
    field: ManifestField,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    job_context: str,
    use_ai: bool,
) -> tuple[str, str]:
    if not use_ai:
        return "", ""
    if field.kind == FieldKind.COVER_LETTER:
        return (
            generate_cover_letter(
                profile,
                company=str(defaults.get("_company") or "the company"),
                role=str(defaults.get("_role") or "the role"),
                location=str(defaults.get("location") or profile.get("location") or ""),
                job_description=job_context,
                use_ai=True,
            ),
            "ai.cover_letter",
        )
    if field.kind in {
        FieldKind.MOTIVATION,
        FieldKind.COMPETENCY,
        FieldKind.ADDITIONAL_INFORMATION,
        FieldKind.NARRATIVE,
    }:
        return (
            answer_question(
                field.question,
                profile,
                job_context=job_context,
                defaults=defaults,
                use_ai=True,
            ),
            "ai.question_answer",
        )
    return "", ""


def _radio_option(control: Any) -> str:
    value = str(control.get_attribute("value") or "").strip()
    try:
        label = str(
            control.evaluate(
                "n => (n.closest('label') || n.parentElement)?.innerText || ''"
            )
            or ""
        ).strip()
    except Exception:
        label = ""
    return label or value


def _fill_manifest_step(
    page: Page,
    manifest: tuple[ManifestField, ...],
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    job_context: str,
    use_ai: bool,
    log: LogFn | None,
) -> tuple[tuple[str, ...], tuple[FieldEvidence, ...]]:
    """Resolve one LinkedIn step through the same typed manifest used by ATS forms."""
    root = page.locator(EASY_APPLY_ROOT_SELECTOR).first
    controls = root.locator(CONTROL_SELECTOR)
    blockers: list[str] = []
    evidence: list[FieldEvidence] = []

    for field in manifest:
        label = field.question or field.field_id
        if not field.visible or field.disabled:
            evidence.append(field.evidence(resolution="ignored"))
            continue
        if field.has_observed_value:
            evidence.append(field.evidence(resolution="preserved", source="portal.observed"))
            continue

        control = controls.nth(field.index)
        if field.kind == FieldKind.CUSTOM_DROPDOWN:
            if field.required:
                blockers.append(f"Custom dropdown is unsupported: {label}")
            evidence.append(field.evidence(resolution="unsupported"))
            continue
        if field.input_type == "file":
            if field.kind != FieldKind.CV_UPLOAD:
                if field.required:
                    blockers.append(
                        f"Required {field.kind.value.replace('_', ' ')} needs an approved document"
                    )
                evidence.append(field.evidence(resolution="unresolved"))
                continue
            try:
                control.set_input_files(cv_path)
                evidence.append(field.evidence(resolution="filled", source="approved.cv"))
                _log(f"Uploaded approved CV to {label[:60]}", log)
            except Exception as exc:
                blockers.append(f"CV upload failed for {label} ({type(exc).__name__})")
                evidence.append(field.evidence(resolution="blocked"))
            continue

        proposal = resolve_deterministic_answer(field, profile=profile, defaults=defaults)
        value, source = proposal.value, proposal.source
        if not value:
            value, source = _narrative_value(
                field,
                profile=profile,
                defaults=defaults,
                job_context=job_context,
                use_ai=use_ai,
            )
        if not value:
            if proposal.blocker:
                blockers.append(proposal.blocker)
            elif field.required:
                blockers.append(f"Required field is unresolved: {label}")
            evidence.append(field.evidence(resolution="unresolved"))
            continue

        limit_blocker = answer_limit_blocker(field, value)
        if limit_blocker:
            blockers.append(limit_blocker)
            evidence.append(field.evidence(resolution="over_limit", source=source))
            continue

        try:
            if field.tag == "select":
                choice = exact_option(field.options, value)
                if not choice:
                    blockers.append(f"Confirmed answer has no exact option for {label}")
                    evidence.append(field.evidence(resolution="unresolved", source=source))
                    continue
                control.select_option(label=choice)
            elif field.input_type == "radio":
                option = _radio_option(control)
                if not exact_option((field.observed_value, option), value):
                    evidence.append(field.evidence(resolution="not_selected", source=source))
                    continue
                control.check()
            elif field.input_type == "checkbox":
                normalized = value.strip().lower()
                if normalized in {"yes", "true", "checked", "agree"}:
                    control.check()
                elif normalized in {"no", "false", "unchecked", "decline"}:
                    if field.required:
                        blockers.append(f"Required checkbox was explicitly declined: {label}")
                    evidence.append(field.evidence(resolution="explicit_unchecked", source=source))
                    continue
                else:
                    blockers.append(f"Candidate must confirm the checkbox: {label}")
                    evidence.append(field.evidence(resolution="unresolved", source=source))
                    continue
            else:
                control.fill(value)
            evidence.append(field.evidence(resolution="filled", source=source))
            _log(f"Filled {label[:60]} from {source}", log)
        except Exception as exc:
            blockers.append(f"Could not fill {label} ({type(exc).__name__})")
            evidence.append(field.evidence(resolution="blocked", source=source))

    return tuple(dict.fromkeys(blockers)), tuple(evidence)


def _dismiss_modals(page: Page) -> None:
    """Dismiss overlays. Prefer Cancel on discard confirm (keep application)."""
    # If discard confirmation is open, Cancel = keep editing
    for sel in (
        "button:has-text('Continue applying')",
        "button[data-test-dialog-secondary-btn]",
        "div[data-test-modal-id='data-test-easy-apply-discard-confirmation'] button:has-text('Cancel')",
    ):
        try:
            b = page.locator(sel).first
            if b.count() and b.is_visible(timeout=400):
                b.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:
            pass
    _click_if_visible(
        page,
        [
            "button:has-text('Not now')",
            "button:has-text('No thanks')",
        ],
    )


def _easy_apply_modal(page: Page):
    return page.locator(EASY_APPLY_ROOT_SELECTOR).first


def _click_modal_button(
    page: Page,
    labels: list[str],
    *,
    timeout: float = 3500,
) -> str | None:
    """Click an exact, adapter-declared, non-final Easy Apply control."""
    modal = _easy_apply_modal(page)
    if not modal.count():
        return None
    roots = []
    footer = modal.locator(
        "footer, .jobs-easy-apply-footer, div[class*='easy-apply-modal__footer'], "
        ".jobs-easy-apply-modal__footer"
    ).first
    if footer.count():
        roots.append(footer)
    roots.append(modal)

    for label in labels:
        for root in roots:
            loc = root.get_by_role("button", name=label, exact=True).first
            try:
                if not (loc.count() and loc.is_visible(timeout=500)):
                    continue
                aria = (loc.get_attribute("aria-label") or "").lower()
                visible_text = (loc.inner_text() or "").lower()
                blob = f"{aria} {visible_text}"
                if any(
                    bad in blob
                    for bad in (
                        "apply",
                        "submit",
                        "send application",
                        "discard",
                        "delete",
                        "save for later",
                    )
                ):
                    continue
                if (loc.get_attribute("type") or "button").lower() == "submit":
                    continue
                if loc.is_disabled(timeout=300):
                    continue
                loc.scroll_into_view_if_needed(timeout=1000)
                loc.click(timeout=timeout)
                return label
            except Exception:
                continue
    return None


def _unanswered_required_hints(page: Page) -> list[str]:
    """Best-effort list of empty required fields blocking safe progress or review."""
    hints: list[str] = []
    hints.extend(_easy_apply_has_errors(page))
    try:
        empty = page.evaluate(
            """() => {
              const modal = document.querySelector(
                '.jobs-easy-apply-modal, .jobs-easy-apply-content'
              ) || document;
              const out = [];
              const nodes = modal.querySelectorAll('input, select, textarea');
              for (const el of nodes) {
                const t = (el.getAttribute('type') || '').toLowerCase();
                if (['hidden','file','submit','button'].includes(t)) continue;
                const req = el.required || el.getAttribute('aria-required') === 'true'
                  || (el.closest('[data-test-form-element]')||'').innerHTML?.includes('required');
                if (!req) continue;
                let val = '';
                if (t === 'radio' || t === 'checkbox') {
                  const name = el.name;
                  if (name && modal.querySelector(`input[name="${name}"]:checked`)) continue;
                  if (t === 'checkbox' && el.checked) continue;
                  if (t === 'radio') {
                    // only report once per group
                  }
                } else {
                  val = (el.value || '').trim();
                  if (val) continue;
                }
                const label = el.getAttribute('aria-label')
                  || el.getAttribute('placeholder')
                  || (el.id && modal.querySelector(`label[for="${el.id}"]`)?.innerText)
                  || el.closest('div,fieldset,li')?.querySelector('label,span')?.innerText
                  || el.name
                  || t
                  || 'field';
                const s = String(label).replace(/\\s+/g, ' ').trim().slice(0, 80);
                if (s && !out.includes(s)) out.push(s);
                if (out.length >= 6) break;
              }
              return out;
            }"""
        )
        for h in empty or []:
            if h not in hints:
                hints.append(h)
    except Exception:
        pass
    return hints[:6]


def _easy_apply_has_errors(page: Page) -> list[str]:
    errs = page.locator(
        ".artdeco-inline-feedback--error, .fb-form-element__error, span[data-test-form-element-error-message]"
    )
    out: list[str] = []
    for i in range(min(errs.count(), 8)):
        try:
            t = (errs.nth(i).inner_text(timeout=400) or "").strip()
            if t:
                out.append(t[:120])
        except Exception:
            continue
    return out


def _visible_submit_candidate(page: Page) -> bool:
    candidate = page.locator(
        "button[aria-label*='Submit' i], button:has-text('Submit application'), "
        "button:has-text('Submit Application')"
    ).first
    try:
        return candidate.count() > 0 and candidate.is_visible(timeout=500)
    except Exception:
        return False


def _fill_easy_apply_step(
    page: Page,
    *,
    manifest: tuple[ManifestField, ...],
    profile: dict[str, Any],
    local_defaults: dict[str, Any],
    cv_path: str,
    job_context: str,
    use_ai: bool,
    log: LogFn | None,
) -> tuple[tuple[str, ...], tuple[FieldEvidence, ...]]:
    return _fill_manifest_step(
        page,
        manifest,
        profile=profile,
        defaults=local_defaults,
        cv_path=cv_path,
        job_context=job_context,
        use_ai=use_ai,
        log=log,
    )


def complete_easy_apply(
    page: Page,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    job_context: str,
    title: str,
    company: str,
    url: str,
    use_ai: bool,
    dry_run: bool,
    log: LogFn | None,
    allow_submit: bool | None = None,
    accept_required_terms: bool = False,
    mode: RunMode | str | None = None,
    ai_policy: AiPolicy | str | None = None,
) -> ApplyResult:
    """
    Assisted Easy Apply wizard: inspect → fill → verified progress → manual review.
    Unknown required fields leave the modal open and return needs-information.
    """
    selected_mode = RunMode.parse(mode, dry_run=dry_run)
    selected_ai_policy = AiPolicy.parse(ai_policy)
    if allow_submit or selected_mode == RunMode.GUARDED_AUTO_SUBMIT:
        return ApplyResult(
            title,
            company,
            url,
            OutcomeStatus.SUBMISSION_DISABLED,
            "Automated final submission is disabled in this safety release.",
        )
    policy_prohibited = ai_policy_prohibited(job_context)
    policy_conflict = bool(
        use_ai and selected_ai_policy == AiPolicy.ALLOWED and policy_prohibited
    )
    effective_ai = bool(
        use_ai and selected_ai_policy == AiPolicy.ALLOWED and not policy_prohibited
    )
    local_defaults = {
        **defaults,
        "_company": company or "the company",
        "_role": title or "the role",
    }
    page.wait_for_timeout(300)
    if selected_mode == RunMode.LOCAL_PREVIEW:
        try:
            manifest = extract_field_manifest(
                page,
                root_selector=EASY_APPLY_ROOT_SELECTOR,
            )
        except Exception as exc:
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.FAILED_RETRYABLE,
                f"Field manifest extraction failed ({type(exc).__name__}); nothing was changed.",
            )
        return ApplyResult(
            title,
            company,
            url,
            OutcomeStatus.PREVIEW_READY,
            f"Inspected {len(manifest)} fields; no portal values, uploads, or controls were changed.",
            field_evidence=tuple(field.evidence(resolution="observed") for field in manifest),
        )
    _dismiss_modals(page)

    all_evidence: list[FieldEvidence] = []
    actions: list[str] = []

    for step in range(16):
        modal = _easy_apply_modal(page)
        if modal.count() == 0:
            if page.locator("text=Application sent").count() or page.locator(
                "text=Your application was sent"
            ).count():
                return ApplyResult(
                    title,
                    company,
                    url,
                    OutcomeStatus.SUBMISSION_UNCONFIRMED,
                    "The modal closed without a receipt-backed submission initiated by this run.",
                    actions=tuple(actions),
                    field_evidence=tuple(all_evidence),
                )
            if step == 0:
                return ApplyResult(
                    title,
                    company,
                    url,
                    OutcomeStatus.REVIEW_READY,
                    "Easy Apply modal not open.",
                    actions=tuple(actions),
                )
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.SUBMISSION_UNCONFIRMED,
                "The application modal disappeared; submission was not inferred.",
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )

        try:
            manifest = extract_field_manifest(
                page,
                root_selector=EASY_APPLY_ROOT_SELECTOR,
            )
        except Exception as exc:
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.FAILED_RETRYABLE,
                f"Field manifest extraction failed ({type(exc).__name__}); nothing on this step was changed.",
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        unsupported = [
            field.question or field.field_id
            for field in manifest
            if field.visible
            and not field.disabled
            and field.required
            and not field.has_observed_value
            and field.kind == FieldKind.CUSTOM_DROPDOWN
        ]
        if unsupported:
            blockers = tuple(f"Custom dropdown is unsupported: {item}" for item in unsupported)
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.UNSUPPORTED,
                "The application paused before changing an unsupported required control.",
                blockers=blockers,
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        missing_documents = [
            field
            for field in manifest
            if field.visible
            and not field.disabled
            and field.required
            and not field.has_observed_value
            and field.input_type == "file"
            and field.kind != FieldKind.CV_UPLOAD
        ]
        if missing_documents:
            blockers = tuple(
                f"Required {field.kind.value.replace('_', ' ')} needs an approved document"
                for field in missing_documents
            )
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.NEEDS_INFORMATION,
                "The application paused before uploading an unapproved document.",
                blockers=blockers,
                actions=tuple(actions),
                field_evidence=tuple(all_evidence)
                + tuple(field.evidence(resolution="unresolved") for field in missing_documents),
            )

        try:
            modal_text = modal.inner_text(timeout=1000) or ""
        except Exception:
            modal_text = ""
        if ai_policy_prohibited(f"{job_context}\n{modal_text}"):
            policy_conflict = policy_conflict or bool(
                use_ai and selected_ai_policy == AiPolicy.ALLOWED
            )
            effective_ai = False
        declarations = attestation_blockers(modal_text)
        if policy_conflict:
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.POLICY_BLOCKED,
                "Employer AI guidance conflicts with the requested policy; AI fields were left untouched.",
                blockers=("Employer AI policy conflicts with the requested AI setting.",),
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        if declarations:
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.NEEDS_INFORMATION,
                "The application paused for an explicit declaration or attestation.",
                blockers=tuple(declarations),
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )

        _log(f"Easy Apply step {step + 1}: filling fields…", log)
        fill_blockers, step_evidence = _fill_easy_apply_step(
            page,
            manifest=manifest,
            profile=profile,
            local_defaults=local_defaults,
            cv_path=cv_path,
            job_context=job_context,
            use_ai=effective_ai,
            log=log,
        )
        all_evidence.extend(step_evidence)
        if fill_blockers:
            status = (
                OutcomeStatus.UNSUPPORTED
                if any("custom dropdown" in blocker.lower() for blocker in fill_blockers)
                else OutcomeStatus.NEEDS_INFORMATION
            )
            return ApplyResult(
                title,
                company,
                url,
                status,
                "The application paused with unresolved fields.",
                blockers=fill_blockers,
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        page.wait_for_timeout(600)

        # Stop when a final action is visible; this release never clicks it.
        if _visible_submit_candidate(page):
            hints = _unanswered_required_hints(page)
            if hints:
                detail = "NEED INFO (modal left open): " + "; ".join(hints[:4])
                _log(f"*** PING: {detail} ***", log)
                return ApplyResult(
                    title,
                    company,
                    url,
                    OutcomeStatus.NEEDS_INFORMATION,
                    detail,
                    blockers=tuple(hints),
                    actions=tuple(actions),
                    field_evidence=tuple(all_evidence),
                )
            verification = verify_application_ready(
                page,
                profile=profile,
                cv_path=cv_path,
                root_selector=EASY_APPLY_ROOT_SELECTOR,
            )
            if verification.blockers:
                detail = "NEED INFO (modal left open): " + "; ".join(
                    verification.blockers[:4]
                )
                return ApplyResult(
                    title,
                    company,
                    url,
                    OutcomeStatus.NEEDS_INFORMATION,
                    detail,
                    blockers=tuple(verification.blockers),
                    actions=tuple(actions),
                    field_evidence=tuple(all_evidence),
                )
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.REVIEW_READY,
                "Application filled to final review; Submit left for the candidate.",
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        # Advance only through exact LinkedIn-declared, non-final labels.
        advanced = _click_modal_button(page, list(LINKEDIN_PROGRESS_LABELS))
        if not advanced:
            hints = _unanswered_required_hints(page)
            if hints:
                detail = "NEED INFO (modal left open): " + "; ".join(hints[:4])
                _log(f"*** PING: {detail} ***", log)
                return ApplyResult(
                    title,
                    company,
                    url,
                    OutcomeStatus.NEEDS_INFORMATION,
                    detail,
                    blockers=tuple(hints),
                    actions=tuple(actions),
                    field_evidence=tuple(all_evidence),
                )
            return ApplyResult(
                title,
                company,
                url,
                OutcomeStatus.REVIEW_READY,
                "No verified non-final action remains; review the open application.",
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        actions.append(f"progress:{advanced}")
        _log(f"Clicked '{advanced}'", log)
        page.wait_for_timeout(1200)

    hints = _unanswered_required_hints(page)
    detail = "NEED INFO: exited wizard without reaching a verified review step"
    if hints:
        detail += " — " + "; ".join(hints[:4])
    _log(f"*** PING: {detail} ***", log)
    return ApplyResult(
        title,
        company,
        url,
        "needs_info",
        detail,
        blockers=tuple(hints),
        actions=tuple(actions),
        field_evidence=tuple(all_evidence),
    )


def apply_to_current_job(
    page: Page,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    use_ai: bool,
    dry_run: bool,
    log: LogFn | None,
    allow_submit: bool | None = None,
    account_credentials: Any | None = None,
    verification_client: Any | None = None,
    accept_required_terms: bool = False,
    mode: RunMode | str | None = None,
    ai_policy: AiPolicy | str | None = None,
) -> ApplyResult:
    selected_mode = RunMode.parse(mode, dry_run=dry_run)
    selected_ai_policy = AiPolicy.parse(ai_policy)
    if allow_submit or selected_mode == RunMode.GUARDED_AUTO_SUBMIT:
        return ApplyResult(
            "",
            "",
            normalize_job_url(page.url),
            OutcomeStatus.SUBMISSION_DISABLED,
            "Automated final submission is disabled in this safety release.",
        )
    title = (
        _safe_text(page, "h1")
        or _safe_text(page, ".job-details-jobs-unified-top-card__job-title")
        or _safe_text(page, ".t-24.job-details-jobs-unified-top-card__job-title")
        or _safe_text(page, "div.job-details-jobs-unified-top-card__job-title")
    )
    company = (
        _safe_text(page, ".job-details-jobs-unified-top-card__company-name")
        or _safe_text(page, "a.job-details-jobs-unified-top-card__company-name")
        or _safe_text(page, ".job-details-jobs-unified-top-card__primary-description-container a")
    )
    url = normalize_job_url(page.url)
    # Wait briefly for top card to hydrate
    for _ in range(5):
        if title and company:
            break
        page.wait_for_timeout(400)
        title = title or _safe_text(page, "h1") or _safe_text(
            page, ".job-details-jobs-unified-top-card__job-title"
        )
        company = company or _safe_text(
            page, ".job-details-jobs-unified-top-card__company-name"
        ) or _safe_text(page, "a.job-details-jobs-unified-top-card__company-name")

    job_context = _safe_text(page, "#job-details") or _safe_text(page, ".jobs-description")

    if selected_mode == RunMode.LOCAL_PREVIEW:
        return ApplyResult(
            title,
            company,
            url,
            OutcomeStatus.PREVIEW_READY,
            "Inspected the job listing; the Apply control and application form were not opened.",
        )

    local_defaults = {
        **defaults,
        "_company": company or "the company",
        "_role": title or "the role",
    }

    # 1) Easy Apply if available (button or SDUI <a aria-label="Easy Apply…")
    easy = page.locator(
        "button.jobs-apply-button[aria-label*='Easy Apply' i], "
        "button[aria-label*='Easy Apply' i], "
        "a[aria-label*='Easy Apply' i], "
        "a[href*='openSDUIApplyFlow'], "
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button:has-text('Easy Apply'), "
        "button:has-text('Continue applying')"
    ).first
    has_easy = False
    try:
        has_easy = easy.count() > 0 and easy.is_visible(timeout=2000)
    except Exception:
        has_easy = False

    if has_easy:
        try:
            easy.click(timeout=4000)
            page.wait_for_timeout(1200)
        except Exception:
            has_easy = False

    if has_easy or _easy_apply_modal(page).count() or "/apply" in (page.url or "").lower():
        result = complete_easy_apply(
            page,
            profile=profile,
            defaults=local_defaults,
            cv_path=cv_path,
            job_context=job_context,
            title=title,
            company=company,
            url=url,
            use_ai=use_ai,
            dry_run=dry_run,
            log=log,
            allow_submit=allow_submit,
            accept_required_terms=accept_required_terms,
            mode=selected_mode,
            ai_policy=selected_ai_policy,
        )
        if has_easy:
            result.actions = ("open:easy-apply",) + result.actions
        return result

    # 2) External Apply (company website / ATS)
    from .external_apply import fill_generic_application_form, looks_like_signup_wall

    apply_btn = None
    for sel in APPLY_BUTTON_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=1200):
                apply_btn = loc
                break
        except Exception:
            continue
    if apply_btn is None:
        return ApplyResult(title, company, url, OutcomeStatus.UNSUPPORTED, "No supported Apply entry action found")

    context = page.context
    before_pages = list(context.pages)
    before_url = page.url
    external: Page | None = None
    try:
        with context.expect_page(timeout=10000) as new_page_info:
            apply_btn.click(timeout=4000)
        external = new_page_info.value
        external.wait_for_load_state("domcontentloaded", timeout=60000)
    except Exception:
        page.wait_for_timeout(3000)
        if _easy_apply_modal(page).count():
            result = complete_easy_apply(
                page,
                profile=profile,
                defaults=local_defaults,
                cv_path=cv_path,
                job_context=job_context,
                title=title,
                company=company,
                url=url,
                use_ai=use_ai,
                dry_run=dry_run,
                log=log,
                allow_submit=allow_submit,
                accept_required_terms=accept_required_terms,
                mode=selected_mode,
                ai_policy=selected_ai_policy,
            )
            result.actions = ("open:application-entry",) + result.actions
            return result
        if len(context.pages) > len(before_pages):
            external = context.pages[-1]
        elif page.url != before_url:
            external = page
        else:
            # Try linkedin offsite apply link if present
            offsite = page.locator("a[href*='externalApply'], a[data-tracking-control-name*='apply']").first
            try:
                if offsite.count() and offsite.is_visible(timeout=1000):
                    with context.expect_page(timeout=8000) as np2:
                        offsite.click()
                    external = np2.value
            except Exception:
                external = page

    assert external is not None
    try:
        external.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    _log(f"External apply page: {external.url}", log)
    if (
        selected_mode != RunMode.LOCAL_PREVIEW
        and looks_like_signup_wall(external)
        and account_credentials is None
    ):
        _log(f"*** PING: SIGNUP/LOGIN REQUIRED *** {external.url}", log)
        return ApplyResult(title, company, url, "needs_signup", f"Signup wall: {external.url}")

    resolved_credentials = account_credentials
    if (
        selected_mode != RunMode.LOCAL_PREVIEW
        and looks_like_signup_wall(external)
        and hasattr(account_credentials, "for_portal")
    ):
        try:
            resolved_credentials = account_credentials.for_portal(
                company or "the company",
                external.url,
            )
        except Exception as exc:
            return ApplyResult(
                title,
                company,
                url,
                "needs_signup",
                f"Employer credential preparation failed ({type(exc).__name__})",
            )

    detail = fill_generic_application_form(
        external,
        profile=profile,
        defaults=local_defaults,
        cv_path=cv_path,
        job_context=job_context,
        company=company or "the company",
        role=title or "the role",
        use_ai=use_ai,
        dry_run=dry_run,
        log=log,
        # Retained for call compatibility; every submission request is rejected.
        allow_submit=False if allow_submit is None else allow_submit,
        account_credentials=resolved_credentials,
        verification_client=verification_client,
        accept_required_terms=accept_required_terms,
        mode=selected_mode,
        ai_policy=selected_ai_policy,
    )
    return ApplyResult(
        title,
        company,
        url,
        detail.status,
        detail.detail,
        blockers=detail.blockers,
        actions=("open:external-apply",) + detail.actions,
        field_evidence=detail.field_evidence,
    )


def _collect_job_hrefs(page: Page, *, limit: int, log: LogFn | None) -> list[str]:
    """Scroll the results pane and collect unique job URLs."""
    hrefs: list[str] = []
    list_pane = page.locator(
        ".jobs-search-results-list, .scaffold-layout__list, div.jobs-search-results-list"
    ).first
    for _ in range(12):
        cards = page.locator(
            "a.job-card-container__link, "
            "div.job-card-container a[href*='/jobs/view/'], "
            "li.jobs-search-results__list-item a[href*='/jobs/view/'], "
            "a[href*='/jobs/view/']"
        )
        for i in range(cards.count()):
            try:
                href = cards.nth(i).get_attribute("href") or ""
                if not href:
                    continue
                href = normalize_job_url(href)
                if "/jobs/view/" in href and href not in hrefs:
                    hrefs.append(href)
            except Exception:
                continue
        if len(hrefs) >= limit:
            break
        try:
            if page.is_closed():
                break
            if list_pane.count():
                list_pane.evaluate("el => el.scrollBy(0, 900)")
            else:
                page.mouse.wheel(0, 1200)
        except Exception:
            try:
                if not page.is_closed():
                    page.evaluate("window.scrollBy(0, 900)")
            except Exception:
                break
        try:
            page.wait_for_timeout(800)
        except Exception:
            break
    _log(f"Collected {len(hrefs)} unique job links after scrolling", log)
    return hrefs[:limit]


def run_linkedin_auto_apply(
    *,
    keywords: str,
    location: str,
    cv_path: str,
    browser_data_dir: str,
    max_applications: int = 10,
    delay_seconds: float = 8,
    easy_apply_only: bool = True,
    headless: bool = False,
    dry_run: bool = True,
    use_ai: bool = True,
    defaults: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    output_dir: str | None = None,
    skip_urls: set[str] | None = None,
    keep_context_open: bool = False,
    existing_page: Page | None = None,
    log: LogFn | None = None,
    allow_submit: bool | None = None,
    account_credentials: Any | None = None,
    verification_client: Any | None = None,
    accept_required_terms: bool = False,
    mode: RunMode | str | None = None,
    ai_policy: AiPolicy | str | None = None,
) -> RunSummary:
    from .cleanup import cleanup_run_logs, merge_applied_from_summary, sanitize_summary_for_disk
    from .application_flow import missing_application_details

    profile = profile or load_profile()
    defaults = defaults or {}
    summary = RunSummary(searched_keywords=keywords, location=location)
    selected_mode = RunMode.parse(mode, dry_run=dry_run)
    selected_ai_policy = AiPolicy.parse(ai_policy)
    if allow_submit or selected_mode == RunMode.GUARDED_AUTO_SUBMIT:
        summary.results.append(
            ApplyResult(
                "",
                "",
                "",
                OutcomeStatus.SUBMISSION_DISABLED,
                "Automated final submission is disabled; no browser was opened.",
            )
        )
        return summary
    skip_urls = set(skip_urls or set())

    missing = missing_application_details(
        profile,
        cv_path,
        keywords=keywords,
        location=location,
        mode="linkedin",
    )
    if missing:
        detail = "Before browser automation, provide: " + "; ".join(missing)
        _log(f"*** APPLICATION PAUSED: {detail} ***", log)
        summary.results.append(ApplyResult("", "", "", "needs_info", detail))
        return summary

    Path(browser_data_dir).mkdir(parents=True, exist_ok=True)
    skip_urls = {normalize_job_url(u) for u in skip_urls}
    # easy_apply_only=False includes company-site Apply links too
    search_url = build_search_url(
        keywords,
        location,
        easy_apply_only=easy_apply_only,
        entry_level=True,
        past_week=True,
    )
    _log(f"Opening LinkedIn search: {search_url}", log)
    _log(
        "IMPORTANT: Log into LinkedIn in the opened Chromium window if prompted "
        "(this is a separate browser from the Cursor panel). "
        "Automated applying may violate LinkedIn's Terms of Service — use carefully.",
        log,
    )

    # Prefer CDP-attached owner Chromium — never launch a second profile (that closes/conflicts).
    from .browser_session import ensure_page, get_context

    page = existing_page
    if page is None:
        page = ensure_page(get_context(browser_data_dir, headless=headless))

    def _run_with_page(page: Page) -> RunSummary:
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)

        _log("Waiting for job results (log in manually if needed — up to ~3 min)...", log)
        for _ in range(90):
            if page.locator(
                "a.job-card-container__link, .jobs-search-results-list, a[href*='/jobs/view/']"
            ).count() > 0:
                break
            if "login" in page.url or "checkpoint" in page.url or "authwall" in page.url:
                _log("Login/checkpoint detected — complete it in the Chromium window...", log)
            page.wait_for_timeout(2000)

        hrefs = _collect_job_hrefs(page, limit=max(max_applications * 3, 30), log=log)
        hrefs = [h for h in hrefs if normalize_job_url(h) not in skip_urls]
        _log(f"{len(hrefs)} new jobs to try (skipping {len(skip_urls)} already-seen)", log)

        applied = 0
        for href in hrefs:
            if applied >= max_applications:
                break
            href = normalize_job_url(href)
            try:
                # Stay on search UI — direct /jobs/view often has no button.jobs-apply-button
                open_job_detail(page, href, search_url=search_url)
                page.wait_for_timeout(800)
                if selected_mode != RunMode.LOCAL_PREVIEW:
                    _dismiss_modals(page)
                # Narrow already-applied detection (avoid false positives)
                applied_badge = page.locator(
                    ".jobs-unified-top-card__applied-state, "
                    "li.job-details-jobs-unified-top-card__job-insight--highlight:has-text('Applied')"
                ).first
                try:
                    if applied_badge.count() and applied_badge.is_visible(timeout=700):
                        txt = (applied_badge.inner_text(timeout=500) or "").strip().lower()
                        if "applied" in txt:
                            summary.results.append(
                                ApplyResult("", "", href, "skipped", "Already applied")
                            )
                            skip_urls.add(href)
                            continue
                except Exception:
                    pass

                result = apply_to_current_job(
                    page,
                    profile=profile,
                    defaults=defaults,
                    cv_path=cv_path,
                    use_ai=use_ai,
                    dry_run=dry_run,
                    log=log,
                    allow_submit=allow_submit,
                    account_credentials=account_credentials,
                    verification_client=verification_client,
                    accept_required_terms=accept_required_terms,
                    mode=selected_mode,
                    ai_policy=selected_ai_policy,
                )
                # Close only leftover LinkedIn tabs — never close company ATS signup tabs
                for extra in list(page.context.pages)[1:]:
                    try:
                        u = (extra.url or "").lower()
                        if "linkedin.com" in u and "jobs" not in u:
                            extra.close()
                    except Exception:
                        pass

                summary.results.append(result)
                _log(f"[{result.status}] {result.title} @ {result.company} — {result.detail}", log)
                if result.status in {
                    OutcomeStatus.PREVIEW_READY,
                    OutcomeStatus.REVIEW_READY,
                    OutcomeStatus.SUBMITTED_CONFIRMED,
                }:
                    applied += 1
                    skip_urls.add(href)
                elif result.status == OutcomeStatus.NEEDS_AUTHENTICATION:
                    skip_urls.add(href)  # don't loop forever; queued for manual
                time.sleep(delay_seconds)
            except Exception as exc:
                summary.results.append(ApplyResult("", "", href, "error", str(exc)))
                _log(f"Error on {href}: {exc}", log)

        if output_dir:
            from .cleanup import append_needs_review_queue
            from .config import ROOT

            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out / f"linkedin_run_{stamp}.json"
            payload = sanitize_summary_for_disk(summary.to_dict())
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            hist = ROOT / "data" / "applied_history.json"
            merge_applied_from_summary(summary.to_dict(), hist)
            qpath = append_needs_review_queue(out, payload["results"])
            removed = cleanup_run_logs(out, keep=3)
            _log(f"Saved run log: {path}; queue: {qpath} (cleaned {len(removed)} old files)", log)

        return summary

    assert page is not None
    summary = _run_with_page(page)
    # Never context.close() here — owner (open_browser.py) owns Chromium lifetime.
    return summary


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:60] or "item"
