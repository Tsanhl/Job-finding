from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from law_firm_application_agent.workspace import create_application_workspace


class ApplicationWorkspaceTests(unittest.TestCase):
    def test_creates_expected_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = create_application_workspace(
                "A&O Shearman",
                "Training Contract 2027",
                office="London",
                root=temporary_root,
                candidate_config=None,
            )

            self.assertEqual(
                workspace.name,
                "a-o-shearman--training-contract-2027--london",
            )
            self.assertTrue((workspace / "application.md").is_file())
            self.assertTrue((workspace / "research" / "sources.md").is_file())
            self.assertTrue((workspace / "evidence" / "fact_ledger.md").is_file())
            self.assertTrue((workspace / "review" / "truth_audit.md").is_file())
            self.assertTrue((workspace / "final" / "README.md").is_file())
            self.assertTrue((workspace / "cv" / "requirements.md").is_file())
            self.assertTrue((workspace / "cv" / "tailoring_map.md").is_file())
            self.assertTrue((workspace / "cv" / "review" / "checklist.md").is_file())
            self.assertTrue((workspace / "cv" / "final" / "README.md").is_file())

    def test_reuses_workspace_without_overwriting_user_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = create_application_workspace(
                "Example LLP",
                "Vacation Scheme",
                root=temporary_root,
                candidate_config=None,
            )
            application_file = workspace / "application.md"
            application_file.write_text("My existing work\n", encoding="utf-8")

            same_workspace = create_application_workspace(
                "Example LLP",
                "Vacation Scheme",
                root=temporary_root,
                candidate_config=None,
            )

            self.assertEqual(workspace, same_workspace)
            self.assertEqual(
                application_file.read_text(encoding="utf-8"),
                "My existing work\n",
            )

    def test_requires_firm_and_programme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            with self.assertRaises(ValueError):
                create_application_workspace(
                    "", "Training Contract", root=temporary_root, candidate_config=None
                )
            with self.assertRaises(ValueError):
                create_application_workspace(
                    "Example LLP", "", root=temporary_root, candidate_config=None
                )

    def test_records_canonical_cv_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            cv_path = root / "master-cv.pdf"
            cv_path.write_bytes(b"test cv")
            config_path = root / "candidate.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "canonical_cv": {
                            "path": str(cv_path),
                            "sha256": "example-hash",
                            "registered_on": "2026-08-25",
                            "status": "approved baseline",
                        }
                    }
                ),
                encoding="utf-8",
            )

            workspace = create_application_workspace(
                "Example LLP",
                "Training Contract",
                root=root / "applications",
                candidate_config=config_path,
            )

            source_reference = (workspace / "documents" / "cv_source.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(str(cv_path), source_reference)
            self.assertIn("example-hash", source_reference)
            self.assertFalse((workspace / "documents" / cv_path.name).exists())


if __name__ == "__main__":
    unittest.main()
