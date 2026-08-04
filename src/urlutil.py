from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def normalize_job_url(url: str) -> str:
    """Normalize LinkedIn/job URLs for dedupe (strip query/fragment, trailing slash)."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("/"):
        url = "https://www.linkedin.com" + url
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    # Keep only /jobs/view/<id> core when present
    if "/jobs/view/" in path:
        parts = path.split("/jobs/view/")
        job_id = parts[1].split("/")[0]
        path = f"/jobs/view/{job_id}"
    clean = urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))
    return clean
