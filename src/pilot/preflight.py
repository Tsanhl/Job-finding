"""Dependency-specific preparation; information gaps do not reject the batch."""

from dataclasses import dataclass, field

from .models import FunctionKind
from .onboarding import setup_status


@dataclass
class Preflight:
    global_blockers: list[str] = field(default_factory=list)
    per_application: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def check(plan, store, documents):
    report = Preflight()
    try:
        profile = store.load_profile(plan.profile_version)
    except ValueError:
        report.global_blockers.append("Selected profile version is unavailable")
        profile = {}
    if plan.request_schema == 2 and plan.function in {
        FunctionKind.AUTOFILL,
        FunctionKind.LINKEDIN_EASY_APPLY,
    }:
        setup = setup_status(store, documents, plan.profile_version)
        if not setup["ready"]:
            report.global_blockers.append(
                "Complete reusable profile setup: " + "; ".join(setup["missing"])
            )
    profile_row = store.one(
        "SELECT profile_id FROM profile_versions WHERE id=?", (plan.profile_version,)
    )
    for target in plan.targets:
        blockers = []
        if plan.will_submit(target) and profile.get(
            "_history_reconciliation_required"
        ):
            blockers.append(
                "Reconcile imported application history before submission"
            )
        if plan.will_submit(target) and target.eligibility != "eligible":
            blockers.append("Eligibility requires confirmation")
        for doc_id in plan.documents:
            try:
                documents.resolve(
                    doc_id, target, profile_row["profile_id"] if profile_row else None
                )
            except ValueError as e:
                blockers.append(str(e))
        if blockers:
            report.per_application[target.identity] = blockers
        if target.ai_policy != "allowed" and "external_ai" in plan.permissions:
            report.warnings.append(
                f"{target.employer}: generated answers disabled until employer policy is resolved"
            )
    return report
