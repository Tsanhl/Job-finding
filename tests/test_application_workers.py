from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.application_workers import (
    ApplicationBatchRun,
    ApplicationTask,
    ApplicationWorkerResult,
    REVIEW_READY_STATUS,
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
    def prepared_tasks(self, count: int) -> list[ApplicationTask]:
        return [
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
            for index in range(count)
        ]

    def wait_for(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for application worker state")
            time.sleep(0.01)

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

    def test_automatic_worker_cap_and_per_application_ai_choice(self) -> None:
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
            tasks = self.prepared_tasks(10)
            with patch("src.application_workers._run_application_worker", side_effect=fake_worker):
                summary = run_application_batch(
                    tasks,
                    profile=complete_profile(),
                    defaults={},
                    default_cv_path=cv.name,
                    use_ai=True,
                )

        self.assertEqual(summary["worker_count"], 10)
        self.assertTrue(observed_ai["task-0"])
        self.assertTrue(all(not observed_ai[f"task-{index}"] for index in range(1, 10)))
        self.assertEqual(summary["final_submission"], "manual_only")
        self.assertEqual(summary["portfolio_status"], "ready_for_manual_review")
        self.assertEqual(summary["incomplete_application_ids"], [])

    def test_queue_file_accepts_at_most_ten_applications(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            path = Path(temporary_root) / "applications.local.json"
            path.write_text(
                json.dumps(
                    {"applications": [{"id": f"task-{index}"} for index in range(11)]}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "at most 10"):
                load_application_tasks(path)

    def test_background_batch_runs_only_selected_concurrency(self) -> None:
        release = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        def fake_worker(task: ApplicationTask, **kwargs) -> ApplicationWorkerResult:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            release.wait(2)
            with lock:
                active -= 1
            return ApplicationWorkerResult(
                task_id=task.task_id,
                company=task.company,
                role=task.role,
                status="ready_for_manual_review",
                detail="prepared",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            run = ApplicationBatchRun(
                self.prepared_tasks(5),
                profile=complete_profile(),
                defaults={},
                default_cv_path=cv.name,
                worker_count=3,
                use_ai=True,
                worker_fn=fake_worker,
            )
            run.start()
            self.wait_for(lambda: run.snapshot()["counts"].get("running") == 3)
            live = run.snapshot()
            self.assertEqual(live["counts"].get("queued"), 2)
            self.assertTrue(live["running"])
            self.assertEqual(live["portfolio_status"], "running")
            release.set()
            run.join(2)

        final = run.snapshot()
        self.assertFalse(final["running"])
        self.assertEqual(final["counts"], {"ready_for_manual_review": 5})
        self.assertEqual(final["portfolio_status"], "ready_for_manual_review")
        self.assertEqual(final["review_ready_count"], 5)
        self.assertEqual(maximum_active, 3)

    def test_cancel_stops_queued_applications_only(self) -> None:
        release = threading.Event()
        started: list[str] = []
        lock = threading.Lock()

        def fake_worker(task: ApplicationTask, **kwargs) -> ApplicationWorkerResult:
            with lock:
                started.append(task.task_id)
            release.wait(2)
            return ApplicationWorkerResult(
                task_id=task.task_id,
                company=task.company,
                role=task.role,
                status="ready_for_manual_review",
                detail="prepared",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            run = ApplicationBatchRun(
                self.prepared_tasks(5),
                profile=complete_profile(),
                defaults={},
                default_cv_path=cv.name,
                worker_count=2,
                worker_fn=fake_worker,
            )
            run.start()
            self.wait_for(lambda: len(started) == 2)
            run.cancel()
            cancelled = run.snapshot()
            self.assertEqual(cancelled["counts"].get("cancelled"), 3)
            self.assertEqual(cancelled["counts"].get("running"), 2)
            release.set()
            run.join(2)

        final = run.snapshot()
        self.assertFalse(final["running"])
        self.assertEqual(len(started), 2)
        self.assertEqual(final["counts"].get("cancelled"), 3)
        self.assertEqual(final["counts"].get("ready_for_manual_review"), 2)
        self.assertEqual(final["portfolio_status"], "incomplete")
        self.assertEqual(len(final["incomplete_application_ids"]), 3)

    def test_partial_worker_result_keeps_portfolio_incomplete(self) -> None:
        def fake_worker(task: ApplicationTask, **kwargs) -> ApplicationWorkerResult:
            status = (
                "needs_user_attention"
                if task.task_id == "task-1"
                else "ready_for_manual_review"
            )
            return ApplicationWorkerResult(
                task_id=task.task_id,
                company=task.company,
                role=task.role,
                status=status,
                detail="upload permission required" if status != REVIEW_READY_STATUS else "prepared",
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            with patch("src.application_workers._run_application_worker", side_effect=fake_worker):
                summary = run_application_batch(
                    self.prepared_tasks(3),
                    profile=complete_profile(),
                    defaults={},
                    default_cv_path=cv.name,
                )

        self.assertEqual(summary["portfolio_status"], "incomplete")
        self.assertEqual(summary["review_ready_count"], 2)
        self.assertEqual(summary["incomplete_application_ids"], ["task-1"])


if __name__ == "__main__":
    unittest.main()
