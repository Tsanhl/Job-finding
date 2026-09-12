"""Bound filling side effects; unknown write endpoints require a portal handoff."""

import hashlib
from contextlib import asynccontextmanager
from email.parser import BytesParser
from email.policy import default
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

# Installed before any filling event and restored before the user's handoff.
INSTALL = """() => {
 if(window.__applypilotFillBoundary)return;
 const proto=HTMLFormElement.prototype;
 const state={submit:proto.submit,requestSubmit:proto.requestSubmit,blocked:0};
 state.stop=e=>{if(safe(e.submitter,e.target))return;state.blocked++;e.preventDefault();e.stopImmediatePropagation()};
 const safe=(button,form)=>button&&button===state.allowed&&button.form===form;
 state.request=function(button){if(safe(button,this))return state.requestSubmit.call(this,button);state.blocked++};
 state.deny=()=>{state.blocked++};
 state.direct=function(){if(state.allowed&&state.allowed.form===this)return state.submit.call(this);state.blocked++};
 window.addEventListener('submit',state.stop,true);
 proto.submit=state.direct;proto.requestSubmit=state.request;
 window.__applypilotFillBoundary=state;
}"""
REMOVE = """() => {
 const s=window.__applypilotFillBoundary;if(!s)return 0;
 window.removeEventListener('submit',s.stop,true);
 if(HTMLFormElement.prototype.submit===s.direct)HTMLFormElement.prototype.submit=s.submit;
 if(HTMLFormElement.prototype.requestSubmit===s.request)HTMLFormElement.prototype.requestSubmit=s.requestSubmit;
 delete window.__applypilotFillBoundary;return s.blocked;
}"""


def document_upload(request, origin, hashes):
    url = urlsplit(request.url)
    if request.method not in {"POST", "PUT"} or url.netloc != origin:
        return False
    if not set(url.path.lower().strip("/").split("/")) & {
        "upload",
        "uploads",
        "attachment",
        "attachments",
        "file",
        "files",
    }:
        return False
    body = request.post_data_buffer or b""
    content_type = request.headers.get("content-type", "")
    if not body or len(body) > 26 * 1024 * 1024:
        return False
    if content_type.startswith("multipart/form-data"):
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + content_type.encode()
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        parts = list(message.iter_parts())
        files = [part for part in parts if part.get_filename()]
        metadata = [part for part in parts if not part.get_filename()]
        allowed_metadata = {
            "__requestverificationtoken",
            "csrfmiddlewaretoken",
            "csrf_token",
            "_csrf",
            "__viewstate",
            "__eventvalidation",
        }
        return (
            len(files) == 1
            and hashlib.sha256(files[0].get_payload(decode=True) or b"").hexdigest()
            in hashes
            and all(
                (part.get_param("name", header="content-disposition") or "").casefold()
                in allowed_metadata
                and len(part.get_payload(decode=True) or b"") <= 65536
                for part in metadata
            )
        )
    return hashlib.sha256(body).hexdigest() in hashes


@asynccontextmanager
async def filling_boundary(page, adapter, guard, *, upload_hashes=frozenset()):
    """Reject native/implicit submission and unclassified JavaScript writes.

    This is a conservative network capability boundary, not an assertion that
    arbitrary third-party JavaScript can be proven harmless. Unknown write APIs
    are parked, and adapters must qualify each permitted endpoint separately.
    """
    evidence = {"blocked": 0, "upload_failures": 0}

    def response_received(response):
        if response.status >= 400 and document_upload(
            response.request, urlsplit(page.url).netloc, upload_hashes
        ):
            evidence["upload_failures"] += 1

    page.on("response", response_received)
    authority = {"action": None}
    page._applypilot_write_authority = authority
    origin = urlsplit(page.url).netloc

    async def install(frame):
        if urlsplit(frame.url).netloc in {"", origin}:
            try:
                await frame.evaluate(INSTALL)
            except PlaywrightError:
                # A disappearing frame cannot receive filling actions.
                return

    async def route(request):
        req = request.request
        url = urlsplit(req.url)
        is_write = req.method not in {"GET", "HEAD", "OPTIONS"}
        allowed_upload = document_upload(req, origin, upload_hashes)
        final_path = any(
            word in url.path.lower()
            for word in ("submit", "finalize", "completeapplication")
        )
        try:
            guard()
        except PermissionError:
            await request.abort("blockedbyclient")
            evidence["blocked"] += 1
            return
        from .portal_actions import allowed_post

        provider_save = allowed_post(req, authority["action"])
        from .workday import allowed_json_save

        provider_save = provider_save or allowed_json_save(req, authority)
        if final_path or (is_write and not allowed_upload and not provider_save):
            evidence["blocked"] += 1
            await request.abort("blockedbyclient")
        else:
            await request.fallback()

    await page.route("**/*", route)
    page.on("framenavigated", install)
    for frame in page.frames:
        await install(frame)
    try:
        yield evidence
    finally:
        page.remove_listener("framenavigated", install)
        page.remove_listener("response", response_received)
        for frame in page.frames:
            if urlsplit(frame.url).netloc in {"", origin}:
                try:
                    evidence["blocked"] += await frame.evaluate(REMOVE)
                except PlaywrightError:
                    continue
        await page.unroute("**/*", route)
        page._applypilot_write_authority = None
