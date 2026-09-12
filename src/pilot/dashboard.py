"""JobSignal-designed, authenticated loopback UI for the existing foreground runtime."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .runtime import client
from .store import default_home, uid

STATIC = Path(__file__).parent / "static"
PUBLIC_COMMANDS = {
    "capabilities",
    "doctor",
    "status",
    "tabs",
    "profiles",
    "documents",
    "source_roots",
    "register_source",
    "start",
    "open",
    "review",
    "release",
    "submitted",
    "detect_edits",
    "resume_application",
    "answer_questions",
    "adopt_edits",
    "pause",
    "resume",
    "stop",
    "open_job_links",
    "linkedin_easy_apply",
    "linkedin_status",
    "linkedin_resume",
    "gmail_connect",
    "gmail_connections",
    "gmail_status",
    "gmail_disconnect",
    "gmail_revoke",
    "gmail_job",
    "document_preview",
    "backup",
    "workspace_profile",
    "workspace_select_profile",
    "workspace_save_profile",
    "workspace_catalog",
    "workspace_opportunities",
    "workspace_opportunity_page",
    "workspace_job_action",
    "workspace_applied",
    "workspace_history",
    "workspace_assessment",
    "workspace_assessment_complete",
    "workspace_history_update",
    "workspace_portfolios",
    "workspace_portfolio_save",
    "workspace_find",
    "workspace_search_history",
    "workspace_save_context",
    "workspace_context",
    "workspace_discovery_status",
    "workspace_job",
    "workspace_cancel_job",
    "workspace_mail_status",
    "workspace_mail_configure",
    "workspace_mail_sync",
    "workspace_mail_messages",
    "workspace_mail_resolve",
    "workspace_codex",
    "workspace_sources",
    "workspace_source_save",
    "workspace_import_preview",
    "workspace_import_jobsignal",
    "workspace_revision",
    "workspace_recovery_status",
    "workspace_recovery_configure",
    "workspace_recovery_now",
}


def create_app(home=None, token=None, port=8502, call=None):
    home = Path(home or default_home()).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = token or secrets.token_urlsafe(32)
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def send(payload):
        return await asyncio.to_thread(call or client, payload, home)

    @app.middleware("http")
    async def protect(request, next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "Invalid local host"}, status_code=403)
        path = request.url.path
        if (
            request.method not in {"GET", "HEAD"}
            and request.headers.get("origin") not in origins
        ):
            return JSONResponse({"error": "Invalid local origin"}, status_code=403)
        if path.startswith("/api/") and path != "/api/unlock":
            if not hmac.compare_digest(
                request.cookies.get("applypilot_session", ""), session
            ):
                return JSONResponse(
                    {"error": "Open the local launcher to unlock this workspace"},
                    status_code=401,
                )
            if request.method != "GET" and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), csrf
            ):
                return JSONResponse(
                    {"error": "Reload the local workspace"}, status_code=403
                )
        if int(request.headers.get("content-length", "0")) > 21 * 1024 * 1024:
            return JSONResponse({"error": "Request exceeds 21 MB"}, status_code=413)
        response = await next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health():
        return {
            "service": "applypilot-local",
            "instance": hashlib.sha256(token.encode()).hexdigest(),
        }

    @app.post("/api/unlock")
    async def unlock(request: Request):
        raw = await request.body()
        if len(raw) > 4096:
            return JSONResponse({"error": "Invalid unlock request"}, status_code=400)
        import json

        try:
            value = json.loads(raw).get("token", "")
        except (ValueError, AttributeError):
            value = ""
        if not isinstance(value, str) or not hmac.compare_digest(value, token):
            return JSONResponse(
                {"error": "Use the link opened by the local launcher"}, status_code=401
            )
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "applypilot_session", session, httponly=True, samesite="strict", path="/"
        )
        return response

    @app.get("/api/session")
    async def session_status():
        return {"csrf": csrf}

    @app.post("/api/command")
    async def command(request: Request):
        raw = await request.body()
        if len(raw) > 1024 * 1024:
            return JSONResponse({"error": "Command exceeds 1 MB"}, status_code=413)
        import json

        try:
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or payload.get("op") not in PUBLIC_COMMANDS
            ):
                raise ValueError("Unsupported dashboard action")
            return {"result": await send(payload)}
        except (ValueError, PermissionError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except (ConnectionError, OSError, RuntimeError):
            return JSONResponse(
                {
                    "error": "Runtime unavailable or out of date. Restart the local launcher."
                },
                status_code=503,
            )

    @app.post("/api/documents")
    async def document(request: Request):
        form = await request.form(
            max_files=1, max_fields=4, max_part_size=21 * 1024 * 1024
        )
        file = form.get("file")
        kind = form.get("kind", "cv")
        if not file or form.get("approved") != "true":
            return JSONResponse(
                {"error": "Select and approve this document"}, status_code=400
            )
        ext = Path(file.filename or "").suffix.lower()
        if ext not in {".pdf", ".docx", ".txt"}:
            return JSONResponse({"error": "Use PDF, DOCX or text"}, status_code=400)
        body = await file.read(20 * 1024 * 1024 + 1)
        if len(body) > 20 * 1024 * 1024:
            return JSONResponse({"error": "Document exceeds 20 MB"}, status_code=400)
        directory = home / "documents"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (uid() + ext)
        with path.open("xb") as out:
            path.chmod(0o600)
            out.write(body)
        try:
            result = await send(
                {
                    "op": "register_document",
                    "path": str(path),
                    "kind": kind,
                    "approved": True,
                }
            )
        except Exception:
            path.unlink(missing_ok=True)
            return JSONResponse(
                {
                    "error": "Document registration failed; check file format and runtime status"
                },
                status_code=400,
            )
        return {"result": result}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
