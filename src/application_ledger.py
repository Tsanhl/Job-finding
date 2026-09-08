"""Persistent, thread-safe lifecycle tracking for job applications."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .urlutil import normalize_job_url


ACTIVE_OR_COMPLETE_STATUSES = {
    "review_ready",
    "submitted",
    "completed_manually",
    "rejected",
}


class ApplicationLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        records = raw.get("applications", raw) if isinstance(raw, dict) else {}
        if not isinstance(records, dict):
            return {}
        return {
            normalize_job_url(url): dict(record)
            for url, record in records.items()
            if url and isinstance(record, dict)
        }

    def _save_unlocked(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"applications": records}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def records(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._load_unlocked()

    def skip_urls(self) -> set[str]:
        now = datetime.now(timezone.utc)
        skipped: set[str] = set()
        for url, record in self.records().items():
            status = record.get("status")
            if status in ACTIVE_OR_COMPLETE_STATUSES:
                skipped.add(url)
                continue
            if status != "in_progress":
                continue
            try:
                updated = datetime.fromisoformat(str(record.get("updated_at") or ""))
            except ValueError:
                continue
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now - updated < timedelta(hours=2):
                skipped.add(url)
        return skipped

    def record(
        self,
        url: str,
        *,
        status: str,
        title: str = "",
        company: str = "",
        detail: str = "",
        submission_policy: str = "",
    ) -> None:
        normalized = normalize_job_url(url)
        if not normalized:
            return
        with self._lock:
            records = self._load_unlocked()
            previous = records.get(normalized, {})
            records[normalized] = {
                "url": normalized,
                "title": title or previous.get("title", ""),
                "company": company or previous.get("company", ""),
                "status": status,
                "detail": detail,
                "submission_policy": submission_policy or previous.get("submission_policy", ""),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_unlocked(records)
