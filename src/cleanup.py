from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .urlutil import normalize_job_url


def cleanup_run_logs(output_dir: str | Path, keep: int = 3) -> list[Path]:
    """Keep only the newest LinkedIn run logs; delete the rest (repo stays clean)."""
    out = Path(output_dir)
    if not out.exists():
        return []
    files = sorted(out.glob("linkedin_run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)
        removed.append(stale)
    for pattern in (
        "marex_*.txt",
        "marex_*.png",
        "cover_letter_test-*.txt",
        "needs_review_finish_*.json",
    ):
        extras = sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in extras[keep:]:
            f.unlink(missing_ok=True)
            removed.append(f)
    return removed


def load_applied_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {normalize_job_url(u) for u in data.get("urls", []) if u}
    except Exception:
        return set()


def save_applied_urls(path: Path, urls: set[str], *, max_urls: int = 500) -> None:
    """Persist only normalized job URLs for skip-deduping."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {normalize_job_url(u) for u in urls if u}
    trimmed = sorted(normalized)[-max_urls:]
    path.write_text(json.dumps({"urls": trimmed}, indent=2) + "\n", encoding="utf-8")


def merge_applied_from_summary(summary: dict[str, Any], history_path: Path) -> set[str]:
    urls = load_applied_urls(history_path)
    for r in summary.get("results", []):
        status = r.get("status")
        if status in {"submitted-confirmed", "applied"} and r.get("url"):
            urls.add(normalize_job_url(r["url"]))
    save_applied_urls(history_path, urls)
    return urls


def sanitize_summary_for_disk(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep title/url/status/detail; drop company for repo cleanliness."""
    clean = dict(summary)
    clean["results"] = []
    for result in summary.get("results", []):
        sanitized = {
            "title": result.get("title", ""),
            "url": normalize_job_url(result.get("url", "")),
            "status": result.get("status", ""),
            "detail": result.get("detail", ""),
            "blockers": list(result.get("blockers") or []),
            "actions": list(result.get("actions") or []),
            "field_evidence": list(result.get("field_evidence") or []),
        }
        if result.get("legacy_status"):
            sanitized["legacy_status"] = result["legacy_status"]
        clean["results"].append(sanitized)
    return clean


def append_needs_review_queue(output_dir: str | Path, results: list[dict[str, Any]]) -> Path:
    """Persist unfinished jobs so tomorrow can resume."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "needs_review_queue.json"
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    by_url = {normalize_job_url(x.get("url", "")): x for x in existing if x.get("url")}
    for r in results:
        if not r.get("url"):
            continue
        normalized = normalize_job_url(r["url"])
        if r.get("status") in {
            "submitted-confirmed",
            "preview-ready",
            "skipped-unsuitable",
            "applied",
            "skipped",
            "dry_run",
        }:
            by_url.pop(normalized, None)
        elif r.get("status") in {
            "review-ready",
            "needs-information",
            "needs-authentication",
            "policy-blocked",
            "unsupported",
            "failed-retryable",
            "submission-disabled",
            "submission-unconfirmed",
            "needs_review",
            "needs_info",
            "needs_signup",
            "error",
        }:
            by_url[normalized] = {
                "url": normalize_job_url(r["url"]),
                "title": r.get("title", ""),
                "detail": r.get("detail", ""),
                "status": r.get("status", ""),
            }
    path.write_text(json.dumps(list(by_url.values()), indent=2) + "\n", encoding="utf-8")
    return path
