from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from playwright.sync_api import Page

from .answers import answer_question
from .application_models import (
    AiPolicy,
    FieldEvidence,
    FillOutcome,
    OutcomeStatus,
    RunMode,
    submission_disabled_outcome,
)
from .ats_adapters import AtsAdapter, detect_ats
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
from .submission_guard import (
    ai_policy_prohibited,
    attestation_blockers,
    verify_application_ready,
)
from .work_experience import fill_repeatable_work_experience

if TYPE_CHECKING:
    from .account_automation import GmailBrowserVerifier, PortalCredentials

LogFn = Callable[[str], None]


def _log(msg: str, log: LogFn | None) -> None:
    if log:
        log(msg)
    else:
        print(msg)


def _page_body_text(page: Page) -> str:
    try:
        return (page.locator("body").inner_text(timeout=1500) or "").lower()[:12000]
    except Exception:
        return ""


def looks_like_signup_wall(page: Page) -> bool:
    """Use strong authentication signals rather than marketing-page wording."""
    try:
        url = (page.url or "").lower()
        if "linkedin.com" in url:
            return False
        if any(x in url for x in ("/login", "/signin", "/sign-in", "/register", "/signup", "/sign-up", "/auth")):
            if page.locator("input[type='password']").count() > 0:
                return True
        if page.locator("input[type='password']").count() == 0:
            return False
        text = _page_body_text(page)
        return any(
            phrase in text
            for phrase in (
                "create an account",
                "create account",
                "sign up to apply",
                "register to apply",
                "new candidate",
                "don't have an account",
                "do not have an account",
            )
        )
    except Exception:
        return False


