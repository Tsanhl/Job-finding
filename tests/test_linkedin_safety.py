from __future__ import annotations

import unittest
from unittest.mock import patch

from src.application_models import AiPolicy, OutcomeStatus, RunMode
from src.linkedin_apply import (
    LINKEDIN_PROGRESS_LABELS,
    apply_to_current_job,
    complete_easy_apply,
    run_linkedin_auto_apply,
)


class _Locator:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._count

    def is_visible(self, timeout: int = 0) -> bool:
        return bool(self._count)


class _ClosedModalPage:
    def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    def locator(self, selector: str) -> _Locator:
        if "Application sent" in selector:
            return _Locator(1)
        return _Locator(0)


class _ListingPage:
    url = "https://www.linkedin.com/jobs/view/123"

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    def locator(self, selector: str):
        raise AssertionError(f"Preview unexpectedly queried an Apply control: {selector}")


class LinkedInSafetyTests(unittest.TestCase):
    def test_progress_labels_exclude_final_and_ambiguous_actions(self) -> None:
        self.assertNotIn("Continue", LINKEDIN_PROGRESS_LABELS)
        self.assertFalse(
            any(
                marker in label.lower()
                for label in LINKEDIN_PROGRESS_LABELS
                for marker in ("apply", "submit")
            )
        )

    def test_listing_preview_does_not_open_apply_form(self) -> None:
        with patch(
            "src.linkedin_apply._safe_text",
            side_effect=["Role", "Example", "Job description"],
        ):
            result = apply_to_current_job(
                _ListingPage(),
                profile={},
                defaults={},
                cv_path="/tmp/cv.pdf",
                use_ai=False,
                dry_run=True,
                log=None,
                mode=RunMode.LOCAL_PREVIEW,
            )
        self.assertEqual(result.status, OutcomeStatus.PREVIEW_READY)

    def test_modal_closure_is_not_submission_confirmation(self) -> None:
        result = complete_easy_apply(
            _ClosedModalPage(),
            profile={},
            defaults={},
            cv_path="/tmp/cv.pdf",
            job_context="",
            title="Role",
            company="Example",
            url="https://example.test/job",
            use_ai=False,
            dry_run=False,
            log=None,
            mode=RunMode.ASSISTED_REVIEW,
            ai_policy=AiPolicy.UNKNOWN,
        )
        self.assertEqual(result.status, OutcomeStatus.SUBMISSION_UNCONFIRMED)

    def test_legacy_submit_flag_is_rejected_before_browser_access(self) -> None:
        with patch("src.browser_session.get_context") as get_context:
            summary = run_linkedin_auto_apply(
                keywords="graduate",
                location="London",
                cv_path="/tmp/cv.pdf",
                browser_data_dir="/tmp/browser",
                profile={},
                allow_submit=True,
            )
        get_context.assert_not_called()
        self.assertEqual(
            summary.results[0].status,
            OutcomeStatus.SUBMISSION_DISABLED,
        )


if __name__ == "__main__":
    unittest.main()
