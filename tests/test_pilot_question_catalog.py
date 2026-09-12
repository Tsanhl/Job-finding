import json
from pathlib import Path

from src.pilot.question_catalog import answer, load, match


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
    assert payload["version"] == 2
    assert len(payload["questions"]) >= 60
    assert all("answer" not in item for item in payload["questions"])
    assert all("default_answer" not in item for item in payload["questions"])


def test_catalogue_has_unique_valid_blank_definitions_across_common_categories():
    questions = load()["questions"]
    ids = [item["id"] for item in questions]
    assert len(ids) == len(set(ids))
    assert {
        "Employer relationship",
        "Work rights and eligibility",
        "Availability and mobility",
        "Education and qualifications",
        "Integrity and regulatory screening",
        "Financial integrity",
        "Checks and references",
        "Compensation",
        "Voluntary demographic disclosure",
        "Social mobility",
        "Declarations and consent",
    }.issubset({item["category"] for item in questions})
    assert all(isinstance(item["setup_required"], bool) for item in questions)
    assert all(item["match_terms"] for item in questions)


def test_previous_employer_default_and_exact_override():
    question = "Have you previously worked for this organisation?"
    profile = {"answers": {"previously_worked_for_employer_default": "No"}}
    assert answer(profile, question, employer="Example LLP") == "No"
    profile["employer_answers"] = {
        "example llp": {"previously_worked_for_employer": "Yes"}
    }
    assert answer(profile, question, employer="Example LLP") == "Yes"


def test_application_specific_work_rights_and_consent_are_not_reused():
    profile = {
        "answers": {
            "authorized_to_work": "Yes",
            "salary_expectation": "A tracked catalogue must not read this",
        }
    }
    work_rights = (
        "Do you have the legal right to work for the duration of the programme "
        "without requiring a visa or sponsorship?"
    )
    assert answer(profile, work_rights, employer="Example Bank") == ""
    assert answer(profile, "What is your salary expectation?") == ""
    assert match("What is your salary expectation?")["ask_policy"] == "required_only"


def test_country_specific_definition_does_not_serialize_the_profile():
    profile = {"answers": {"authorized_to_work": "Yes"}}
    question = "Do you have the legal right to work in the country of employment?"
    assert answer(profile, question) == ""


def test_most_specific_match_wins_when_catalogue_terms_overlap():
    item = match(
        "Do you have the legal right to work for the duration of the programme "
        "without requiring a visa or sponsorship?"
    )
    assert item["id"] == "work_rights_entire_programme_without_visa_or_sponsorship"
