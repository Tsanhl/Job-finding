from __future__ import annotations

import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.account_automation import (
    PortalCredentialManager,
    PortalCredentials,
    choose_verification_link,
    verification_message_matches,
)
from src.full_automation import (
    FullAutomationRequest,
    run_full_automation,
    suitability_blocker,
)
from src.linkedin_apply import ApplyResult


def complete_profile() -> dict:
    return {
        "full_name": "Example Candidate",
        "email": "candidate@example.test",
        "phone": "+1 555 0100",
        "location": "London",
        "answers": {
            "authorized_to_work": "Yes",
            "require_sponsorship": "No",
            "start_date": "Example start date",
        },
    }


class FullAutomationTests(unittest.TestCase):
    def test_auto_submit_is_disabled_before_discovery(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["graduate"],
                "locations": ["London"],
                "submission_policy": "auto-submit",
            }
        )
        with patch("src.full_automation.discover_linkedin_job_urls") as discover:
            result = run_full_automation(
                request,
                credentials=None,
                profile={},
                defaults={},
                cv_path="/tmp/cv.pdf",
                browser_data_dir="/tmp/browser",
                output_dir="/tmp/output",
            )
        discover.assert_not_called()
        self.assertEqual(result["status"], "submission-disabled")

    def test_corrupt_ledger_blocks_discovery(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {"keywords": ["graduate"], "locations": ["London"]}
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv, tempfile.TemporaryDirectory() as out:
            with open(f"{out}/application_ledger.json", "w", encoding="utf-8") as ledger:
                ledger.write("not-json")
            with (
                patch("src.full_automation.load_applied_urls", return_value=set()),
                patch("src.full_automation.discover_linkedin_job_urls") as discover,
            ):
                result = run_full_automation(
                    request,
                    credentials=None,
                    profile=complete_profile(),
                    defaults={},
                    cv_path=cv.name,
                    browser_data_dir="unused",
                    output_dir=out,
                )
        discover.assert_not_called()
        self.assertEqual(result["status"], "needs-information")

    def test_request_requires_explicit_submission_policy(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["graduate analyst"],
                "locations": ["London"],
                "submission_policy": "auto-submit",
                "max_applications": 10,
            }
        )
        self.assertEqual(request.submission_policy, "auto-submit")
        with self.assertRaisesRegex(ValueError, "submission_policy"):
            FullAutomationRequest.from_mapping(
                {
                    "keywords": ["graduate analyst"],
                    "locations": ["London"],
                    "submission_policy": "sometimes",
                }
            )

    def test_request_caps_full_automation_at_ten(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            FullAutomationRequest.from_mapping(
                {
                    "keywords": ["graduate analyst"],
                    "locations": ["London"],
                    "max_applications": 11,
                }
            )

    def test_portal_password_is_hidden_from_representation(self) -> None:
        credentials = PortalCredentials(
            email="candidate@example.test",
            password="synthetic-secret",
        )
        self.assertNotIn("synthetic-secret", repr(credentials))

    def test_verification_link_selection_ignores_unsubscribe(self) -> None:
        chosen = choose_verification_link(
            [
                ("Manage preferences", "https://mail.example/preferences/unsubscribe"),
                ("Verify email", "https://jobs.example/verify?token=synthetic"),
            ],
            expected_host="jobs.example",
        )
        self.assertEqual(chosen, "https://jobs.example/verify?token=synthetic")

    def test_verification_link_requires_matching_portal_domain(self) -> None:
        self.assertEqual(
            choose_verification_link(
                [("Verify email", "https://malicious.example/verify?token=synthetic")],
                expected_host="jobs.example",
            ),
            "",
        )

    def test_verification_message_requires_non_free_sender_and_company(self) -> None:
        self.assertTrue(
            verification_message_matches(
                company="Example Legal",
                sender_email="noreply@jobs.example",
                message_text="Confirm your Example Legal candidate account.",
            )
        )
        self.assertFalse(
            verification_message_matches(
                company="Example Legal",
                sender_email="random@gmail.com",
                message_text="Confirm your Example Legal candidate account.",
            )
        )
        self.assertFalse(
            verification_message_matches(
                company="Example Legal",
                sender_email="noreply@unrelated.test",
                message_text="Confirm your Example Legal candidate account.",
                expected_host="jobs.example",
            )
        )

    def test_unique_portal_credentials_are_stable_per_employer(self) -> None:
        with (
            patch("src.account_automation.sys.platform", "darwin"),
            patch.object(PortalCredentialManager, "_load_keychain", return_value=""),
            patch.object(PortalCredentialManager, "_save_keychain") as save,
        ):
            manager = PortalCredentialManager(
                email="candidate@example.test",
                generate_unique=True,
                save_to_keychain=True,
            )
            first = manager.for_portal("Employer One", "https://jobs.one.example/apply")
            again = manager.for_portal("Employer One", "https://jobs.one.example/apply")
            second = manager.for_portal("Employer Two", "https://jobs.two.example/apply")
        self.assertEqual(first.password, again.password)
        self.assertNotEqual(first.password, second.password)
        self.assertEqual(save.call_count, 2)
        self.assertNotIn(first.password, repr(manager))

    def test_existing_keychain_password_is_reused(self) -> None:
        with (
            patch("src.account_automation.sys.platform", "darwin"),
            patch.object(
                PortalCredentialManager,
                "_load_keychain",
                return_value="Existing-Portal-Secret-1!",
            ),
            patch.object(PortalCredentialManager, "_save_keychain") as save,
        ):
            manager = PortalCredentialManager(
                email="candidate@example.test",
                generate_unique=True,
                save_to_keychain=True,
            )
            credentials = manager.for_portal(
                "Employer One",
                "https://jobs.one.example/apply",
            )
        self.assertEqual(credentials.password, "Existing-Portal-Secret-1!")
        save.assert_not_called()

    def test_suitability_filter_rejects_unpaid_training(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {"keywords": ["legal graduate"], "locations": ["London"]}
        )
        reason = suitability_blocker(
            request,
            title="Risk and Compliance Work Placement",
            description=(
                "This unpaid programme is focused on learning. You will not be "
                "working for the company or replacing paid employees."
            ),
        )
        self.assertIn("unpaid", reason.lower())

    def test_suitability_filter_rejects_completed_employer(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["legal graduate"],
                "locations": ["London"],
                "excluded_employers": ["Willkie Farr", "Clyde & Co"],
            }
        )
        reason = suitability_blocker(
            request,
            title="Graduate Legal Role",
            description="The role is offered by Clyde & Co in London.",
        )
        self.assertIn("Clyde & Co", reason)

    def test_suitability_filter_rejects_experience_above_limit(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["paralegal"],
                "locations": ["London"],
                "max_required_experience_years": 1,
            }
        )
        reason = suitability_blocker(
            request,
            title="Legal Assistant",
            description="Applicants need at least 2 years of conveyancing experience.",
        )
        self.assertIn("2 years", reason)

    def test_suitability_filter_can_be_relaxed(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["paralegal"],
                "locations": ["London"],
                "paid_roles_only": False,
                "max_required_experience_years": None,
            }
        )
        self.assertEqual(
            suitability_blocker(
                request,
                title="Placement",
                description="This unpaid programme requires 3+ years experience.",
            ),
            "",
        )

    def test_full_automation_uses_one_worker_per_application(self) -> None:
        barrier = threading.Barrier(3)

        def fake_apply(url: str, **kwargs) -> ApplyResult:
            barrier.wait(timeout=2)
            return ApplyResult("Graduate Role", "Example", url, "needs_review", "ready")

        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["graduate"],
                "locations": ["London"],
                "max_applications": 3,
                "submission_policy": "review",
            }
        )
        credentials = PortalCredentials(
            email="candidate@example.test",
            password="synthetic-secret",
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv, tempfile.TemporaryDirectory() as out:
            with (
                patch(
                    "src.full_automation.discover_linkedin_job_urls",
                    return_value=[
                        "https://www.linkedin.com/jobs/view/1",
                        "https://www.linkedin.com/jobs/view/2",
                        "https://www.linkedin.com/jobs/view/3",
                    ],
                ),
                patch("src.full_automation._apply_one_job", side_effect=fake_apply),
                patch("src.full_automation.load_applied_urls", return_value=set()),
                patch("src.full_automation.save_applied_urls"),
                patch("src.full_automation.append_needs_review_queue"),
            ):
                summary = run_full_automation(
                    request,
                    credentials=credentials,
                    profile=complete_profile(),
                    defaults={},
                    cv_path=cv.name,
                    browser_data_dir="unused",
                    output_dir=out,
                    use_ai=False,
                )

        self.assertEqual(summary["worker_count"], 3)
        self.assertEqual(len(summary["results"]), 3)
        self.assertNotIn("synthetic-secret", json.dumps(summary))

    def test_full_automation_replaces_rejected_candidates(self) -> None:
        request = FullAutomationRequest.from_mapping(
            {
                "keywords": ["graduate"],
                "locations": ["London"],
                "max_applications": 3,
                "submission_policy": "review",
            }
        )
        candidates = [f"https://www.linkedin.com/jobs/view/{index}" for index in range(1, 6)]

        def fake_apply(url: str, **kwargs) -> ApplyResult:
            job_id = int(url.rsplit("/", 1)[-1])
            status = "skipped" if job_id < 3 else "needs_review"
            return ApplyResult(f"Role {job_id}", "Example", url, status, "test")

        credentials = PortalCredentials(
            email="candidate@example.test",
            password="synthetic-secret",
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv, tempfile.TemporaryDirectory() as out:
            with (
                patch("src.full_automation.discover_linkedin_job_urls", return_value=candidates),
                patch("src.full_automation._apply_one_job", side_effect=fake_apply),
                patch("src.full_automation.load_applied_urls", return_value=set()),
                patch("src.full_automation.save_applied_urls"),
                patch("src.full_automation.append_needs_review_queue"),
            ):
                summary = run_full_automation(
                    request,
                    credentials=credentials,
                    profile=complete_profile(),
                    defaults={},
                    cv_path=cv.name,
                    browser_data_dir="unused",
                    output_dir=out,
                    use_ai=False,
                )

        self.assertEqual(summary["prepared"], 3)
        self.assertEqual(summary["review_ready"], 3)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["searched"], 5)
        self.assertEqual(summary["status"], "review-ready")
        self.assertEqual(summary["counts"]["skipped-unsuitable"], 2)


if __name__ == "__main__":
    unittest.main()
