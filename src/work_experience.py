"""Populate repeatable work-history sections without inventing profile facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from playwright.sync_api import Page


LogFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class WorkExperienceEntry:
    employer: str
    title: str
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    location: str = ""
    portal_title: str = ""


@dataclass(frozen=True, slots=True)
class ExperienceFillReport:
    attempted: bool = False
    added: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _first(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean(record.get(key))
        if value:
            return value
    return ""


def _standardised_title(record: dict[str, Any], title: str) -> str:
    explicit = _first(record, "portal_title", "standardized_title", "standardised_title")
    if explicit:
        return explicit

    # SmartRecruiters may require a taxonomy value rather than the CV wording.
    # These mappings preserve the nature of the verified role; they do not add
    # seniority or responsibilities.
    context = f"{title} {_first(record, 'form_type')}".lower()
    for marker, value in (
        ("intern", "Intern"),
        ("tutor", "Tutor"),
        ("ambassador", "Ambassador"),
        ("consultant", "Consultant"),
        ("panel member", "Panel Member"),
    ):
        if marker in context:
            return value
    return title


def work_experience_entries(profile: dict[str, Any]) -> tuple[WorkExperienceEntry, ...]:
    """Return every complete, unique structured work-history record."""
    raw = profile.get("work_experience")
    if not isinstance(raw, list):
        raw = profile.get("employment_history")
    if not isinstance(raw, list):
        return ()

    entries: list[WorkExperienceEntry] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in raw:
        if not isinstance(record, dict):
            continue
        employer = _first(record, "employer", "company", "organisation", "organization")
        title = _first(record, "position", "title", "role")
        if not employer or not title:
            continue
        bullets = record.get("cv_bullets")
        description = _first(record, "summary", "description", "responsibilities")
        if not description and isinstance(bullets, list):
            description = " ".join(_clean(item) for item in bullets if _clean(item))
        start_date = _first(record, "start_date", "start")
        end_date = _first(record, "end_date", "end")
        location = _first(record, "office", "location", "city")
        key = (
            _normalise(employer),
            _normalise(title),
            start_date,
            end_date,
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            WorkExperienceEntry(
                employer=employer,
                title=title,
                description=description,
                start_date=start_date,
                end_date=end_date,
                location=location,
                portal_title=_standardised_title(record, title),
            )
        )
    return tuple(entries)


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000) or ""
    except Exception:
        return ""


def _entry_present(page_text: str, entry: WorkExperienceEntry) -> bool:
    """Match the employer and role so two roles at one company are retained."""
    body = _normalise(page_text)
    employer = _normalise(entry.employer)
    titles = {
        _normalise(entry.title),
        _normalise(entry.portal_title),
    }
    titles.discard("")
    return bool(
        employer
        and employer in body
        and any(title in body for title in titles)
    )


def _last_visible(locator: Any) -> Any | None:
    try:
        for index in range(locator.count() - 1, -1, -1):
            candidate = locator.nth(index)
            if candidate.is_visible(timeout=250) and not candidate.is_disabled():
                return candidate
    except Exception:
        return None
    return None


def _find_by_label_or_selector(
    page: Page,
    patterns: tuple[str, ...],
    selectors: tuple[str, ...],
) -> Any | None:
    for pattern in patterns:
        candidate = _last_visible(page.get_by_label(re.compile(pattern, re.I)))
        if candidate is not None:
            return candidate
    for selector in selectors:
        candidate = _last_visible(page.locator(selector))
        if candidate is not None:
            return candidate
    return None


def _button_in_experience_context(page: Page, names: re.Pattern[str]) -> Any | None:
    buttons = page.get_by_role("button", name=names)
    try:
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            if not button.is_visible(timeout=250) or button.is_disabled():
                continue
            context = button.evaluate(
                r"""node => {
                  for (let parent = node.parentElement;
                       parent && parent !== document.body;
                       parent = parent.parentElement) {
                    const text = (parent.innerText || '').trim();
                    if (text.length <= 1800 &&
                        /(?:work\s+experience|employment|work\s+history|experience)/i.test(text)) {
                      return text;
                    }
                  }
                  return '';
                }"""
            )
            if context:
                return button
    except Exception:
        return None
    return None


def _find_experience_add_button(page: Page) -> Any | None:
    for selector in (
        "button[aria-label*='experience' i]:has-text('Add')",
        "button:has-text('Add experience')",
        "button:has-text('Add work experience')",
        "[data-testid*='experience' i] button:has-text('Add')",
        "[data-test*='experience' i] button:has-text('Add')",
    ):
        candidate = _last_visible(page.locator(selector))
        if candidate is not None:
            return candidate
    return _button_in_experience_context(
        page,
        re.compile(r"^(?:add|add another|add experience|add work experience)$", re.I),
    )


def _find_experience_save_button(page: Page) -> Any | None:
    contextual = _button_in_experience_context(
        page,
        re.compile(r"^(?:save|done|add experience)$", re.I),
    )
    if contextual is not None:
        return contextual
    for name in (
        re.compile(r"^save(?: experience)?$", re.I),
        re.compile(r"^done$", re.I),
        re.compile(r"^add experience$", re.I),
    ):
        candidate = _last_visible(page.get_by_role("button", name=name))
        if candidate is not None:
            return candidate
    return None


def _dismiss_experience_editor(page: Page) -> bool:
    """Close only the active experience editor after one record is blocked."""
    names = re.compile(r"^(?:cancel|close|discard)$", re.I)
    candidate = _button_in_experience_context(page, names)
    if candidate is None:
        candidate = _last_visible(page.get_by_role("button", name=names))
    if candidate is None:
        return False
    try:
        candidate.click(timeout=2000)
        page.wait_for_timeout(200)
        return True
    except Exception:
        return False


def _option_match(option_text: str, wanted: str) -> bool:
    option = _normalise(option_text)
    target = _normalise(wanted)
    return bool(
        option
        and target
        and (option == target or (len(option) >= 4 and (option in target or target in option)))
    )


def _fill_typeahead(page: Page, control: Any, value: str) -> None:
    control.fill(value)
    page.wait_for_timeout(250)
    options = page.get_by_role("option")
    try:
        for index in range(options.count()):
            option = options.nth(index)
            if not option.is_visible(timeout=150):
                continue
            text = (option.inner_text(timeout=300) or "").strip()
            if _option_match(text, value):
                option.click(timeout=1200)
                return
    except Exception:
        return


def _date_value(control: Any, raw: str) -> str | None:
    """Format only the precision present in the profile; never invent a day."""
    value = raw.strip()
    if not value:
        return ""
    full_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    month_date = bool(re.fullmatch(r"\d{4}-\d{2}", value))
    if not (full_date or month_date):
        return value

    input_type = (control.get_attribute("type") or "text").lower()
    placeholder = (control.get_attribute("placeholder") or "").lower()
    if input_type == "month":
        return value[:7]
    if input_type == "date":
        return value if full_date else None
    if "mm/yyyy" in placeholder or "mm-yyyy" in placeholder:
        return f"{value[5:7]}/{value[:4]}"
    if "dd/mm" in placeholder:
        if not full_date:
            return None
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%d/%m/%Y")
    if "mm/dd" in placeholder:
        if not full_date:
            return None
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%m/%d/%Y")
    return value


def _fill_date(control: Any | None, raw: str, label: str) -> str | None:
    if control is None or not raw:
        return None
    value = _date_value(control, raw)
    if value is None:
        return f"{label} has month-only precision but the portal requires an exact day"
    control.fill(value)
    return None


def _fill_experience_editor(page: Page, entry: WorkExperienceEntry) -> tuple[bool, str]:
    title = _find_by_label_or_selector(
        page,
        (r"^(?:job\s+)?title", r"^position", r"^role$"),
        (
            "input[name*='title' i]",
            "input[id*='title' i]",
            "input[aria-label*='title' i]",
            "input[name*='position' i]",
        ),
    )
    employer = _find_by_label_or_selector(
        page,
        (r"^company", r"^employer", r"^organisation", r"^organization"),
        (
            "input[name*='company' i]",
            "input[id*='company' i]",
            "input[name*='employer' i]",
            "input[id*='employer' i]",
        ),
    )
    if title is None or employer is None:
        return False, "the new experience editor did not expose title and employer fields"

    _fill_typeahead(page, title, entry.portal_title or entry.title)
    _fill_typeahead(page, employer, entry.employer)

    location = _find_by_label_or_selector(
        page,
        (r"^office", r"^location", r"^city"),
        (
            "input[name*='location' i]",
            "input[id*='location' i]",
            "input[name*='office' i]",
            "input[name*='city' i]",
        ),
    )
    if location is not None and entry.location:
        _fill_typeahead(page, location, entry.location)

    description = _find_by_label_or_selector(
        page,
        (r"^description", r"^responsibilit", r"^duties", r"^summary"),
        (
            "textarea[name*='description' i]",
            "textarea[id*='description' i]",
            "textarea",
        ),
    )
    if description is not None and entry.description:
        description.fill(entry.description)

    start = _find_by_label_or_selector(
        page,
        (r"^start(?:ing)? date", r"^from$"),
        (
            "input[name*='start' i]",
            "input[id*='start' i]",
            "input[aria-label*='start' i]",
        ),
    )
    end = _find_by_label_or_selector(
        page,
        (r"^end(?:ing)? date", r"^to$"),
        (
            "input[name*='end' i]",
            "input[id*='end' i]",
            "input[aria-label*='end' i]",
        ),
    )
    date_blockers = tuple(
        message
        for message in (
            _fill_date(start, entry.start_date, "Start date"),
            _fill_date(end, entry.end_date, "End date"),
        )
        if message
    )
    if date_blockers:
        return False, "; ".join(date_blockers)

    save = _find_experience_save_button(page)
    if save is None:
        return False, "the experience editor did not expose a Save control"
    save.click(timeout=3000)
    page.wait_for_timeout(350)
    try:
        if save.is_visible(timeout=250):
            return False, "the experience editor remained open after Save (check required fields)"
    except Exception:
        pass
    return True, ""


def fill_repeatable_work_experience(
    page: Page,
    *,
    profile: dict[str, Any],
    log: LogFn | None = None,
) -> ExperienceFillReport:
    """Add every missing profile entry when a repeatable section is present."""
    entries = work_experience_entries(profile)
    if not entries:
        return ExperienceFillReport()

    add_button = _find_experience_add_button(page)
    if add_button is None:
        return ExperienceFillReport()

    added: list[str] = []
    skipped: list[str] = []
    blockers: list[str] = []
    for index, entry in enumerate(entries):
        if _entry_present(_body_text(page), entry):
            skipped.append(entry.employer)
            continue
        add_button = _find_experience_add_button(page)
        if add_button is None:
            remaining = [item.employer for item in entries[index:]]
            blockers.append(
                "Add experience control disappeared before: " + ", ".join(remaining)
            )
            break
        try:
            add_button.click(timeout=3000)
            page.wait_for_timeout(250)
            success, detail = _fill_experience_editor(page, entry)
        except Exception as exc:
            success = False
            detail = f"portal interaction failed ({type(exc).__name__})"
        if not success:
            blockers.append(f"{entry.employer}: {detail}")
            if not _dismiss_experience_editor(page):
                blockers.append(
                    "Could not safely close the blocked experience editor; "
                    "later records were not attempted"
                )
                break
            continue
        added.append(entry.employer)
        if log:
            log(f"Added work experience: {entry.title} at {entry.employer}")

    return ExperienceFillReport(
        attempted=True,
        added=tuple(added),
        skipped=tuple(skipped),
        blockers=tuple(blockers),
    )
