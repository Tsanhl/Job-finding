"""Qualification evidence is bound to the current implementation and real attempts."""

import hashlib
import json
from pathlib import Path


def source_digest():
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    files = sorted((root / "src/pilot").rglob("*.py")) + sorted(
        (root / "src/pilot/migrations").glob("*.sql")
    )
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def record(store, adapter, level, evidence_path):
    path = Path(evidence_path).expanduser().resolve(strict=True)
    evidence = json.loads(path.read_text())
    current = source_digest()
    if level == "SYNTHETIC":
        if (
            evidence.get("exit_code") != 0
            or evidence.get("source_digest") != current
            or adapter + ":submit" not in evidence.get("qualified_workflows", [])
        ):
            raise ValueError("Current successful local workflow test report required")
    else:
        prerequisite = "SYNTHETIC" if level == "SUPERVISED" else "SUPERVISED"
        previous = store.one(
            "SELECT * FROM adapter_qualifications WHERE adapter=? AND workflow=? AND level=?",
            (adapter, "submit", prerequisite),
        )
        if not valid(previous):
            raise ValueError("Previous qualification stage is missing or stale")
        if level == "SUPERVISED":
            receipt = store.one(
                "SELECT receipts.*,runs.plan FROM receipts JOIN submission_attempts ON submission_attempts.id=receipts.attempt_id JOIN runs ON runs.id=submission_attempts.run_id WHERE receipts.application_id=? AND receipts.reference=? AND receipts.source='portal'",
                (evidence.get("application_id"), evidence.get("receipt_reference")),
            )
            from .adapters import choose

            target = (
                store.one(
                    "SELECT ra.target FROM run_applications ra JOIN submission_attempts sa ON sa.run_id=ra.run_id AND sa.application_id=ra.application_id WHERE sa.id=?",
                    (receipt["attempt_id"],),
                )
                if receipt
                else None
            )
            if (
                not receipt
                or not target
                or choose(json.loads(target["target"])["url"]).name != adapter
                or not json.loads(receipt["plan"]).get("supervised_acceptance")
            ):
                raise ValueError(
                    "Matched supervised live attempt and portal receipt required"
                )
    payload = json.dumps(
        {
            "path": str(path),
            "source_digest": current,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    store.db.execute(
        "INSERT OR REPLACE INTO adapter_qualifications VALUES(?,?,?,?)",
        (adapter, "submit", level, payload),
    )


def valid(row):
    if not row:
        return False
    try:
        evidence = json.loads(row["evidence"])
        path = Path(evidence["path"])
        return (
            evidence["source_digest"] == source_digest()
            and evidence["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        )
    except (ValueError, KeyError, OSError):
        return False
