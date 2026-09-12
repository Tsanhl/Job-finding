from scripts.privacy_gate import scan_blob


def test_privacy_gate_detects_known_values_without_echoing_them():
    findings = set()
    scan_blob(
        "index",
        "src/example.py",
        b"candidate_name = 'Private Candidate'",
        {b"Private Candidate"},
        findings,
    )
    assert findings == {
        ("index", "src/example.py", "known local private value")
    }

    path_findings = set()
    scan_blob(
        "working-tree",
        "notes/Private Candidate.txt",
        b"No sensitive content",
        {b"Private Candidate"},
        path_findings,
    )
    assert path_findings == {
        ("working-tree", "notes/Private Candidate.txt", "known local private value in path")
    }


def test_privacy_gate_allows_synthetic_fixture_email():
    findings = set()
    scan_blob(
        "working-tree",
        "tests/test_fixture.py",
        b"email = 'candidate@example.test'",
        set(),
        findings,
    )
    assert not findings
