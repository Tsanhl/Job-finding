"""Persistent, thread-safe lifecycle tracking for job applications."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .urlutil import normalize_job_url


ACTIVE_OR_COMPLETE_STATUSES = {
    "review_ready",
    "review-ready",
    "submitted",
    "submitted-confirmed",
    "completed_manually",
    "submitted-user-reported",
    "rejected",
    "employer-rejected",
    "submission-unconfirmed",
}


class LedgerCorruptionError(RuntimeError):
    """Raised when duplicate-sensitive history cannot be trusted."""


class ApplicationLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LedgerCorruptionError(
                f"Application ledger is unreadable: {self.path}"
            ) from exc
        if not isinstance(raw, dict):
            raise LedgerCorruptionError("Application ledger root must be an object")
        records = raw.get("applications", raw)
        if not isinstance(records, dict):
            raise LedgerCorruptionError(
                f"Application ledger has an unsupported structure: {self.path}"
            )
        if any(not url or not isinstance(record, dict) for url, record in records.items()):
            raise LedgerCorruptionError("Application ledger contains malformed records")
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
        skipped: set[str] = set()
        for url, record in self.records().items():
            status = record.get("status")
            if status in ACTIVE_OR_COMPLETE_STATUSES:
                skipped.add(url)
                continue
            if status != "in_progress":
                continue
            # A lease expiring does not prove that an external side effect did not
            # occur. Keep it blocked until a user or receipt reconciles it.
            skipped.add(url)
        return skipped

    def reconciliation_urls(self) -> set[str]:
        return {
            url
            for url, record in self.records().items()
            if record.get("status") in {"in_progress", "submission-unconfirmed"}
        }

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
