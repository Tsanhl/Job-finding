from __future__ import annotations

import unittest
from unittest.mock import patch

from src.application_models import AiPolicy, OutcomeStatus, RunMode
from src.ats_adapters import GENERIC_ADAPTER
from src.external_apply import _click_progress, fill_generic_application_form
from src.field_manifest import FieldKind, ManifestField
from src.submission_guard import VerificationReport
from src.work_experience import ExperienceFillReport


class _Control:
    def __init__(self, *, text: str = "", control_type: str = "button") -> None:
        self.text = text
        self.control_type = control_type
        self.fills: list[str] = []
        self.uploads: list[str] = []
        self.clicks = 0
        self.selections: list[str] = []

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def is_visible(self, timeout: int = 0) -> bool:
        return True

    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, name: str):
        if name == "type":
            return self.control_type
        if name == "value":
            return ""
        return None

    def click(self, timeout: int = 0) -> None:
        self.clicks += 1

    def fill(self, value: str) -> None:
        self.fills.append(value)

    def set_input_files(self, value: str) -> None:
        self.uploads.append(value)

    def select_option(self, *, label: str) -> None:
        self.selections.append(label)

    def check(self) -> None:
        self.clicks += 1


class _Empty(_Control):
    def count(self) -> int:
        return 0

    def is_visible(self, timeout: int = 0) -> bool:
        return False


class _Controls:
    def __init__(self, controls: list[_Control]) -> None:
        self.controls = controls

    def nth(self, index: int) -> _Control:
        return self.controls[index]


class _Root(_Empty):
    def __init__(self, controls: list[_Control]) -> None:
        super().__init__()
        self.controls = controls

    def locator(self, selector: str) -> _Controls:
        return _Controls(self.controls)


class _Body(_Empty):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class _Page:
    def __init__(self, controls: list[_Control], *, body: str = "") -> None:
        self.url = "https://jobs.example.test/apply"
        self.controls = controls
        self.body = body

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    def locator(self, selector: str):
        if selector == "body":
            return _Body(self.body)
        if selector == GENERIC_ADAPTER.root_selector:
            return _Root(self.controls)
        return _Empty()


