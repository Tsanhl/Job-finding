from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.application_workers import (
    ApplicationTask,
    ApplicationWorkerResult,
    application_task_blockers,
    load_application_tasks,
    run_application_batch,
    validate_application_batch,
)


def complete_profile() -> dict:
    return {
        "full_name": "Example Candidate",
        "email": "candidate@example.test",
        "phone": "+1 555 0100",
        "location": "Example City",
        "answers": {
            "authorized_to_work": "Yes",
            "require_sponsorship": "No",
            "start_date": "Example start date",
        },
    }


class ApplicationWorkerTests(unittest.TestCase):
    def test_loads_multiple_unique_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "applications.local.json"
            path.write_text(
                json.dumps(
                    {
                        "applications": [
                            {
                                "id": "law-one",
                                "application_type": "law",
                                "company": "Example LLP",
                                "role": "Vacation Scheme",
                                "url": "https://example.com/one",
                            },
                            {
                                "id": "graduate-two",
                                "application_type": "graduate",
                                "company": "Example Organisation",
                                "role": "Graduate Scheme",
                                "url": "https://example.org/two",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            tasks = load_application_tasks(path)
        self.assertEqual([task.task_id for task in tasks], ["law-one", "graduate-two"])

    def test_rejects_duplicate_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "applications.local.json"
            path.write_text(
                json.dumps({"applications": [{"id": "same"}, {"id": "same"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_application_tasks(path)

    def test_each_task_requires_its_own_confirmations(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            task = ApplicationTask(
                task_id="example",
                application_type="other",
                company="Example",
                role="Example Role",
                url="https://example.com/apply",
            )
            blockers = application_task_blockers(
                task,
                profile=complete_profile(),
                default_cv_path=cv.name,
            )
        combined = " ".join(blockers).lower()
        self.assertIn("portal questions", combined)
        self.assertIn("ai policy", combined)
        self.assertIn("submission", combined)

    def test_batch_passes_without_unrequested_academic_data(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            tasks = [
                ApplicationTask(
                    task_id="one",
                    application_type="law",
                    company="Example LLP",
                    role="Example Programme",
                    url="https://example.com/one",
                    job_description="Submit a CV and confirm availability.",
                    intake_confirmed=True,
                    ai_policy_confirmed=True,
                    manual_submit_confirmed=True,
                ),
                ApplicationTask(
                    task_id="two",
                    application_type="graduate",
                    company="Example Organisation",
                    role="Example Scheme",
                    url="https://example.org/two",
                    job_description="Submit a CV and confirm availability.",
                    intake_confirmed=True,
                    ai_policy_confirmed=True,
                    manual_submit_confirmed=True,
                ),
            ]
            blockers = validate_application_batch(
                tasks,
                profile=complete_profile(),
                default_cv_path=cv.name,
            )
        self.assertEqual(blockers, {})

    def test_only_application_requesting_marks_is_blocked(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            tasks = [
                ApplicationTask(
                    task_id="no-marks",
                    application_type="law",
                    company="Example LLP",
                    role="Programme One",
                    url="https://example.com/one",
                    intake_confirmed=True,
                    ai_policy_confirmed=True,
                    manual_submit_confirmed=True,
                ),
                ApplicationTask(
                    task_id="needs-marks",
                    application_type="graduate",
                    company="Example Organisation",
                    role="Programme Two",
                    url="https://example.org/two",
                    portal_questions="List every A-level subject and grade.",
                    intake_confirmed=True,
                    ai_policy_confirmed=True,
                    manual_submit_confirmed=True,
                ),
            ]
            blockers = validate_application_batch(
                tasks,
                profile=complete_profile(),
                default_cv_path=cv.name,
            )
        self.assertNotIn("no-marks", blockers)
        self.assertIn("needs-marks", blockers)
        self.assertTrue(any("school subject" in item for item in blockers["needs-marks"]))

    def test_worker_cap_and_per_application_ai_choice(self) -> None:
        observed_ai: dict[str, bool] = {}

        def fake_worker(task: ApplicationTask, **kwargs) -> ApplicationWorkerResult:
            observed_ai[task.task_id] = kwargs["use_ai"]
            return ApplicationWorkerResult(
                task_id=task.task_id,
                company=task.company,
                role=task.role,
                status="ready_for_manual_review",
                detail="test",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            tasks = [
                ApplicationTask(
                    task_id=f"task-{index}",
                    application_type="other",
                    company="Example",
                    role=f"Role {index}",
                    url=f"https://example.com/{index}",
                    allow_ai=index == 0,
                    intake_confirmed=True,
                    ai_policy_confirmed=True,
                    manual_submit_confirmed=True,
                )
                for index in range(5)
            ]
            with patch("src.application_workers._run_application_worker", side_effect=fake_worker):
                summary = run_application_batch(
                    tasks,
                    profile=complete_profile(),
                    defaults={},
                    default_cv_path=cv.name,
                    worker_count=10,
                    use_ai=True,
                )

        self.assertEqual(summary["worker_count"], 4)
        self.assertTrue(observed_ai["task-0"])
        self.assertTrue(all(not observed_ai[f"task-{index}"] for index in range(1, 5)))
        self.assertEqual(summary["final_submission"], "manual_only")


if __name__ == "__main__":
    unittest.main()
