"""Repeatable portal sections using stable containers, identities and readback."""

import re

from .portal_actions import FAMILY, execute

SECTIONS = {
    "work_experience": '[id$="EmploymentHistory"],[id$="WorkExperience"],[data-automation-id="workExperienceSection"]',
    "education": '[id$="EducationHistory"],[id$="Education"],[data-automation-id="educationSection"]',
}
ALIASES = {
    "employer": ("employer", "company", "companyname"),
    "title": ("jobtitle", "title", "position"),
    "institution": ("institution", "school", "university", "schoolname"),
    "degree": ("degree", "qualification"),
    "start": ("start", "startdate", "fromdate"),
    "end": ("end", "enddate", "todate"),
    "responsibilities": ("responsibilities", "duties", "description"),
}
CARDS = '.employment-record,.education-record,[id$="EmploymentRecord"],[id$="EducationRecord"]'
EDITOR = '.record-editor,[id$="EmploymentEditor"],[id$="EducationEditor"],[data-automation-id="workExperience"],[data-automation-id="education"]'


def key_for(text):
    clean = re.sub(r"[^a-z]", "", re.split(r"[_$]|--", text)[-1].lower())
    return next((key for key, names in ALIASES.items() if clean in names), None)


def values_for(record, kind):
    return {
        k: str(value)
        for k in ALIASES
        if (
            value := record.get(
                k,
                record.get(
                    {
                        "institution": "school",
                        "start": "start_date",
                        "end": "end_date",
                    }.get(k, k)
                ),
            )
        )
        not in (None, "")
    }


async def controls(editor):
    result = {}
    for loc in await editor.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=button]),textarea,select"
    ).all():
        if not await loc.is_visible():
            continue
        descriptor = await loc.evaluate(
            "e=>({name:e.name||e.id||'',label:[...(e.labels||[])].map(l=>l.textContent).join(' ')})"
        )
        key = key_for(descriptor["name"]) or key_for(descriptor["label"])
        if key:
            if key in result:
                raise ValueError("Ambiguous repeated-record control")
            result[key] = loc
    return result


async def card_values(card):
    values = {k: await loc.input_value() for k, loc in (await controls(card)).items()}
    for pair in await card.locator("dt").all():
        key = key_for(await pair.inner_text())
        dd = pair.locator("xpath=following-sibling::dd[1]")
        if key and await dd.count():
            values[key] = (await dd.inner_text()).strip()
    return values


async def reconcile(page, adapter, profile, guard, plan):
    problems = []
    for kind, selector in SECTIONS.items():
        section = page.locator(selector)
        if await section.count() != 1 or not await section.is_visible():
            continue
        records = profile.get(kind, [])
        if isinstance(records, dict):
            records = [records]
        for record in records:
            values = values_for(record, kind)
            identity = str(record.get("id", ""))
            required_identity = (
                ("employer", "title")
                if kind == "work_experience"
                else ("institution", "degree")
            )
            if not identity or any(not values.get(k) for k in required_identity):
                problems.append(f"{kind}: confirm the record identity before adding it")
                continue
            saved = [
                await card_values(card) for card in await section.locator(CARDS).all()
            ]
            matches = [
                row
                for row in saved
                if all(
                    row.get(k, "").casefold() == values[k].casefold()
                    for k in required_identity
                )
            ]
            if len(matches) > 1 or (
                matches
                and any(
                    k in matches[0] and matches[0][k] != v for k, v in values.items()
                )
            ):
                problems.append(
                    f"{kind} record {identity}: reconcile the existing parsed record"
                )
                continue
            if matches:
                continue
            editor = section.locator(EDITOR)
            if await editor.count() == 0:
                label = "Employment" if kind == "work_experience" else "Education"
                add = section.locator(f'[id$="btnAdd{label}"]')
                if await add.count() != 1:
                    problems.append(f"{kind}: supported Add control unavailable")
                    continue
                guard()
                plan.require("fill")
                await execute(page, add, "add", guard)
                editor = section.locator(EDITOR)
            if await editor.count() != 1:
                problems.append(f"{kind}: ambiguous record editor")
                continue
            mapped = await controls(editor)
            conflict = False
            for key, loc in mapped.items():
                current = await loc.input_value()
                value = values.get(key)
                if current and value and current.casefold() != value.casefold():
                    conflict = True
            if conflict:
                problems.append(
                    f"{kind} record {identity}: existing editor contains different information"
                )
                continue
            await editor.evaluate(
                "(e,v)=>{e.dataset.recordId=v.id;e.dataset.recordKind=v.kind}",
                {"id": identity, "kind": kind},
            )
            missing = False
            for key, loc in mapped.items():
                value = values.get(key)
                if not value:
                    if await loc.get_attribute("required") is not None:
                        missing = True
                    continue
                guard()
                plan.require("fill")
                type_ = await loc.get_attribute("type")
                if type_ == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    missing = True
                    continue
                if await loc.evaluate("e=>e.tagName") == "SELECT":
                    await loc.select_option(label=value, timeout=15000)
                else:
                    await loc.fill(value, timeout=15000)
                if (
                    await loc.input_value() != value
                    and await loc.evaluate("e=>e.tagName") != "SELECT"
                ):
                    missing = True
            if missing or any(key not in mapped for key in required_identity):
                problems.append(
                    f"{kind} record {identity}: complete the unresolved record fields"
                )
                continue
            if adapter.name not in FAMILY:
                # Workday keeps edited records in the section until Save/Continue.
                continue
            label = "Employment" if kind == "work_experience" else "Education"
            save = editor.locator(f'[id$="btnSave{label}"]')
            if await save.count() != 1:
                problems.append(f"{kind}: supported record Save control unavailable")
                continue
            await execute(page, save, "save_record", guard)
            reread = [
                await card_values(card) for card in await section.locator(CARDS).all()
            ]
            if not any(
                all(
                    row.get(key) == value
                    for key, value in values.items()
                    if key in mapped
                )
                for row in reread
            ):
                problems.append(
                    f"{kind} record {identity}: saved summary does not match"
                )
    return problems
