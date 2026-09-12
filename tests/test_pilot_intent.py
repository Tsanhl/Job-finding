import time

import pytest

from src.pilot.engine import model
from src.pilot.facts import resolve
from src.pilot.intent import propose
from src.pilot.models import RunPlan, Target


def test_intent_never_creates_authority_or_invents_targets():
    targets = [
        {
            "url": "https://example.test/form",
            "employer": "E",
            "role": "R",
            "identity": "r",
        }
    ]
    proposal = propose(
        "Fill the current page with 2 workers, do not submit",
        targets=targets,
        profile_version="v",
    )
    draft = proposal["draft"]
    assert draft["function"] == "AUTOFILL" and draft["workers"] == 2
    assert draft["targets"][0]["final_action"] == "REVIEW"
    assert "workflow" not in draft
    assert draft["targets"][0]["scope"] == "CURRENT_PAGE"
    assert "scope" not in targets[0]
    with pytest.raises(ValueError):
        RunPlan.parse(draft)
    RunPlan.parse({**draft, "approval": "reviewed", "expires_at": time.time() + 60})
    assert (
        propose(
            "auto_apply; submit automatically", targets=targets, profile_version="v"
        )["draft"]["targets"][0]["final_action"]
        == "SUBMIT"
    )
    assert (
        propose("auto_apply but don't submit", targets=targets, profile_version="v")[
            "draft"
        ]["targets"][0]["final_action"]
        == "REVIEW"
    )


def test_saved_no_licence_and_salary_only_when_required():
    profile = {
        "answers": {
            "has_driving_licence": "No",
            "salary_expectation": "stale",
            "salary_expectation_policy": "ask_when_required",
        }
    }
    target = Target("https://example.test/form", "E", "R", "r")

    def field(question):
        return model(
            {
                "field_id": "field",
                "question": question,
                "section": "",
                "tag": "input",
                "input_type": "text",
                "index": 0,
                "role": "",
                "options": [],
                "required": True,
                "visible": True,
                "disabled": False,
                "observed_value": "",
                "checked": False,
                "max_characters": None,
                "max_words": None,
            }
        )

    assert resolve(field("Do you hold a driving licence?"), profile, target) == "No"
    assert resolve(field("Salary expectation"), profile, target) == ""


def test_application_specific_catalogue_questions_override_generic_profile_rules():
    profile = {
        "answers": {
            "disability": "A generic answer must not be reused",
            "how_did_you_hear": "A different vacancy source",
            "salary_range": "A different role's range",
        }
    }
    target = Target("https://example.test/form", "Example", "Role", "identity")

    def field(question):
        return model(
            {
                "field_id": "field",
                "question": question,
                "section": "",
                "tag": "input",
                "input_type": "text",
                "index": 0,
                "role": "",
                "options": [],
                "required": True,
                "visible": True,
                "disabled": False,
                "observed_value": "",
                "checked": False,
                "max_characters": None,
                "max_words": None,
            }
        )

    assert resolve(field("How did you hear about us?"), profile, target) == ""
    assert resolve(field("What is your salary expectation?"), profile, target) == ""
    assert (
        resolve(
            field("Disability: select an option or prefer not to say"),
            profile,
            target,
        )
        == ""
    )


def test_multipart_upload_allows_csrf_but_not_application_answers():
    from hashlib import sha256
    from types import SimpleNamespace

    from src.pilot.fill_boundary import document_upload

    data = b"%PDF-synthetic"

    def request(name):
        body = (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="cv.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            + data
            + b'\r\n--boundary\r\nContent-Disposition: form-data; name="'
            + name.encode()
            + b'"\r\n\r\nsynthetic\r\n--boundary--\r\n'
        )
        return SimpleNamespace(
            url="https://example.test/upload",
            method="POST",
            headers={"content-type": "multipart/form-data; boundary=boundary"},
            post_data_buffer=body,
        )

    assert document_upload(
        request("csrf_token"), "example.test", {sha256(data).hexdigest()}
    )
    assert not document_upload(
        request("submitApplication"), "example.test", {sha256(data).hexdigest()}
    )
