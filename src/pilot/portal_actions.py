"""Explicit provider actions for ASP.NET and structured application editors."""

import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

FAMILY = {"allhires", "apply4law"}
SAFE_IDS = {
    "next": {"btnnext", "btnsavecontinue", "btnsaveandcontinue", "saveandcontinue"},
    "add": {
        "btnaddemployment",
        "btnaddworkexperience",
        "btnaddeducation",
        "btnaddqualification",
    },
    "save_record": {
        "btnsaveemployment",
        "btnsaveworkexperience",
        "btnsaveeducation",
        "btnsavequalification",
    },
}
FINAL = re.compile(
    r"submitapplication|finalsubmit|finalise|finalize|declaration|withdraw|delete", re.I
)


def control_suffix(value):
    return re.split(r"[_$]", value)[-1].lower()


def allowed_post(request, action):
    if not action or request.method != "POST" or request.url != action["url"]:
        return False
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return False
    try:
        body = parse_qs(
            request.post_data or "", keep_blank_values=True, max_num_fields=2000
        )
    except ValueError:
        return False
    # Only the selected submitter/event target may initiate this request.
    target = body.get("__EVENTTARGET", [""])
    if target not in ([""], [action["name"]]):
        return False
    if any(FINAL.search(k) and any(v for v in values) for k, values in body.items()):
        return False
    return body.get(action["name"]) == [action["value"]] or target == [action["name"]]


async def family_next(page):
    selectors = ",".join(
        f'input[id$="_{suffix}"],button[id$="_{suffix}"],input[id="{suffix}"],button[id="{suffix}"]'
        for suffix in (
            "btnNext",
            "btnSaveContinue",
            "btnSaveAndContinue",
            "SaveAndContinue",
        )
    )
    candidates = page.locator(selectors)
    visible = []
    for loc in await candidates.all():
        if await loc.is_visible() and await loc.is_enabled():
            label = (
                (await loc.get_attribute("value") or await loc.inner_text())
                .strip()
                .casefold()
            )
            if label in {"next", "save and continue", "save & continue", "continue"}:
                visible.append(loc)
    return visible[0] if len(visible) == 1 else None


@asynccontextmanager
async def authorised_action(page, control, kind):
    state = getattr(page, "_applypilot_write_authority", None)
    if state is None:
        raise ValueError("Provider action requires an active filling boundary")
    descriptor = await control.evaluate(
        """e=>({name:e.name||'',id:e.id||'',value:e.value||e.textContent.trim(),type:e.type||'',action:e.form?.action||'',method:e.form?.method||'',formId:e.form?.id||'',formCount:document.forms.length})"""
    )
    if (
        control_suffix(descriptor["id"]) not in SAFE_IDS[kind]
        and control_suffix(descriptor["name"]) not in SAFE_IDS[kind]
    ):
        raise ValueError("Unrecognised provider action")
    if descriptor["type"] not in {"submit", "button"}:
        raise ValueError("Unsupported action control")
    origin = urlsplit(page.url)
    destination = urlsplit(descriptor["action"])
    if (
        (origin.scheme, origin.netloc, origin.path)
        != (destination.scheme, destination.netloc, destination.path)
        or descriptor["method"].lower() != "post"
        or not descriptor["name"]
    ):
        raise ValueError("Provider action must post to its existing form endpoint")
    if state.get("action"):
        raise ValueError("Another provider action is active")
    state["action"] = {
        "url": descriptor["action"],
        "name": descriptor["name"],
        "value": descriptor["value"],
    }
    await control.evaluate(
        "e=>{const s=window.__applypilotFillBoundary;if(!s)throw Error('Missing boundary');s.allowed=e}"
    )
    try:
        yield
    finally:
        state["action"] = None
        try:
            await page.evaluate(
                "()=>{const s=window.__applypilotFillBoundary;if(s)s.allowed=null}"
            )
        except Exception:
            pass  # A completed navigation removed the old frame.


async def execute(page, control, kind, guard):
    async with authorised_action(page, control, kind):
        guard()
        await control.click(timeout=15000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
