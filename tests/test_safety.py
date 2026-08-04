from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.answers import answer_question
from src.application_flow import missing_application_details
from src.cover_letter import generate_cover_letter_template


class SafetyFlowTests(unittest.TestCase):
    def test_preflight_lists_missing_details(self) -> None:
        missing = missing_application_details(
            {},
            "",
            keywords="",
            location="",
        )
        self.assertTrue(missing)
        self.assertTrue(any("full name" in item for item in missing))
        self.assertTrue(any("CV PDF" in item for item in missing))

    def test_preflight_passes_only_with_explicit_profile_facts(self) -> None:
        profile = {
            "full_name": "Example Candidate",
            "email": "candidate@example.test",
            "phone": "+1 555 0100",
            "location": "Example City",
            "answers": {
                "authorized_to_work": "Yes",
                "require_sponsorship": "No",
                "start_date": "Available immediately",
            },
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf") as cv:
            missing = missing_application_details(
                profile,
                Path(cv.name),
                keywords="intern",
                location="Example City",
            )
        self.assertEqual(missing, [])

    def test_unknown_question_is_not_guessed(self) -> None:
        self.assertEqual(
            answer_question(
                "What is your security clearance level?",
                {},
                use_ai=False,
            ),
            "",
        )

    def test_template_does_not_contain_candidate_specific_defaults(self) -> None:
        letter = generate_cover_letter_template({}, company="Example", role="Intern")
        self.assertNotIn("University", letter)
        self.assertNotIn("sponsorship required", letter)
        self.assertIn("[Your name]", letter)


if __name__ == "__main__":
    unittest.main()
