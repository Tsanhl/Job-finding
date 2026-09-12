from __future__ import annotations

import unittest

from src.field_manifest import (
    FieldKind,
    ManifestField,
    answer_limit_blocker,
    classify_field,
    extract_field_manifest,
    resolve_deterministic_answer,
)


class _ManifestRoot:
    @property
    def first(self):
        return self

    def evaluate(self, script: str, selector: str):
        return [
            {
                "index": 0,
                "field_id": "motivation",
                "section": "Application questions",
                "question": "Why this role? Maximum 2 words",
                "tag": "textarea",
                "input_type": "",
                "role": "",
                "options": ["One", "Two"],
                "required": True,
                "visible": True,
                "disabled": False,
                "observed_value": "",
                "checked": False,
                "max_characters": None,
            }
        ]


class _ManifestPage:
    def locator(self, selector: str) -> _ManifestRoot:
        return _ManifestRoot()


def manifest_field(kind: FieldKind, question: str, **overrides) -> ManifestField:
    values = {
        "index": 0,
        "field_id": "field",
        "section": "",
        "question": question,
        "tag": "input",
        "input_type": "text",
        "role": "",
        "options": (),
        "required": True,
        "visible": True,
        "disabled": False,
        "observed_value": "",
        "checked": False,
        "max_characters": None,
        "max_words": None,
        "kind": kind,
    }
    values.update(overrides)
    return ManifestField(**values)


class FieldManifestTests(unittest.TestCase):
    def test_extracts_structure_options_and_limits_before_filling(self) -> None:
        fields = extract_field_manifest(_ManifestPage())
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].kind, FieldKind.MOTIVATION)
        self.assertEqual(fields[0].options, ("One", "Two"))
        self.assertEqual(fields[0].max_words, 2)
        self.assertTrue(fields[0].required)

    def test_identity_and_employer_names_are_distinct(self) -> None:
        self.assertEqual(classify_field("First name"), FieldKind.FIRST_NAME)
        self.assertEqual(classify_field("Surname"), FieldKind.LAST_NAME)
        self.assertEqual(classify_field("Your full name"), FieldKind.FULL_NAME)
        employer = manifest_field(FieldKind.EMPLOYER_NAME, "Employer name")
        answer = resolve_deterministic_answer(
            employer,
            profile={"full_name": "Example Candidate"},
            defaults={},
        )
        self.assertEqual(answer.value, "")
        self.assertIn("work-history", answer.blocker)

    def test_narrative_types_are_not_all_cover_letters(self) -> None:
        self.assertEqual(classify_field("Upload your cover letter"), FieldKind.COVER_LETTER)
        self.assertEqual(classify_field("Why are you interested in this role?"), FieldKind.MOTIVATION)
        self.assertEqual(classify_field("Describe a situation where you led a team"), FieldKind.COMPETENCY)
        self.assertEqual(classify_field("Responsibilities", tag="textarea"), FieldKind.RESPONSIBILITIES)
        self.assertEqual(classify_field("Additional information", tag="textarea"), FieldKind.ADDITIONAL_INFORMATION)

    def test_sensitive_choice_requires_an_exact_profile_answer(self) -> None:
        field = manifest_field(FieldKind.DEMOGRAPHIC, "Gender")
        unresolved = resolve_deterministic_answer(field, profile={}, defaults={})
        self.assertEqual(unresolved.value, "")
        self.assertIn("Candidate choice required", unresolved.blocker)
        resolved = resolve_deterministic_answer(
            field,
            profile={"answers": {"Gender": "Prefer not to say"}},
            defaults={},
        )
        self.assertEqual(resolved.value, "Prefer not to say")

    def test_character_and_word_limits_block_instead_of_truncating(self) -> None:
        character_field = manifest_field(
            FieldKind.FIRST_NAME,
            "First name",
            max_characters=3,
        )
        word_field = manifest_field(
            FieldKind.MOTIVATION,
            "Motivation",
            max_words=2,
        )
        self.assertIn("5 characters", answer_limit_blocker(character_field, "Alice"))
        self.assertIn("3 words", answer_limit_blocker(word_field, "one two three"))

    def test_exact_narrative_answer_is_available_when_ai_is_off(self) -> None:
        field = manifest_field(FieldKind.MOTIVATION, "Why this role?")
        answer = resolve_deterministic_answer(
            field,
            profile={"answers": {"Why this role?": "Verified motivation"}},
            defaults={},
        )
        self.assertEqual(answer.value, "Verified motivation")
        self.assertEqual(answer.source, "profile.answers.exact_question")

    def test_placeholder_select_value_is_not_treated_as_answered(self) -> None:
        field = manifest_field(
            FieldKind.ELIGIBILITY,
            "Right to work",
            tag="select",
            observed_value="Select an option",
        )
        self.assertFalse(field.has_observed_value)

    def test_custom_dropdown_and_document_slots_are_explicit(self) -> None:
        self.assertEqual(
            classify_field("Country", tag="button", role="combobox"),
            FieldKind.CUSTOM_DROPDOWN,
        )
        self.assertEqual(
            classify_field("CV / Résumé", input_type="file"),
            FieldKind.CV_UPLOAD,
        )
        self.assertEqual(
            classify_field("Cover letter", input_type="file"),
            FieldKind.COVER_LETTER_UPLOAD,
        )
        self.assertEqual(
            classify_field("Academic transcript", input_type="file"),
            FieldKind.TRANSCRIPT_UPLOAD,
        )

    def test_serialized_evidence_omits_values_and_redacts_email_options(self) -> None:
        item = manifest_field(
            FieldKind.EMAIL,
            "Email",
            observed_value="candidate@example.test",
            options=("candidate@example.test", "Another option"),
        ).evidence(resolution="preserved")
        serialized = item.to_dict()
        self.assertNotIn("observed_value", serialized)
        self.assertNotIn("candidate@example.test", str(serialized))
        self.assertIn("[redacted-email]", serialized["options"])


if __name__ == "__main__":
    unittest.main()
