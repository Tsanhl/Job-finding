"""Observed Workday save contracts, separate from final transmission."""

import json
import re
from urllib.parse import urlsplit

FINAL = re.compile(
    r"submit|finali[sz]e|completeapplication|declaration|withdraw|delete|decline|offer",
    re.IGNORECASE,
)
APPLICATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _request_identity(url, *, origin, tenant):
    parsed = urlsplit(url)
    if f"{parsed.scheme}://{parsed.netloc}" != origin:
        return None
    prefix = f"/wday/calypso/cxs/jobapplication/{tenant}/"
    if not parsed.path.startswith(prefix):
        return None
    relative = parsed.path[len(prefix) :]
    matched = re.fullmatch(r"jobapplication/([^/]+)/(.+)", relative)
    if not matched:
        matched = re.fullmatch(r"package/([^/]+)(?:/(.+))?", relative)
    if not matched or not APPLICATION_ID.fullmatch(matched.group(1)):
        return None
    return matched.group(1), (matched.group(2) or "package")


def _bind_identity(authority, application_id):
    current = authority.get("workday_application_id")
    if current and current != application_id:
        return False
    authority["workday_application_id"] = application_id
    return True


def allowed_json_save(request, authority):
    if not authority or request.method not in {"POST", "PUT", "PATCH"}:
        return False
    if not request.headers.get("content-type", "").startswith("application/json"):
        return False
    try:
        payload = json.loads(request.post_data or "")
    except (ValueError, TypeError):
        return False
    exact = authority.get("json") or authority
    if exact.get("url") and request.url == exact["url"]:
        return payload == exact["payload"]
    if exact.get("workday_source") and payload == exact.get("payload"):
        identity = _request_identity(
            request.url,
            origin=exact["origin"],
            tenant=exact["tenant"],
        )
        if not identity or identity[1].casefold() != "source":
            return False
        expected = exact.get("application_id") or authority.get(
            "workday_application_id"
        )
        if expected and identity[0] != expected:
            return False
        return _bind_identity(authority, identity[0])
    navigation = authority.get("navigation")
    if not navigation:
        return False
    identity = _request_identity(
        request.url,
        origin=navigation["origin"],
        tenant=navigation["tenant"],
    )
    if not identity:
        return False
    application_id, operation = identity
    expected = navigation.get("application_id") or authority.get(
        "workday_application_id"
    )
    if expected and application_id != expected:
        return False
    body = request.post_data or ""
    if not body or len(body.encode()) > 2 * 1024 * 1024:
        return False
    if FINAL.search(operation) or FINAL.search(body):
        return False
    return _bind_identity(authority, application_id)


async def select_option(page, target, raw, option, value, guard):
    """Grant only the exact observed source selection for this application."""
    if raw["field_id"] != "source--source":
        guard()
        await option.click(timeout=15000)
        return
    origin = urlsplit(page.url)
    application_id = target.portal_application_id
    if application_id and not APPLICATION_ID.fullmatch(application_id):
        raise ValueError("Invalid bound Workday application identity")
    tenant = origin.hostname.split(".")[0]
    option_id = await option.get_attribute("data-value")
    if not option_id or (await option.inner_text()).strip() != value:
        raise ValueError("Source option does not match the requested choice")
    authority = getattr(page, "_applypilot_write_authority", None)
    if authority is None:
        raise PermissionError("Field save requires an active filling boundary")
    authority["json"] = {
        "workday_source": True,
        "origin": f"{origin.scheme}://{origin.netloc}",
        "tenant": tenant,
        "application_id": application_id or authority.get("workday_application_id", ""),
        "payload": {"source": {"id": option_id, "descriptor": value}},
    }

    def source_response(response):
        identity = _request_identity(
            response.url,
            origin=f"{origin.scheme}://{origin.netloc}",
            tenant=tenant,
        )
        return bool(
            response.request.method == "POST"
            and identity
            and identity[1].casefold() == "source"
        )

    try:
        guard()
        async with page.expect_response(source_response, timeout=15000) as saved:
            await option.click(timeout=15000)
        response = await saved.value
        if not response.ok:
            raise ValueError("Workday rejected the source save")
    finally:
        authority["json"] = None


async def advance(page, target, action, guard):
    """Allow only application-scoped JSON saves while a non-final action runs."""
    if (await action.inner_text()).strip().casefold() not in {
        "save and continue",
        "next",
        "continue",
        "review",
    }:
        raise ValueError("Unrecognised Workday navigation action")
    application_id = target.portal_application_id
    if application_id and not APPLICATION_ID.fullmatch(application_id):
        raise ValueError("Invalid bound Workday application identity")
    origin = urlsplit(page.url)
    authority = getattr(page, "_applypilot_write_authority", None)
    if authority is None:
        raise PermissionError("Navigation requires an active filling boundary")
    authority["navigation"] = {
        "origin": f"{origin.scheme}://{origin.netloc}",
        "tenant": origin.hostname.split(".")[0],
        "application_id": application_id or authority.get("workday_application_id", ""),
    }
    try:
        guard()
        await action.click(timeout=15000)
        await page.wait_for_timeout(500)
    finally:
        authority["navigation"] = None
