"""Explicit task contracts; task routing cannot create permission."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskContract:
    kind: str
    permission: str
    postcondition: str


CONTRACTS = {
    "INSPECT_FORM": TaskContract(
        "INSPECT_FORM", "session", "Saved form checkpoint or explicit handoff"
    ),
    "WAIT_EMAIL": TaskContract(
        "WAIT_EMAIL",
        "mail",
        "Matched transaction consumed, uncertain, or scheduled for a bounded later check",
    ),
}


def validate_task(kind, plan):
    if kind not in CONTRACTS:
        raise ValueError("Unknown durable task type")
    if not plan.preview:
        plan.require(CONTRACTS[kind].permission)
    return CONTRACTS[kind]
