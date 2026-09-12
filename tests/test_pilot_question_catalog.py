import json
from pathlib import Path

from src.pilot.question_catalog import answer, match


def test_question_catalog_matches_sensitive_screening_without_answers():
    dismissed = match("Have you ever been dismissed from a previous employer?")
    assert dismissed["id"] == "ever_dismissed"
    assert dismissed["profile_path"] == "answers.ever_dismissed"
    assert dismissed["reuse"] == "reusable_until_changed"

    consent = match(
        "We need information for pre-employment vetting checks. Are you happy to continue?"
    )
    assert consent["id"] == "pre_employment_vetting_consent"
    assert consent["reuse"] == "application_specific"

    path = Path(__file__).parents[1] / "data" / "preset_questions.json"
    payload = json.loads(path.read_text())
    assert all("answer" not in item for item in payload["questions"])


def test_previous_employer_default_and_exact_override():
    question = "Have you previously worked for this organisation?"
    profile = {"answers": {"previously_worked_for_employer_default": "No"}}
    assert answer(profile, question, employer="Example LLP") == "No"
    profile["employer_answers"] = {
        "example llp": {"previously_worked_for_employer": "Yes"}
    }
    assert answer(profile, question, employer="Example LLP") == "Yes"


def test_application_specific_work_rights_and_consent_are_not_reused():
    profile = {"answers": {"authorized_to_work": "Yes"}}
    work_rights = (
        "Do you have the legal right to work for the duration of the programme "
        "without requiring a visa or sponsorship?"
    )
    assert answer(profile, work_rights, employer="Example Bank") == ""
