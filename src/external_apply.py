from __future__ import annotations

import re
import time
from typing import Any, Callable

from playwright.sync_api import Page

from .answers import answer_question
from .cover_letter import generate_cover_letter

LogFn = Callable[[str], None]


def _log(msg: str, log: LogFn | None) -> None:
    if log:
        log(msg)
    else:
        print(msg)


def looks_like_signup_wall(page: Page) -> bool:
    """Stricter signup detection — avoid false positives on marketing pages."""
    try:
        url = (page.url or "").lower()
        if "linkedin.com" in url:
            return False
        # Strong signals: password + create/register copy, or auth paths
        if any(x in url for x in ("/login", "/signin", "/sign-in", "/register", "/signup", "/sign-up", "/auth")):
            if page.locator("input[type='password']").count() > 0:
                return True
        pw = page.locator("input[type='password']")
        if pw.count() == 0:
            return False
        text = ""
        try:
            text = (page.locator("body").inner_text(timeout=2000) or "").lower()[:4000]
        except Exception:
            text = (page.content() or "").lower()[:4000]
        strong = (
            "create an account",
            "create account",
            "sign up to apply",
            "register to apply",
            "new candidate",
            "don't have an account",
            "do not have an account",
        )
        return any(h in text for h in strong)
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


def _click_progress(page: Page, *, allow_submit: bool = False) -> str | None:
    """Click safe progress controls; final external submission is user-only."""
    labels = [
        ("submit", ["Submit application", "Submit Application", "Submit your application", "Send application"]),
        ("next", ["Next", "Continue", "Save and continue", "Review"]),
        ("apply", ["Apply", "Apply Now", "Apply now"]),
    ]
    for kind, texts in labels:
        if kind == "submit" and not allow_submit:
            continue
        for t in texts:
            for sel in (
                f"button:has-text('{t}')",
                f"input[type='submit'][value='{t}']",
                f"button[type='submit']:has-text('{t}')",
            ):
                loc = page.locator(sel).first
                try:
                    if not (loc.count() and loc.is_visible(timeout=500)):
                        continue
                    txt = ((loc.inner_text() or "") + " " + (loc.get_attribute("value") or "")).lower()
                    if "easy apply" in txt or "linkedin" in txt:
                        continue
                    # Prefer submit when available
                    if kind == "apply" and page.locator("button:has-text('Submit')").count():
                        continue
                    loc.click(timeout=4000)
                    return f"{kind}:{t}"
                except Exception:
                    continue
    if not allow_submit:
        return None
    # Generic submit (only when explicitly enabled by a caller).
    for sel in ("button[type='submit']", "input[type='submit']"):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=4000)
                return "submit:generic"
        except Exception:
            continue
    return None


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
) -> str:
    """Best-effort fill for Greenhouse / Lever / Workday / Breezy / generic ATS (multi-step)."""
    page.wait_for_timeout(1200)

    if looks_like_signup_wall(page) and not wait_for_manual_login(page, log=log):
        return "needs_signup: login/register wall detected — user must sign in"

    # Wait briefly if still on LinkedIn redirect interstitial
    if "linkedin.com" in (page.url or "").lower():
        page.wait_for_timeout(2500)
        if "linkedin.com" in (page.url or "").lower():
            return "external_still_on_linkedin: no company ATS opened"

    portfolio = " | ".join(profile.get("portfolio_urls", [])[:3])
    defaults = {
        **defaults,
        "linkedin_url": profile.get("linkedin_url", ""),
        "portfolio": portfolio,
    }
    answers = profile.get("answers", {}) or {}

    def configured_yes_no(*keys: str) -> str | None:
        for key in keys:
            value = defaults.get(key) or answers.get(key)
            if str(value).strip().lower() in {"yes", "no"}:
                return str(value).strip().title()
        return None

    def matching_option(options: list[str], answer: str) -> str | None:
        """Match a confirmed answer to a dropdown without choosing by guesswork."""

        def normalise(value: str) -> str:
            value = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
            value = value.replace("a levels", "a level")
            value = value.replace("international baccalaureate", "ib")
            return " ".join(value.split())

        wanted = normalise(answer)
        if not wanted:
            return None
        exact = next((option for option in options if normalise(option) == wanted), None)
        if exact:
            return exact
        if len(wanted) >= 4:
            candidates = [
                option
                for option in options
                if wanted in normalise(option) or normalise(option) in wanted
            ]
            if len(candidates) == 1:
                return candidates[0]
        return None

    actions: list[str] = []
    for step in range(8):
        if looks_like_signup_wall(page) and not wait_for_manual_login(page, log=log):
            return "needs_signup: login/register wall detected — user must sign in"

        # File uploads
        files = page.locator("input[type='file']")
        for i in range(min(files.count(), 4)):
            try:
                files.nth(i).set_input_files(cv_path)
                _log(f"Uploaded CV on external form (input #{i+1})", log)
            except Exception as exc:
                _log(f"CV upload skip #{i+1}: {exc}", log)

        field_map = [
            (("name", "full name", "fullname"), profile.get("full_name", "")),
            (("first name", "firstname", "given"), profile.get("first_name", "")),
            (("last name", "lastname", "surname", "family"), profile.get("last_name", "")),
            (("email", "e-mail"), profile.get("email", "")),
            (("phone", "mobile", "tel"), profile.get("phone", "")),
            (("linkedin",), profile.get("linkedin_url", "") or ""),
            (
                ("portfolio", "website", "personal site", "github", "project"),
                defaults.get("portfolio") or "",
            ),
            (("city", "location"), profile.get("location", "")),
        ]

        inputs = page.locator(
            "input[type='text'], input[type='email'], input[type='tel'], "
            "input:not([type]), textarea"
        )
        for i in range(min(inputs.count(), 60)):
            el = inputs.nth(i)
            try:
                if not el.is_visible(timeout=300):
                    continue
                t = (el.get_attribute("type") or "text").lower()
                if t in {"hidden", "file", "checkbox", "radio", "submit", "button", "password"}:
                    continue
                if el.input_value() and el.input_value().strip():
                    continue
                meta = " ".join(
                    filter(
                        None,
                        [
                            el.get_attribute("name"),
                            el.get_attribute("id"),
                            el.get_attribute("placeholder"),
                            el.get_attribute("aria-label"),
                        ],
                    )
                ).lower()
                try:
                    label = el.evaluate(
                        """(n) => {
                          const id = n.id;
                          let ownLabel = '';
                          if (id) {
                            const l = document.querySelector(`label[for="${id}"]`);
                            if (l) ownLabel = l.innerText || '';
                          }
                          const wrap = n.closest('label,div,li,fieldset');
                          const nearby = wrap ? (wrap.querySelector('label,span')?.innerText || '') : '';
                          const fieldset = n.closest('fieldset');
                          const legend = fieldset ? (fieldset.querySelector('legend')?.innerText || '') : '';
                          const section = n.closest('section,[role="group"]');
                          const heading = section ? (section.querySelector('h1,h2,h3,h4')?.innerText || '') : '';
                          return [heading, legend, ownLabel, nearby].filter(Boolean).join(' ');
                        }"""
                    )
                    meta = (meta + " " + (label or "")).lower()
                except Exception:
                    pass

                tag = el.evaluate("n => n.tagName.toLowerCase()")
                if tag == "textarea" or "cover" in meta or "why" in meta or "additional" in meta:
                    letter = generate_cover_letter(
                        profile,
                        company=company or "the company",
                        role=role or "the role",
                        location=defaults.get("location", "London"),
                        job_description=job_context,
                        use_ai=use_ai,
                    )
                    el.fill(letter[:4500])
                    continue

                filled = False
                for keys, value in field_map:
                    if value and any(k in meta for k in keys):
                        el.fill(str(value)[:500])
                        filled = True
                        break
                if filled:
                    continue
                if meta.strip():
                    ans = answer_question(
                        meta,
                        profile,
                        job_context=job_context,
                        defaults=defaults,
                        use_ai=use_ai,
                    )
                    if ans:
                        el.fill(ans[:500])
            except Exception:
                continue

        # Selects
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                if not sel.is_visible(timeout=300):
                    continue
                opts = [
                    sel.locator("option").nth(j).inner_text().strip()
                    for j in range(sel.locator("option").count())
                ]
                label = ""
                try:
                    label = sel.evaluate(
                        "n => (n.closest('div,fieldset,label')?.innerText || '').slice(0,180)"
                    ).lower()
                except Exception:
                    pass
                choice = None
                if "sponsor" in label:
                    wanted = configured_yes_no("require_sponsorship")
                    choice = next((t for t in opts if t.lower() == wanted.lower()), None) if wanted else None
                elif any(k in label for k in ("driving licence", "driving license", "driver")):
                    wanted = configured_yes_no("has_driving_licence", "driving_licence")
                    choice = next((t for t in opts if t.lower() == wanted.lower()), None) if wanted else None
                elif any(k in label for k in ("authorize", "right to work", "eligible")):
                    wanted = configured_yes_no("authorized_to_work", "work_authorization", "right_to_work")
                    choice = next((t for t in opts if t.lower() == wanted.lower()), None) if wanted else None
                elif any(k in label for k in ("gender", "race", "ethnicity", "veteran", "disability")):
                    choice = next(
                        (t for t in opts if "prefer not" in t.lower() or t.lower() == "no"),
                        None,
                    )
                else:
                    confirmed_answer = answer_question(
                        label,
                        profile,
                        job_context=job_context,
                        defaults=defaults,
                        use_ai=False,
                    )
                    if confirmed_answer:
                        choice = matching_option(opts, confirmed_answer)
                if choice:
                    sel.select_option(label=choice)
            except Exception:
                continue

        if dry_run:
            return "external_dry_run: filled form, did not submit"

        submit_button = page.locator(
            "button:has-text('Submit application'), button:has-text('Submit Application'), "
            "button:has-text('Submit your application'), button:has-text('Send application'), "
            "input[type='submit'][value*='submit' i], input[type='submit'][value*='send' i]"
        ).first
        try:
            if submit_button.count() and submit_button.is_visible(timeout=500):
                return "external_ready_for_manual_submit: form filled; final Submit left for user"
        except Exception:
            pass

        clicked = _click_progress(page, allow_submit=False)
        if not clicked:
            if step == 0:
                return "external_ready_for_manual_submit: form filled; final Submit left for user"
            break
        actions.append(clicked)
        _log(f"External step {step + 1}: clicked {clicked}", log)
        page.wait_for_timeout(1500)

        # Success heuristics
        body = ""
        try:
            body = (page.locator("body").inner_text(timeout=1500) or "").lower()[:2000]
        except Exception:
            pass
        if any(
            x in body
            for x in (
                "thank you for applying",
                "application submitted",
                "application has been submitted",
                "successfully applied",
                "we have received your application",
            )
        ):
            return "external_submitted: " + ",".join(actions)

    if any(a.startswith("submit") for a in actions):
        return "external_ready_for_manual_submit: form filled; final Submit left for user"
    if actions:
        return "external_filled_needs_manual_submit: " + ",".join(actions)
    return "external_filled_needs_manual_submit"
