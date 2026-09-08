from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from playwright.sync_api import Page

from .answers import answer_question
from .cover_letter import generate_cover_letter
from .profile import load_profile
from .submission_guard import (
    ai_rewrite_required,
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
    status: str
    detail: str = ""


@dataclass
class RunSummary:
    searched_keywords: str
    location: str
    results: list[ApplyResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "searched_keywords": self.searched_keywords,
            "location": self.location,
            "results": [asdict(r) for r in self.results],
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


def _fill_text_inputs(
    page: Page,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    *,
    job_context: str,
    use_ai: bool,
    log: LogFn | None,
) -> None:
    inputs = page.locator(
        "input[type='text'], input[type='email'], input[type='tel'], "
        "input:not([type]), textarea"
    )
    count = inputs.count()
    for i in range(count):
        el = inputs.nth(i)
        try:
            if not el.is_visible(timeout=500):
                continue
            input_type = (el.get_attribute("type") or "text").lower()
            if input_type in {"hidden", "file", "checkbox", "radio", "submit", "button"}:
                continue
            if el.is_disabled():
                continue

            label = ""
            el_id = el.get_attribute("id")
            if el_id:
                label = _safe_text(page, f"label[for='{el_id}']")
            if not label:
                label = el.get_attribute("aria-label") or el.get_attribute("placeholder") or ""
            if not label:
                try:
                    label = el.evaluate(
                        """(node) => {
                          const l = node.closest('div,fieldset,li,label')?.querySelector('label,span,p');
                          return l ? l.innerText : '';
                        }"""
                    )
                except Exception:
                    label = ""

            label = (label or "").strip()
            if not label:
                continue
            label_l = label.lower()
            current = (el.input_value() or "").strip()

            # Always correct identity fields — LinkedIn often swaps first/last
            if "middle name" in label_l or "middle initial" in label_l:
                want = (profile.get("middle_name") or "").strip()
                if current != want:
                    el.fill(want)
                    _log(f"Set middle name → {want!r}", log)
                continue
            if label_l.startswith("first name") or label_l == "first name":
                want = (profile.get("first_name") or "").strip()
                if want and current != want:
                    el.fill(want)
                    _log(f"Corrected first name → {want}", log)
                continue
            if label_l.startswith("last name") or label_l == "last name" or "surname" in label_l:
                want = (profile.get("last_name") or "").strip()
                if want and current != want:
                    el.fill(want)
                    _log(f"Corrected last name → {want}", log)
                continue

            # Location typeahead — must pick a suggestion, not free-text
            if "location" in label_l or (label_l == "city" or "city)" in label_l):
                errs = _easy_apply_has_errors(page)
                needs_fix = any("valid" in e.lower() for e in errs) or not current
                if not needs_fix:
                    continue
                city = (
                    str(defaults.get("location", "")).strip()
                    or str(profile.get("location", "")).strip()
                )
                if not city:
                    _log("Location field left blank — target location is not configured", log)
                    continue
                try:
                    el.click(timeout=1000)
                    el.fill("")
                    el.type(city, delay=60)
                    page.wait_for_timeout(900)
                    opt = page.locator(
                        "[role='listbox'] [role='option'], "
                        ".basic-typeahead__selectable, "
                        ".search-typeahead-v2__hit"
                    ).first
                    if opt.count() and opt.is_visible(timeout=1500):
                        opt.click(timeout=2000)
                        _log(f"Picked location typeahead: {city}", log)
                    else:
                        page.keyboard.press("ArrowDown")
                        page.keyboard.press("Enter")
                        _log(f"Location keyboard select: {city}", log)
                except Exception as exc:
                    _log(f"Location typeahead skip: {exc}", log)
                continue

            if current:
                continue

            # Cover letter / longer text
            tag = el.evaluate("n => n.tagName.toLowerCase()")
            if tag == "textarea" or "cover" in label_l or "why" in label_l:
                company = defaults.get("_company", "the company")
                role = defaults.get("_role", "the role")
                text = generate_cover_letter(
                    profile,
                    company=company,
                    role=role,
                    location=defaults.get("location", ""),
                    job_description=job_context,
                    use_ai=use_ai,
                )
                el.fill(text[:4500])
                _log(f"Filled long-text field: {label[:60]}", log)
                continue

            ans = answer_question(
                label,
                profile,
                job_context=job_context,
                defaults=defaults,
                use_ai=use_ai,
            )
            if not (ans or "").strip():
                _log(f"Left blank (unknown — will PING if required): {label[:60]}", log)
                continue
            el.fill(ans[:500])
            _log(f"Filled '{label[:50]}' → {ans[:80]}", log)
        except Exception as exc:
            _log(f"Skip input #{i}: {exc}", log)


def _handle_file_uploads(page: Page, cv_path: str, log: LogFn | None) -> None:
    file_inputs = page.locator("input[type='file']")
    n = file_inputs.count()
    for i in range(n):
        el = file_inputs.nth(i)
        try:
            el.set_input_files(cv_path)
            _log(f"Uploaded CV to file input #{i+1}", log)
        except Exception as exc:
            _log(f"Could not upload CV on input #{i+1}: {exc}", log)


def _field_context(el) -> str:
    try:
        return (
            el.evaluate(
                """(node) => {
                  const root = node.closest('fieldset,div,li,label,form') || node.parentElement;
                  return (root && root.innerText) ? root.innerText.slice(0, 280) : '';
                }"""
            )
            or ""
        ).lower()
    except Exception:
        return ""


def _wanted_yes_no(
    context: str,
    profile: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> str | None:
    """Return Yes/No only for an explicitly configured question; None if unknown."""
    c = context.lower()
    profile = profile or {}
    defaults = defaults or {}
    answers = profile.get("answers", {}) or {}

    def configured(*keys: str) -> str | None:
        for key in keys:
            value = defaults.get(key) or answers.get(key)
            if str(value).strip().lower() in {"yes", "no"}:
                return str(value).strip().title()
        return None

    if any(
        k in c
        for k in (
            "driving licence",
            "driving license",
            "driver's licence",
            "driver's license",
            "full driving",
            "hold a licence",
            "hold a license",
        )
    ):
        return configured("has_driving_licence", "driving_licence")
    if any(k in c for k in ("sponsor", "sponsorship", "visa sponsorship", "require a visa")):
        return configured("require_sponsorship")
    if any(
        k in c
        for k in (
            "authorized to work",
            "authorised to work",
            "right to work",
            "eligible to work",
            "legally authorised",
            "legally authorized",
            "work authorization",
        )
    ):
        value = configured("authorized_to_work", "work_authorization", "right_to_work")
        return value
    if any(k in c for k in ("commute", "relocate", "hybrid", "willing to")):
        return configured("willing_to_commute", "willing_to_relocate")
    return None


def _select_sensible_options(
    page: Page,
    log: LogFn | None,
    *,
    profile: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> None:
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        try:
            if not sel.is_visible(timeout=400):
                continue
            # Skip if already chosen a real value
            try:
                cur = (sel.input_value() or "").strip()
                if cur and cur.lower() not in {"", "select an option", "0"}:
                    # still allow overwrite only for empty-looking placeholders
                    opt_txt = ""
                    try:
                        opt_txt = sel.locator("option:checked").inner_text(timeout=200)
                    except Exception:
                        opt_txt = cur
                    if opt_txt and "select" not in opt_txt.lower():
                        continue
            except Exception:
                pass

            options = sel.locator("option")
            texts = [options.nth(j).inner_text().strip() for j in range(options.count())]
            context = _field_context(sel)
            choice = None
            wanted = _wanted_yes_no(context, profile, defaults)
            if wanted and wanted in texts:
                choice = wanted
            elif any(k in context for k in ("disability", "veteran", "gender", "race", "ethnicity")):
                for preferred in ("Prefer not to say", "I am not a protected veteran", "No"):
                    if preferred in texts:
                        choice = preferred
                        break
            elif "email" in context and profile:
                email = str(profile.get("email", "")).strip()
                choice = next((t for t in texts if t.strip() == email), None)
            elif ("phone" in context or "country" in context) and profile:
                country = str(profile.get("phone_country", "")).strip().lower()
                choice = next((t for t in texts if t.strip().lower() == country), None)
            if not choice:
                # Do NOT blindly pick Yes — only safe defaults
                for preferred in ("Prefer not to say", "I am not a protected veteran"):
                    if preferred in texts:
                        choice = preferred
                        break
            if choice:
                sel.select_option(label=choice)
                _log(f"Selected dropdown: {choice}", log)
        except Exception:
            continue

    # Radio groups — label-aware Yes/No (licence=No, sponsorship=No, work rights=Yes)
    radios = page.locator("input[type='radio']")
    seen_names: set[str] = set()
    for i in range(radios.count()):
        r = radios.nth(i)
        try:
            if not r.is_visible(timeout=300):
                continue
            name = r.get_attribute("name") or f"anon-{i}"
            if name in seen_names:
                continue
            context = _field_context(r)

            if any(k in context for k in ("disability", "veteran", "gender", "race", "ethnicity")):
                prefer = page.locator(f"input[type='radio'][name='{name}']")
                picked = False
                for j in range(prefer.count()):
                    val = (prefer.nth(j).get_attribute("value") or "").lower()
                    label_txt = ""
                    try:
                        label_txt = prefer.nth(j).evaluate(
                            "n => (n.closest('label')||n.parentElement)?.innerText || ''"
                        )
                    except Exception:
                        pass
                    if "prefer not" in (label_txt or "").lower() or "prefer not" in val:
                        prefer.nth(j).check(force=True)
                        picked = True
                        break
                seen_names.add(name)
                if picked:
                    _log(f"Radio '{name}': Prefer not to say", log)
                continue

            value_wanted = _wanted_yes_no(context, profile, defaults)
            if not value_wanted:
                seen_names.add(name)
                _log(f"Left unknown radio group '{name}' for user review", log)
                continue
            target = page.locator(
                f"input[type='radio'][name='{name}'][value='{value_wanted}']"
            ).first
            if target.count() == 0:
                group = page.locator(f"input[type='radio'][name='{name}']")
                for j in range(group.count()):
                    label_txt = ""
                    try:
                        label_txt = group.nth(j).evaluate(
                            "n => (n.closest('label')||n.parentElement)?.innerText || ''"
                        )
                    except Exception:
                        pass
                    if (label_txt or "").strip().lower() == value_wanted.lower():
                        group.nth(j).check(force=True)
                        seen_names.add(name)
                        _log(f"Radio via label: {value_wanted} ({context[:50]})", log)
                        break
            else:
                target.check(force=True)
                seen_names.add(name)
                _log(f"Radio '{name}' → {value_wanted}", log)
        except Exception:
            continue


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
    return page.locator(
        ".jobs-easy-apply-modal, .jobs-easy-apply-content, div[aria-labelledby*='jobs-apply']"
    ).first


def _click_modal_button(
    page: Page,
    labels: list[str],
    *,
    timeout: float = 3500,
    force: bool = False,
) -> str | None:
    """Click a footer button inside Easy Apply (partial aria-label / text match)."""
    modal = _easy_apply_modal(page)
    # Prefer footer so we don't click header/nav buttons
    roots = []
    if modal.count():
        footer = modal.locator(
            "footer, .jobs-easy-apply-footer, div[class*='easy-apply-modal__footer'], "
            ".jobs-easy-apply-modal__footer"
        ).first
        if footer.count():
            roots.append(footer)
        roots.append(modal)
    roots.append(page)

    for label in labels:
        for root in roots:
            candidates = [
                root.locator(f"button[aria-label*='{label}' i]").first,
                root.locator(f"button:has-text('{label}')").first,
            ]
            for loc in candidates:
                try:
                    if not (loc.count() and loc.is_visible(timeout=500)):
                        continue
                    # Avoid Discard / Save while advancing
                    aria = (loc.get_attribute("aria-label") or "").lower()
                    text = (loc.inner_text() or "").lower()
                    blob = aria + " " + text
                    if any(bad in blob for bad in ("discard", "delete", "save for later")):
                        continue
                    # Skip disabled unless force (LinkedIn sometimes keeps Next enabled visually)
                    try:
                        disabled = loc.is_disabled(timeout=300)
                    except Exception:
                        disabled = False
                    if disabled and not force:
                        continue
                    loc.scroll_into_view_if_needed(timeout=1000)
                    loc.click(timeout=timeout, force=force)
                    return label
                except Exception:
                    continue
    return None


def _unanswered_required_hints(page: Page) -> list[str]:
    """Best-effort list of empty required fields blocking Next/Submit."""
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
    profile: dict[str, Any],
    local_defaults: dict[str, Any],
    cv_path: str,
    job_context: str,
    use_ai: bool,
    log: LogFn | None,
) -> None:
    _handle_file_uploads(page, cv_path, log)
    _fill_text_inputs(
        page,
        profile,
        local_defaults,
        job_context=job_context,
        use_ai=use_ai,
        log=log,
    )
    _select_sensible_options(page, log, profile=profile, defaults=local_defaults)
    _check_required_consent_boxes(page, log)
    # Uncheck follow company if present (optional)
    try:
        follow = page.locator("label:has-text('Follow'), input[id*='follow-company']").first
        if follow.count() and follow.is_visible(timeout=400):
            pass
    except Exception:
        pass


def _check_required_consent_boxes(
    page: Page,
    log: LogFn | None,
    *,
    accept_required_terms: bool = False,
) -> None:
    """Handle required terms only when the user explicitly allowed that policy."""
    modal = _easy_apply_modal(page)
    if not modal.count():
        return
    boxes = modal.locator("input[type='checkbox']")
    for i in range(boxes.count()):
        try:
            box = boxes.nth(i)
            if not box.is_visible(timeout=300) or box.is_checked():
                continue
            text = box.evaluate(
                "n => (n.closest('label,div,fieldset')?.innerText || '').slice(0,500)"
            ).lower()
            can_accept = (
                accept_required_terms
                and any(term in text for term in ("terms", "privacy", "data processing", "acknowledge"))
                and not any(term in text for term in ("marketing", "newsletter", "job alerts"))
            )
            if can_accept:
                box.check(timeout=3000)
                _log(f"Accepted required application terms checkbox #{i+1}", log)
            else:
                _log(f"Left consent/policy checkbox #{i+1} unchecked for user review", log)
        except Exception:
            continue


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
) -> ApplyResult:
    """
    Full Easy Apply wizard:
    fill → Next/Continue (repeat) → Review → Submit application.
    On unknown required fields: leave modal open and return needs_info (PING).
    """
    submit_enabled = (not dry_run) if allow_submit is None else bool(allow_submit)
    rewrite_required = ai_rewrite_required(job_context, use_ai=use_ai)
    local_defaults = {
        **defaults,
        "_company": company or "the company",
        "_role": title or "the role",
    }
    page.wait_for_timeout(1000)
    _dismiss_modals(page)

    advance_labels = [
        "Review your application",
        "Review",
        "Continue to next step",
        "Next",
        "Continue",
    ]
    submit_labels = ["Submit application", "Submit Application"]

    for step in range(16):
        modal = _easy_apply_modal(page)
        if modal.count() == 0:
            if page.locator("text=Application sent").count() or page.locator(
                "text=Your application was sent"
            ).count():
                return ApplyResult(title, company, url, "applied", "Application sent confirmation")
            if step == 0:
                return ApplyResult(title, company, url, "needs_review", "Easy Apply modal not open")
            break

        _log(f"Easy Apply step {step + 1}: filling fields…", log)
        _fill_easy_apply_step(
            page,
            profile=profile,
            local_defaults=local_defaults,
            cv_path=cv_path,
            job_context=job_context,
            use_ai=use_ai,
            log=log,
        )
        page.wait_for_timeout(600)
        try:
            modal_text = modal.inner_text(timeout=1000) or ""
        except Exception:
            modal_text = ""
        rewrite_required = rewrite_required or ai_rewrite_required(
            f"{job_context}\n{modal_text}",
            use_ai=use_ai,
        )
        declarations = attestation_blockers(modal_text)
        can_submit = submit_enabled and not rewrite_required and not declarations

        if dry_run:
            _click_modal_button(page, ["Discard", "Cancel"])
            _click_if_visible(page, ["button[aria-label='Dismiss']"])
            return ApplyResult(title, company, url, "dry_run", f"Filled through step {step + 1}")

        # 1) Submit when available — only if every required field is resolved.
        if _visible_submit_candidate(page):
            _check_required_consent_boxes(
                page,
                log,
                accept_required_terms=accept_required_terms and can_submit,
            )
            hints = _unanswered_required_hints(page)
            if hints:
                detail = "NEED INFO (modal left open): " + "; ".join(hints[:4])
                _log(f"*** PING: {detail} ***", log)
                return ApplyResult(title, company, url, "needs_info", detail)
            verification = verify_application_ready(
                page,
                profile=profile,
                cv_path=cv_path,
                root_selector=".jobs-easy-apply-modal, [role='dialog'], body",
            )
            if verification.blockers:
                detail = "NEED INFO (modal left open): " + "; ".join(
                    verification.blockers[:4]
                )
                return ApplyResult(title, company, url, "needs_info", detail)
            if rewrite_required:
                return ApplyResult(
                    title,
                    company,
                    url,
                    "needs_review",
                    "AI-assisted draft answers populated; employer AI guidance requires user rewrite; Submit locked",
                )
            if declarations:
                return ApplyResult(
                    title,
                    company,
                    url,
                    "needs_review",
                    "; ".join(declarations) + "; Submit left for user",
                )
            if not submit_enabled:
                return ApplyResult(
                    title,
                    company,
                    url,
                    "needs_review",
                    "Application filled to final review; Submit left for user",
                )
        submitted = _click_modal_button(page, submit_labels) if can_submit else None
        if not submitted:
            try:
                sub = page.locator(
                    "button[data-live-test-easy-apply-submit-button], "
                    "button[aria-label='Submit application']"
                ).first
                if can_submit and sub.count() and sub.is_visible(timeout=800):
                    # Clear discard overlay if present (Cancel = keep app)
                    _dismiss_modals(page)
                    sub.click(timeout=5000, force=True)
                    submitted = "Submit application"
            except Exception:
                pass
        if submitted:
            page.wait_for_timeout(2000)
            if page.locator("text=Application sent").count() or page.locator(
                "text=Your application was sent"
            ).count() or _easy_apply_modal(page).count() == 0:
                _click_modal_button(page, ["Done"])
                _click_if_visible(page, ["button[aria-label='Dismiss']", "button:has-text('Done')"])
                _log("Submitted Easy Apply", log)
                return ApplyResult(title, company, url, "applied", f"Submitted after step {step + 1}")
            errs = _unanswered_required_hints(page)
            if errs:
                _log(f"Submit blocked by errors: {errs}", log)
                _fill_easy_apply_step(
                    page,
                    profile=profile,
                    local_defaults=local_defaults,
                    cv_path=cv_path,
                    job_context=job_context,
                    use_ai=use_ai,
                    log=log,
                )
                submitted2 = _click_modal_button(page, submit_labels) if can_submit else None
                if submitted2:
                    page.wait_for_timeout(1800)
                    if page.locator("text=Application sent").count() or _easy_apply_modal(page).count() == 0:
                        _click_modal_button(page, ["Done"])
                        return ApplyResult(title, company, url, "applied", "Submitted after fixing errors")
                hints = _unanswered_required_hints(page) or errs
                detail = "NEED INFO (modal left open): " + "; ".join(hints[:4])
                _log(f"*** PING: {detail} ***", log)
                return ApplyResult(title, company, url, "needs_info", detail)

        # 2) Advance: Review / Next / Continue
        advanced = _click_modal_button(page, advance_labels)
        if not advanced:
            hints = _unanswered_required_hints(page)
            _log("No Next/Submit — retry fill once" + (f" (hints: {hints})" if hints else ""), log)
            _fill_easy_apply_step(
                page,
                profile=profile,
                local_defaults=local_defaults,
                cv_path=cv_path,
                job_context=job_context,
                use_ai=use_ai,
                log=log,
            )
            page.wait_for_timeout(600)
            submitted = _click_modal_button(page, submit_labels) if can_submit else None
            if submitted:
                page.wait_for_timeout(1500)
                if page.locator("text=Application sent").count() or _easy_apply_modal(page).count() == 0:
                    _click_modal_button(page, ["Done"])
                    return ApplyResult(title, company, url, "applied", "Submitted after retry fill")
            advanced = _click_modal_button(page, advance_labels)
            if not advanced:
                # Last try: force-click Next if present but Playwright thinks disabled
                advanced = _click_modal_button(page, advance_labels, force=True)
            if not advanced:
                hints = _unanswered_required_hints(page)
                detail = "NEED INFO (modal left open)"
                if hints:
                    detail += ": " + "; ".join(hints[:4])
                else:
                    detail += ": unknown required field / captcha — please fill in Chromium"
                _log(f"*** PING: {detail} ***", log)
                # Leave modal open for the user — do NOT discard
                return ApplyResult(title, company, url, "needs_info", detail)
        _log(f"Clicked '{advanced}'", log)
        page.wait_for_timeout(1200)

    hints = _unanswered_required_hints(page)
    detail = "NEED INFO: exited wizard without submit"
    if hints:
        detail += " — " + "; ".join(hints[:4])
    _log(f"*** PING: {detail} ***", log)
    return ApplyResult(title, company, url, "needs_info", detail)


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
) -> ApplyResult:
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
        return complete_easy_apply(
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
        )

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
        return ApplyResult(title, company, url, "skipped", "No Apply button found")

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
            return complete_easy_apply(
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
            )
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
    if looks_like_signup_wall(external) and account_credentials is None:
        _log(f"*** PING: SIGNUP/LOGIN REQUIRED *** {external.url}", log)
        return ApplyResult(title, company, url, "needs_signup", f"Signup wall: {external.url}")

    resolved_credentials = account_credentials
    if looks_like_signup_wall(external) and hasattr(account_credentials, "for_portal"):
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
        # Existing LinkedIn --submit behavior applies to Easy Apply only.
        # External auto-submission requires the full-auto caller to opt in explicitly.
        allow_submit=False if allow_submit is None else allow_submit,
        account_credentials=resolved_credentials,
        verification_client=verification_client,
        accept_required_terms=accept_required_terms,
    )
    if "needs_signup" in detail:
        _log(f"*** PING: SIGNUP/LOGIN REQUIRED *** {external.url}", log)
        return ApplyResult(title, company, url, "needs_signup", detail)
    if "external_needs_info" in detail:
        status = "needs_info"
    elif "submitted" in detail:
        status = "applied"
    elif dry_run:
        status = "dry_run"
    else:
        status = "needs_review"
    return ApplyResult(title, company, url, status, detail)


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
) -> RunSummary:
    from .cleanup import cleanup_run_logs, merge_applied_from_summary, sanitize_summary_for_disk
    from .application_flow import missing_application_details

    profile = profile or load_profile()
    defaults = defaults or {}
    summary = RunSummary(searched_keywords=keywords, location=location)
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
                if result.status in {"applied", "dry_run"}:
                    applied += 1
                    skip_urls.add(href)
                elif result.status == "needs_signup":
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
