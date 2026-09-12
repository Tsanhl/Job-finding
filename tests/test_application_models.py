from __future__ import annotations

import unittest

from src.application_models import (
    AiPolicy,
    FillOutcome,
    OutcomeStatus,
    RunMode,
)


class ApplicationModelTests(unittest.TestCase):
    def test_legacy_modes_and_statuses_have_canonical_values(self) -> None:
        self.assertEqual(RunMode.parse("local-preview"), RunMode.LOCAL_PREVIEW)
        self.assertEqual(RunMode.parse("review"), RunMode.ASSISTED_REVIEW)
        self.assertEqual(AiPolicy.parse(None), AiPolicy.UNKNOWN)
        outcome = FillOutcome.build("needs_review", "prepared")
        self.assertEqual(outcome.status, OutcomeStatus.REVIEW_READY)
        self.assertEqual(outcome.to_dict()["legacy_status"], "needs_review")

    def test_submission_states_are_not_legacy_successes(self) -> None:
        disabled = FillOutcome(OutcomeStatus.SUBMISSION_DISABLED)
        uncertain = FillOutcome(OutcomeStatus.SUBMISSION_UNCONFIRMED)
        self.assertNotIn("legacy_status", disabled.to_dict())
        self.assertNotIn("legacy_status", uncertain.to_dict())
        self.assertNotEqual(uncertain.status, OutcomeStatus.SUBMITTED_CONFIRMED)


if __name__ == "__main__":
    unittest.main()