def wait_for_manual_login(
    page: Page,
    *,
    timeout_seconds: int = 300,
    log: LogFn | None = None,
) -> bool:
    """Wait for the user to sign in to an external ATS in the open browser."""
    if not looks_like_signup_wall(page):
        return True
    _log(
        "External application requires login. Please sign in in the open browser; "
        f"waiting up to {timeout_seconds // 60} minutes.",
        log,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                return False
            if not looks_like_signup_wall(page):
                _log("External login detected; continuing with safe form filling.", log)
                return True
            page.wait_for_timeout(2000)
        except Exception:
            return False
    return False


def _click_progress(
    page: Page,
    *,
    allow_submit: bool = False,
    adapter: AtsAdapter | None = None,
) -> str | None:
    """Click only an exact, adapter-declared, non-submit progress control."""
    if allow_submit:
        return None
    # Selector profiles are not proof that a portal action cannot transmit.
    # Navigation is supported by the capability-qualified foreground runtime.
    return None


def _manifest_or_outcome(
    page: Page,
    adapter: AtsAdapter,
) -> tuple[tuple[ManifestField, ...], FillOutcome | None]:
    try:
        return extract_field_manifest(page, root_selector=adapter.root_selector), None
    except Exception as exc:
        return (), FillOutcome(
            OutcomeStatus.FAILED_RETRYABLE,
            f"Field manifest extraction failed ({type(exc).__name__}).",
            blockers=("The form was not changed because its fields could not be inspected.",),
        )


def _narrative_answer(
    field: ManifestField,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    company: str,
    role: str,
    job_context: str,
    ai_enabled: bool,
) -> tuple[str, str]:
    if not ai_enabled:
        return "", ""
    if field.kind == FieldKind.COVER_LETTER:
        return (
            generate_cover_letter(
                profile,
                company=company or "the company",
                role=role or "the role",
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


def _unresolved_status(blockers: list[str]) -> OutcomeStatus:
    if any("custom dropdown" in blocker.lower() for blocker in blockers):
        return OutcomeStatus.UNSUPPORTED
    return OutcomeStatus.NEEDS_INFORMATION


def fill_generic_application_form(
    page: Page,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    cv_path: str,
    job_context: str,
    company: str,
    role: str,
    use_ai: bool,
    dry_run: bool,
    log: LogFn | None,
    allow_submit: bool = False,
    account_credentials: PortalCredentials | None = None,
    verification_client: GmailBrowserVerifier | None = None,
    accept_required_terms: bool = False,
    mode: RunMode | str | None = None,
    ai_policy: AiPolicy | str | None = None,
) -> FillOutcome:
    """Inspect first, then fill confirmed values; never perform final submission."""
    selected_mode = RunMode.parse(mode, dry_run=dry_run)
    selected_ai_policy = AiPolicy.parse(ai_policy)
    if allow_submit or selected_mode == RunMode.GUARDED_AUTO_SUBMIT:
        return submission_disabled_outcome()

    page.wait_for_timeout(300)
    page_text = _page_body_text(page)
    adapter = detect_ats(page.url, page_text)
    _log(f"Detected ATS adapter: {adapter.name}", log)
    policy_prohibited = ai_policy_prohibited(f"{job_context}\n{page_text}")
    ai_enabled = bool(
        use_ai
        and selected_ai_policy == AiPolicy.ALLOWED
        and not policy_prohibited
    )
    policy_conflict = bool(use_ai and selected_ai_policy == AiPolicy.ALLOWED and policy_prohibited)

    initial_manifest, manifest_error = _manifest_or_outcome(page, adapter)
    if manifest_error:
        return manifest_error
    if selected_mode == RunMode.LOCAL_PREVIEW:
        evidence = tuple(field.evidence(resolution="observed") for field in initial_manifest)
        if policy_conflict:
            return FillOutcome(
                OutcomeStatus.POLICY_BLOCKED,
                "The employer page prohibits AI assistance; no fields or uploads were changed.",
                blockers=("Employer AI policy conflicts with the requested AI setting.",),
                field_evidence=evidence,
            )
        return FillOutcome(
            OutcomeStatus.PREVIEW_READY,
            f"Inspected {len(initial_manifest)} fields; no portal values, uploads, or controls were changed.",
            field_evidence=evidence,
        )
    if policy_conflict:
        return FillOutcome(
            OutcomeStatus.POLICY_BLOCKED,
            "The employer page prohibits AI assistance; no fields, uploads, or account actions were changed.",
            blockers=("Employer AI policy conflicts with the requested AI setting.",),
            field_evidence=tuple(
                field.evidence(resolution="observed") for field in initial_manifest
            ),
        )
    if not initial_manifest:
        return FillOutcome(
            OutcomeStatus.UNSUPPORTED,
            "No application fields were detected; no Apply or Continue action was guessed.",
        )

    def ensure_access() -> FillOutcome | None:
        if not looks_like_signup_wall(page):
            return None
        if account_credentials is not None:
            from .account_automation import ensure_portal_access

            try:
                access = ensure_portal_access(
                    page,
                    credentials=account_credentials,
                    company=company,
                    verifier=verification_client,
                    accept_required_terms=accept_required_terms,
                    log=log,
                )
            except Exception as exc:
                return FillOutcome(
                    OutcomeStatus.NEEDS_AUTHENTICATION,
                    f"Account automation failed ({type(exc).__name__}).",
                )
            if access.status == "ready":
                return None
            return FillOutcome(OutcomeStatus.NEEDS_AUTHENTICATION, access.detail)
        return FillOutcome(
            OutcomeStatus.NEEDS_AUTHENTICATION,
            "Login or registration is required; the application remains resumable.",
        )

    access_error = ensure_access()
    if access_error:
        return access_error

    actions: list[str] = []
    all_evidence: list[FieldEvidence] = []
    carried_blockers: list[str] = []

    for step in range(8):
        access_error = ensure_access()
        if access_error:
            return access_error
        page_text = _page_body_text(page)
        if ai_policy_prohibited(f"{job_context}\n{page_text}"):
            if selected_ai_policy == AiPolicy.ALLOWED and use_ai:
                policy_conflict = True
            ai_enabled = False
        if policy_conflict:
            return FillOutcome(
                OutcomeStatus.POLICY_BLOCKED,
                "A later form step prohibits AI assistance; that step was not changed.",
                blockers=("Employer AI policy conflicts with the requested AI setting.",),
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )

        manifest, manifest_error = _manifest_or_outcome(page, adapter)
        if manifest_error:
            return manifest_error
        step_blockers: list[str] = []

        experience_report = fill_repeatable_work_experience(page, profile=profile, log=log)
        if experience_report.attempted:
            step_blockers.extend(experience_report.blockers)
            manifest, manifest_error = _manifest_or_outcome(page, adapter)
            if manifest_error:
                return manifest_error
        root = page.locator(adapter.root_selector).first
        controls = root.locator(CONTROL_SELECTOR)

        for field in manifest:
            if (not field.visible and field.input_type != "file") or field.disabled:
                all_evidence.append(field.evidence(resolution="ignored"))
                continue
            if field.has_observed_value:
                all_evidence.append(field.evidence(resolution="preserved", source="portal.observed"))
                continue
            control = controls.nth(field.index)

            if field.kind == FieldKind.CUSTOM_DROPDOWN:
                if field.required:
                    step_blockers.append(
                        f"Custom dropdown is unsupported: {field.question or field.field_id}"
                    )
                all_evidence.append(field.evidence(resolution="unsupported"))
                continue

            if field.input_type == "file":
                if field.kind == FieldKind.CV_UPLOAD:
                    try:
                        control.set_input_files(cv_path)
                        all_evidence.append(field.evidence(resolution="filled", source="approved.cv"))
                    except Exception as exc:
                        step_blockers.append(
                            f"CV upload failed for {field.question or field.field_id} ({type(exc).__name__})"
                        )
                        all_evidence.append(field.evidence(resolution="blocked"))
                else:
                    if field.required:
                        step_blockers.append(
                            f"Required {field.kind.value.replace('_', ' ')} needs an approved document"
                        )
                    all_evidence.append(field.evidence(resolution="unresolved"))
                continue

            proposal = resolve_deterministic_answer(
                field,
                profile=profile,
                defaults=defaults,
            )
            value, source = proposal.value, proposal.source
            if not value:
                value, source = _narrative_answer(
                    field,
                    profile=profile,
                    defaults=defaults,
                    company=company,
                    role=role,
                    job_context=job_context,
                    ai_enabled=ai_enabled,
                )
            if not value:
                if proposal.blocker:
                    step_blockers.append(proposal.blocker)
                elif field.required:
                    step_blockers.append(
                        f"Required field is unresolved: {field.question or field.field_id}"
                    )
                all_evidence.append(field.evidence(resolution="unresolved"))
                continue

            limit_blocker = answer_limit_blocker(field, value)
            if limit_blocker:
                step_blockers.append(limit_blocker)
                all_evidence.append(field.evidence(resolution="over_limit", source=source))
                continue

            try:
                if field.tag == "select":
                    choice = exact_option(field.options, value)
                    if not choice:
                        step_blockers.append(
                            f"Confirmed answer has no exact option for {field.question or field.field_id}"
                        )
                        all_evidence.append(field.evidence(resolution="unresolved", source=source))
                        continue
                    control.select_option(label=choice)
                elif field.input_type == "checkbox":
                    if value.strip().lower() in {"yes", "true", "checked", "agree"}:
                        control.check()
                    elif value.strip().lower() in {"no", "false", "unchecked", "decline"}:
                        if field.required:
                            step_blockers.append(
                                f"Required checkbox was explicitly declined: {field.question or field.field_id}"
                            )
                        all_evidence.append(field.evidence(resolution="explicit_unchecked", source=source))
                        continue
                    else:
                        step_blockers.append(
                            f"Candidate must confirm the checkbox: {field.question or field.field_id}"
                        )
                        all_evidence.append(field.evidence(resolution="unresolved", source=source))
                        continue
                elif field.input_type == "radio":
                    if field.observed_value and exact_option((field.observed_value,), value):
                        control.check()
                    else:
                        all_evidence.append(field.evidence(resolution="unresolved", source=source))
                        continue
                else:
                    control.fill(value)
                all_evidence.append(field.evidence(resolution="filled", source=source))
            except Exception as exc:
                step_blockers.append(
                    f"Could not fill {field.question or field.field_id} ({type(exc).__name__})"
                )
                all_evidence.append(field.evidence(resolution="blocked", source=source))

        verification = verify_application_ready(
            page,
            profile=profile,
            cv_path=cv_path,
            root_selector=adapter.root_selector,
        )
        step_blockers.extend(verification.blockers)
        step_blockers.extend(attestation_blockers(page_text))
        carried_blockers.extend(step_blockers)
        carried_blockers = list(dict.fromkeys(carried_blockers))

        if policy_conflict:
            return FillOutcome(
                OutcomeStatus.POLICY_BLOCKED,
                "Employer AI guidance conflicts with the requested drafting policy; AI fields were left untouched.",
                blockers=tuple(carried_blockers)
                + ("Employer AI policy conflicts with the requested AI setting.",),
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        if step_blockers:
            return FillOutcome(
                _unresolved_status(step_blockers),
                "The application paused with unresolved fields.",
                blockers=tuple(carried_blockers),
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )

        submit_button = page.locator(adapter.submit_selector).first
        try:
            if submit_button.count() and submit_button.is_visible(timeout=500):
                return FillOutcome(
                    OutcomeStatus.REVIEW_READY,
                    "Application filled to final review; final submission is left to the candidate.",
                    actions=tuple(actions),
                    field_evidence=tuple(all_evidence),
                )
        except Exception:
            pass

        clicked = _click_progress(page, adapter=adapter)
        if not clicked:
            return FillOutcome(
                OutcomeStatus.INCOMPLETE,
                "No verified non-final action remains; open the runtime to resume with a supported adapter.",
                actions=tuple(actions),
                field_evidence=tuple(all_evidence),
            )
        actions.append(clicked)
        _log(f"External step {step + 1}: clicked {clicked}", log)
        page.wait_for_timeout(1000)

    return FillOutcome(
        OutcomeStatus.INCOMPLETE,
        "Step limit reached; application completion has not been verified.",
        actions=tuple(actions),
        field_evidence=tuple(all_evidence),
    )


def _submission_confirmed(body: str) -> bool:
    """Generic text or modal state can never prove an external submission."""
    return False
