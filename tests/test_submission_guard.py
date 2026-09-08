from __future__ import annotations

import unittest

from src.submission_guard import (
    ai_rewrite_required,
    attestation_blockers,
    verify_application_ready,
)


class _FakeLocator:
    def __init__(self, fields: list[dict]) -> None:
        self.fields = fields

    def evaluate(self, script: str, root_selector: str) -> list[dict]:
        return self.fields


class _FakePage:
    def __init__(self, fields: list[dict]) -> None:
        self.fields = fields

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self.fields)


class SubmissionGuardTests(unittest.TestCase):
    def test_ai_prohibition_requires_rewrite_but_not_field_suppression(self) -> None:
        self.assertTrue(
            ai_rewrite_required(
                "The use of generative AI is prohibited in application answers.",
                use_ai=True,
            )
        )
        self.assertFalse(
            ai_rewrite_required(
                "The use of generative AI is prohibited in application answers.",
                use_ai=False,
            )
        )
        self.assertTrue(ai_rewrite_required("No AI tools permitted.", use_ai=True))

    def test_declaration_blocks_automatic_submission(self) -> None:
        self.assertTrue(attestation_blockers("I certify that all information is accurate."))

    def test_final_verifier_finds_missing_and_mismatched_values(self) -> None:
        fields = [
            {
                "tag": "input", "type": "email", "name": "email", "label": "Email",
                "required": True, "visible": True, "disabled": False,
                "value": "wrong@example.test", "checked": False, "files": [],
            },
            {
                "tag": "textarea", "type": "textarea", "name": "answer", "label": "Answer",
                "required": True, "visible": True, "disabled": False,
                "value": "", "checked": False, "files": [],
            },
            {
                "tag": "input", "type": "file", "name": "resume", "label": "Resume",
                "required": True, "visible": False, "disabled": False,
                "value": "", "checked": False, "files": ["old_cv.pdf"],
            },
        ]
        report = verify_application_ready(
            _FakePage(fields),
            profile={"email": "candidate@example.test", "phone": "+44 7000 000000"},
            cv_path="/tmp/current_cv.pdf",
        )
        combined = " ".join(report.blockers).lower()
        self.assertIn("email", combined)
        self.assertIn("required field is empty", combined)
        self.assertIn("approved cv", combined)


if __name__ == "__main__":
    unittest.main()
