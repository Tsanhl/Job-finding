from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.answers import answer_from_profile, answer_question
from src.application_flow import (
    direct_application_intake_questions,
    missing_academic_details,
    missing_application_details,
)
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

    def test_demographic_choice_is_not_inferred(self) -> None:
        self.assertEqual(
            answer_question("What is your gender?", {}, use_ai=False),
            "",
        )

    def test_law_direct_apply_always_asks_full_intake(self) -> None:
        questions = direct_application_intake_questions(
            {},
            "",
            application_type="law",
            target_url="",
        )
        combined = " ".join(questions).lower()
        self.assertIn("application question", combined)
        self.assertIn("sponsorship", combined)
        self.assertIn("module marks", combined)
        self.assertIn("final submission", combined)

    def test_graduate_direct_apply_includes_assessment_warning(self) -> None:
        questions = direct_application_intake_questions(
            {},
            "",
            application_type="graduate",
            target_url="",
        )
        combined = " ".join(questions).lower()
        self.assertIn("assessment centre", combined)
        self.assertIn("competency", combined)

    def test_academic_preflight_accepts_other_school_qualification_systems(self) -> None:
        profile = {
            "education": {
                "institution": "Example University",
                "degree": "Example Degree",
                "start": "2022-09",
                "end": "2025-06",
                "classification": "Pending",
                "modules": [{"name": "Example Module", "mark": "Pending"}],
            },
            "school_qualifications": {
                "type": "International Baccalaureate",
                "school": "Example School",
                "completion_year": "2022",
                "results": [{"subject": "Mathematics", "grade": "6"}],
            },
        }
        self.assertEqual(missing_academic_details(profile), [])

    def test_live_preflight_does_not_block_when_form_has_no_academic_questions(self) -> None:
        self.assertEqual(
            missing_academic_details(
                {},
                application_text="Upload your CV and provide your availability.",
                require_all=False,
            ),
            [],
        )

    def test_live_preflight_asks_only_when_portal_requests_academics(self) -> None:
        missing = missing_academic_details(
            {},
            application_text="List all A-level subjects and grades.",
            require_all=False,
        )
        combined = " ".join(missing).lower()
        self.assertIn("school qualification system", combined)
        self.assertIn("school subject", combined)
        self.assertNotIn("university", combined)

    def test_degree_request_does_not_require_unrequested_module_marks(self) -> None:
        missing = missing_academic_details(
            {},
            application_text="What is your degree subject?",
            require_all=False,
        )
        combined = " ".join(missing).lower()
        self.assertIn("degree type and subject", combined)
        self.assertNotIn("module", combined)
        self.assertNotIn("school qualification", combined)

    def test_secondary_results_are_available_to_form_filling(self) -> None:
        profile = {
            "school_qualifications": {
                "type": "A levels",
                "results": [
                    {"subject": "Mathematics", "grade": "A*"},
                    {"subject": "History", "grade": "A"},
                ],
            }
        }
        self.assertEqual(
            answer_from_profile("A-level subject 1", profile),
            "Mathematics",
        )
        self.assertEqual(
            answer_from_profile("A-level grade 2", profile),
            "A",
        )
        self.assertIn(
            "Mathematics: A*",
            answer_from_profile("List all A-level subjects and grades", profile) or "",
        )

    def test_university_module_marks_are_available_to_form_filling(self) -> None:
        profile = {
            "education": {
                "modules": [
                    {"name": "Contract Law", "mark": "70", "year": "2"},
                ]
            }
        }
        self.assertEqual(
            answer_from_profile("University module 1", profile),
            "Contract Law",
        )
        self.assertEqual(
            answer_from_profile("University module mark 1", profile),
            "70",
        )

    def test_template_does_not_contain_candidate_specific_defaults(self) -> None:
        letter = generate_cover_letter_template({}, company="Example", role="Intern")
        self.assertNotIn("University", letter)
        self.assertNotIn("sponsorship required", letter)
        self.assertIn("[Your name]", letter)


if __name__ == "__main__":
    unittest.main()
