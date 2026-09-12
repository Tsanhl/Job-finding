from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.pilot.ui import (
    _filter_discovered_jobs,
    _profile_value,
    _set_profile_value,
)


def test_dashboard_exposes_functions_and_does_not_launch_browser():
    def client(request, home=None):
        if request["op"] == "status":
            return {
                "applications": [],
                "questions": [],
                "runs": [],
                "active_workers": 0,
                "retained_contexts": 0,
            }
        if request["op"] == "setup_status":
            return {
                "ready": False,
                "profile_version": "",
                "missing": ["reusable profile", "approved CV"],
            }
        if request["op"] == "source_roots":
            return []
        if request["op"] in {"profiles", "documents"}:
            return []
        raise AssertionError("Unexpected operation: " + request["op"])

    with patch("src.pilot.ui.client", side_effect=client):
        app = AppTest.from_file(
            str(Path(__file__).parents[1] / "app.py"), default_timeout=15
        ).run()
    assert not app.exception
    assert any(t.value == "ApplyPilot" for t in app.title)
    assert [tab.label for tab in app.tabs] == [
        "Applications",
        "Autofill",
        "Find jobs",
        "LinkedIn Easy Apply",
        "Open job links",
        "Profile & documents",
        "Settings",
    ]
    assert not any("mode" in widget.label.casefold() for widget in app.selectbox)


def test_discovered_jobs_filter_and_requested_limit():
    rows = [
        {
            "role": "Graduate Software Engineer",
            "employer": "A",
            "location": "London, GB",
            "requirements": "Python",
        },
        {
            "role": "Graduate Solicitor",
            "employer": "B",
            "location": "London, GB",
            "requirements": "LLB",
        },
        {
            "role": "Software Engineer",
            "employer": "C",
            "location": "Manchester, GB",
            "requirements": "Python",
        },
    ]
    selected = _filter_discovered_jobs(
        rows, keywords="software", location="London", limit=1
    )
    assert selected == rows[:1]


def test_nested_private_profile_values():
    profile = {}
    _set_profile_value(profile, "answers.ever_dismissed", "No")
    assert _profile_value(profile, "answers.ever_dismissed") == "No"
