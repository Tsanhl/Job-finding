# ApplyPilot local test report

Recorded: 2026-09-12T16:37:20.938795+00:00
Python: 3.14.5; SQLite: 3.53.4

Command: `.venv-upgrade/bin/python -m pytest -q -s --junitxml=output/upgrade-validation/results.xml`

Exit code: 0
Pytest summary: 232 passed, 6 subtests passed in 70.51s (0:01:10)
JUnit counts: {'tests': 238, 'failures': 0, 'errors': 0, 'skipped': 0}

## Measured browser concurrency

- Requested 1: measured 1 overlapping browser uploads; maximum 1 active workers.
- Requested 2: measured 2 overlapping browser uploads; maximum 2 active workers.
- Requested 5: measured 5 overlapping browser uploads; maximum 5 active workers.
- Requested 10: measured 10 overlapping browser uploads; maximum 10 active workers.

Tests use disposable SQLite databases, synthetic profiles, loopback HTTP fixtures or intercepted browser requests. OAuth uses simulated provider responses and an in-memory secret backend. No native Keychain, real mailbox, employer registration or live submission was exercised.

The original baseline was 86 passed plus 6 subtests. Added coverage includes the four function requests, first-run readiness, the versioned blank question catalogue, required-versus-optional setup, application-specific salary and work-rights handling, target-scoped final actions, four-source discovery persistence, bounded listing-to-detail crawling, area filtering, CHECK PORTAL formatting, exact link counts, external-draft redaction and a multi-step LinkedIn review-ready flow that never submits. No tests were deselected.

Detailed local logs: `output/upgrade-validation/pytest.log`; JUnit XML: `output/upgrade-validation/results.xml`.
