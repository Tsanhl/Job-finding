"""Single local launcher. No login service, public server or background installation."""

import argparse
import asyncio
import fcntl
import hashlib
import os
import tempfile
import secrets
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import uvicorn

from .dashboard import create_app
from .runtime import client, serve
from .store import default_home


async def run(home, port, cdp, open_browser):
    import httpx

    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (home / "dashboard.lock").open("a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            for _ in range(30):
                try:
                    url = (home / "dashboard-url.txt").read_text()
                    parsed = urlsplit(url)
                    if parsed.hostname not in {"127.0.0.1", "localhost"}:
                        raise ValueError("Invalid local launcher link")
                    token = parse_qs(parsed.fragment).get("token", [""])[0]
                    async with httpx.AsyncClient(timeout=2) as c:
                        r = await c.get(f"{parsed.scheme}://{parsed.netloc}/health")
                    if (
                        r.json().get("instance")
                        == hashlib.sha256(token.encode()).hexdigest()
                    ):
                        if open_browser:
                            webbrowser.open(url)
                        print("Opened the existing local workspace.", flush=True)
                        return
                except (OSError, ValueError, httpx.HTTPError):
                    pass
                await asyncio.sleep(0.2)
            raise RuntimeError(
                "A local dashboard is starting or unavailable; retry shortly"
            )
        await serve_workspace(home, port, cdp, open_browser)
    finally:
        lock.close()


async def serve_workspace(home, port, cdp, open_browser):
    home = Path(home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = None
    try:
        capabilities = await asyncio.to_thread(client, {"op": "capabilities"}, home)
        if capabilities.get("protocol") != 2:
            raise RuntimeError("Runtime upgrade required")
    except (ConnectionRefusedError, FileNotFoundError):
        runtime = asyncio.create_task(serve(home, cdp))
        for _ in range(100):
            if runtime.done():
                await runtime
            try:
                if (await asyncio.to_thread(client, {"op": "capabilities"}, home)).get(
                    "protocol"
                ) == 2:
                    break
            except (ConnectionRefusedError, FileNotFoundError):
                pass
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Runtime did not start")
    token = secrets.token_urlsafe(32)
    # Token is a URL fragment, never a query parameter or HTTP access-log entry.
    url = f"http://127.0.0.1:{port}/#token={token}"
    private_link = home / "dashboard-url.txt"
    # Readers must never observe an empty or partially written unlock link.
    with tempfile.NamedTemporaryFile(
        mode="w", dir=home, prefix=".dashboard-link-", delete=False
    ) as out:
        temporary_link = Path(out.name)
        try:
            os.fchmod(out.fileno(), 0o600)
            out.write(url)
            out.flush()
            os.fsync(out.fileno())
            temporary_link.replace(private_link)
        finally:
            temporary_link.unlink(missing_ok=True)

    async def open_when_ready():
        import httpx

        for _ in range(50):
            try:
                async with httpx.AsyncClient() as c:
                    response = await c.get(f"http://127.0.0.1:{port}/")
                if response.status_code == 200:
                    if open_browser:
                        webbrowser.open(url)
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)

    opening = asyncio.create_task(open_when_ready())
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(home, token, port),
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
            )
        )
        print(
            f"Local workspace: http://127.0.0.1:{port}/ — use the launcher link to unlock.",
            flush=True,
        )
        await server.serve()
    finally:
        opening.cancel()
        if runtime:
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


def main():
    p = argparse.ArgumentParser(
        description="Open the merged local ApplyPilot / JobSignal workspace"
    )
    p.add_argument("--home", default=str(default_home()))
    p.add_argument("--port", type=int, default=8502)
    p.add_argument("--cdp", default="http://127.0.0.1:9333")
    p.add_argument("--no-open", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.home, a.port, a.cdp, not a.no_open))


if __name__ == "__main__":
    main()
