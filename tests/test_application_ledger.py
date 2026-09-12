from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.application_ledger import ApplicationLedger, LedgerCorruptionError


class ApplicationLedgerTests(unittest.TestCase):
    def test_corrupt_history_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text("{not valid json", encoding="utf-8")
            ledger = ApplicationLedger(path)
            with self.assertRaises(LedgerCorruptionError):
                ledger.skip_urls()

    def test_in_progress_work_never_expires_into_an_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ApplicationLedger(Path(directory) / "ledger.json")
            url = "https://jobs.example.test/pending"
            ledger.record(url, status="in_progress")
            self.assertIn(url, ledger.skip_urls())
            self.assertIn(url, ledger.reconciliation_urls())

    def test_review_ready_and_manual_completion_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ApplicationLedger(Path(directory) / "ledger.json")
            review_url = "https://www.linkedin.com/jobs/view/123?tracking=ignored"
            manual_url = "https://jobs.example.test/456"
            failed_url = "https://jobs.example.test/789"
            ledger.record(review_url, status="review_ready", title="Graduate Role")
            ledger.record(manual_url, status="completed_manually", title="Paralegal")
            ledger.record(failed_url, status="failed", title="Retry Me")

            skipped = ledger.skip_urls()

        self.assertIn("https://www.linkedin.com/jobs/view/123", skipped)
        self.assertIn(manual_url, skipped)
        self.assertNotIn(failed_url, skipped)


if __name__ == "__main__":
    unittest.main()
