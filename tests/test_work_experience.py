from __future__ import annotations

import unittest
from unittest.mock import patch

from src.work_experience import (
    WorkExperienceEntry,
    _date_value,
    fill_repeatable_work_experience,
    work_experience_entries,
)


class _Control:
    def __init__(self, input_type: str = "text", placeholder: str = "") -> None:
        self.attributes = {"type": input_type, "placeholder": placeholder}

    def get_attribute(self, name: str) -> str:
        return self.attributes.get(name, "")


class _BodyLocator:
    def __init__(self, page: "_Page") -> None:
        self.page = page

    def inner_text(self, timeout: int = 0) -> str:
        return self.page.body


class _Button:
    def click(self, timeout: int = 0) -> None:
        return None


class _Page:
    def __init__(self, body: str = "") -> None:
        self.body = body

    def locator(self, selector: str) -> _BodyLocator:
        if selector != "body":
            raise AssertionError(f"Unexpected selector: {selector}")
        return _BodyLocator(self)

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None


class WorkExperienceTests(unittest.TestCase):
    def test_extracts_every_unique_structured_record(self) -> None:
        profile = {
            "work_experience": [
                {
                    "employer": "Example LLP",
                    "position": "Work Experience Week",
                    "form_type": "In person internship",
                    "start_date": "2026-06-22",
                    "end_date": "2026-06-26",
                    "cv_bullets": ["Researched a verified legal issue."],
                },
                {
                    "employer": "Example Press",
                    "position": "Panel Member",
                    "start_date": "2024-11",
                    "end_date": "2026-04",
                    "summary": "Reviewed educational materials.",
                },
                {
                    "employer": "Example LLP",
                    "position": "Work Experience Week",
                    "start_date": "2026-06-22",
                    "end_date": "2026-06-26",
                },
            ]
        }
        entries = work_experience_entries(profile)
        self.assertEqual([entry.employer for entry in entries], ["Example LLP", "Example Press"])
        self.assertEqual(entries[0].portal_title, "Intern")
        self.assertEqual(entries[0].description, "Researched a verified legal issue.")

    def test_repeatable_filler_processes_all_missing_records(self) -> None:
        profile = {
            "work_experience": [
                {"employer": "Existing LLP", "position": "Intern"},
                {"employer": "Second LLP", "position": "Assistant"},
                {"employer": "Third Ltd", "position": "Tutor"},
            ]
        }
        page = _Page("Experience\nExisting LLP\nIntern")

        def fake_fill(editor_page: _Page, entry: WorkExperienceEntry) -> tuple[bool, str]:
            editor_page.body += f"\n{entry.employer}\n{entry.title}"
            return True, ""

        with (
            patch("src.work_experience._find_experience_add_button", return_value=_Button()),
            patch("src.work_experience._fill_experience_editor", side_effect=fake_fill),
        ):
            report = fill_repeatable_work_experience(page, profile=profile)

        self.assertEqual(report.skipped, ("Existing LLP",))
        self.assertEqual(report.added, ("Second LLP", "Third Ltd"))
        self.assertEqual(report.blockers, ())

    def test_month_precision_is_not_converted_to_an_invented_day(self) -> None:
        self.assertIsNone(_date_value(_Control("date"), "2024-11"))
        self.assertEqual(_date_value(_Control("month"), "2024-11"), "2024-11")
        self.assertEqual(
            _date_value(_Control("text", "MM/YYYY"), "2024-11"),
            "11/2024",
        )
        self.assertEqual(
            _date_value(_Control("date"), "2024-11-23"),
            "2024-11-23",
        )

    def test_one_blocked_record_does_not_prevent_later_records(self) -> None:
        profile = {
            "work_experience": [
                {"employer": "Blocked LLP", "position": "Intern"},
                {"employer": "Later Ltd", "position": "Tutor"},
            ]
        }
        page = _Page("Experience")

        def fake_fill(editor_page: _Page, entry: WorkExperienceEntry) -> tuple[bool, str]:
            if entry.employer == "Blocked LLP":
                return False, "exact date required"
            editor_page.body += f"\n{entry.employer}\n{entry.portal_title}"
            return True, ""

        with (
            patch("src.work_experience._find_experience_add_button", return_value=_Button()),
            patch("src.work_experience._fill_experience_editor", side_effect=fake_fill),
            patch("src.work_experience._dismiss_experience_editor", return_value=True),
        ):
            report = fill_repeatable_work_experience(page, profile=profile)

        self.assertEqual(report.added, ("Later Ltd",))
        self.assertEqual(report.blockers, ("Blocked LLP: exact date required",))


if __name__ == "__main__":
    unittest.main()