def field(index: int, kind: FieldKind, question: str, **overrides) -> ManifestField:
    values = {
        "index": index,
        "field_id": f"field-{index}",
        "section": "Application",
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


class ExternalSafetyTests(unittest.TestCase):
    def test_generic_progress_never_considers_apply_or_submit(self) -> None:
        page = _Page([])
        with patch.object(page, "locator", wraps=page.locator) as locator:
            self.assertIsNone(_click_progress(page, adapter=GENERIC_ADAPTER))
        selectors = " ".join(call.args[0] for call in locator.call_args_list)
        self.assertNotIn("Apply Now", selectors)
        self.assertNotIn("Submit application", selectors)
        self.assertNotIn("Continue", selectors)

    def test_local_preview_performs_no_field_mutations(self) -> None:
        control = _Control()
        manifest = (field(0, FieldKind.FIRST_NAME, "First name"),)
        with patch("src.external_apply.extract_field_manifest", return_value=manifest):
            outcome = fill_generic_application_form(
                _Page([control]),
                profile={"first_name": "Alice"},
                defaults={},
                cv_path="/tmp/cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=False,
                dry_run=True,
                log=None,
                mode=RunMode.LOCAL_PREVIEW,
            )
        self.assertEqual(outcome.status, OutcomeStatus.PREVIEW_READY)
        self.assertEqual(control.fills, [])
        self.assertEqual(control.uploads, [])
        self.assertEqual(control.clicks, 0)

    def test_prohibited_ai_generates_and_uploads_no_affected_content(self) -> None:
        narrative = _Control()
        upload = _Control()
        manifest = (
            field(0, FieldKind.COVER_LETTER, "Cover letter", tag="textarea"),
            field(
                1,
                FieldKind.COVER_LETTER_UPLOAD,
                "Cover letter upload",
                input_type="file",
            ),
        )
        with (
            patch("src.external_apply.extract_field_manifest", return_value=manifest),
            patch("src.external_apply.generate_cover_letter") as generate,
            patch(
                "src.external_apply.fill_repeatable_work_experience",
                return_value=ExperienceFillReport(),
            ),
            patch(
                "src.external_apply.verify_application_ready",
                return_value=VerificationReport(),
            ),
        ):
            outcome = fill_generic_application_form(
                _Page([narrative, upload], body="Use of generative AI is prohibited."),
                profile={},
                defaults={},
                cv_path="/tmp/cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=True,
                dry_run=False,
                log=None,
                mode=RunMode.ASSISTED_REVIEW,
                ai_policy=AiPolicy.ALLOWED,
            )
        self.assertEqual(outcome.status, OutcomeStatus.POLICY_BLOCKED)
        generate.assert_not_called()
        self.assertEqual(narrative.fills, [])
        self.assertEqual(upload.uploads, [])

    def test_unknown_ai_policy_keeps_narrative_generation_off(self) -> None:
        narrative = _Control()
        manifest = (
            field(0, FieldKind.MOTIVATION, "Why this role?", tag="textarea"),
        )
        with (
            patch("src.external_apply.extract_field_manifest", return_value=manifest),
            patch("src.external_apply.answer_question") as answer,
            patch(
                "src.external_apply.fill_repeatable_work_experience",
                return_value=ExperienceFillReport(),
            ),
            patch(
                "src.external_apply.verify_application_ready",
                return_value=VerificationReport(),
            ),
        ):
            outcome = fill_generic_application_form(
                _Page([narrative]),
                profile={},
                defaults={},
                cv_path="/tmp/cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=True,
                dry_run=False,
                log=None,
                mode=RunMode.ASSISTED_REVIEW,
                ai_policy=AiPolicy.UNKNOWN,
            )
        answer.assert_not_called()
        self.assertEqual(narrative.fills, [])
        self.assertEqual(outcome.status, OutcomeStatus.NEEDS_INFORMATION)

    def test_only_approved_cv_slot_is_uploaded(self) -> None:
        cv_control = _Control()
        cover_control = _Control()
        manifest = (
            field(0, FieldKind.CV_UPLOAD, "CV", input_type="file"),
            field(
                1,
                FieldKind.COVER_LETTER_UPLOAD,
                "Cover letter",
                input_type="file",
            ),
        )
        with (
            patch("src.external_apply.extract_field_manifest", return_value=manifest),
            patch(
                "src.external_apply.fill_repeatable_work_experience",
                return_value=ExperienceFillReport(),
            ),
            patch(
                "src.external_apply.verify_application_ready",
                return_value=VerificationReport(),
            ),
        ):
            outcome = fill_generic_application_form(
                _Page([cv_control, cover_control]),
                profile={},
                defaults={},
                cv_path="/tmp/approved-cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=False,
                dry_run=False,
                log=None,
                mode=RunMode.ASSISTED_REVIEW,
            )
        self.assertEqual(cv_control.uploads, ["/tmp/approved-cv.pdf"])
        self.assertEqual(cover_control.uploads, [])
        self.assertEqual(outcome.status, OutcomeStatus.NEEDS_INFORMATION)

    def test_over_limit_profile_value_is_not_silently_truncated(self) -> None:
        name_control = _Control()
        manifest = (
            field(
                0,
                FieldKind.FIRST_NAME,
                "First name (maximum 3 characters)",
                max_characters=3,
            ),
        )
        with (
            patch("src.external_apply.extract_field_manifest", return_value=manifest),
            patch(
                "src.external_apply.fill_repeatable_work_experience",
                return_value=ExperienceFillReport(),
            ),
            patch(
                "src.external_apply.verify_application_ready",
                return_value=VerificationReport(),
            ),
        ):
            outcome = fill_generic_application_form(
                _Page([name_control]),
                profile={"first_name": "Alice"},
                defaults={},
                cv_path="/tmp/cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=False,
                dry_run=False,
                log=None,
                mode=RunMode.ASSISTED_REVIEW,
            )
        self.assertEqual(name_control.fills, [])
        self.assertEqual(outcome.status, OutcomeStatus.NEEDS_INFORMATION)
        self.assertIn("limit is 3", " ".join(outcome.blockers))

    def test_native_select_uses_only_an_exact_confirmed_option(self) -> None:
        select = _Control()
        manifest = (
            field(
                0,
                FieldKind.SPONSORSHIP,
                "Will you require sponsorship?",
                tag="select",
                input_type="select-one",
                options=("Select", "Yes", "No"),
            ),
        )
        with (
            patch("src.external_apply.extract_field_manifest", return_value=manifest),
            patch(
                "src.external_apply.fill_repeatable_work_experience",
                return_value=ExperienceFillReport(),
            ),
            patch(
                "src.external_apply.verify_application_ready",
                return_value=VerificationReport(),
            ),
        ):
            outcome = fill_generic_application_form(
                _Page([select]),
                profile={"answers": {"require_sponsorship": "No"}},
                defaults={},
                cv_path="/tmp/cv.pdf",
                job_context="",
                company="Example",
                role="Role",
                use_ai=False,
                dry_run=False,
                log=None,
                mode=RunMode.ASSISTED_REVIEW,
            )
        self.assertEqual(select.selections, ["No"])
        # No final-review evidence exists on this synthetic page.
        self.assertEqual(outcome.status, OutcomeStatus.INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
