"""Typed plans and authority, independent of browser/model configuration."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit


class Workflow(StrEnum):
    """Legacy/internal policy boundary accepted by the compatibility parser."""

    FILL_ONLY = "FILL_ONLY"
    AUTO_APPLY = "AUTO_APPLY"


class FunctionKind(StrEnum):
    FIND_JOBS = "FIND_JOBS"
    LINKEDIN_EASY_APPLY = "LINKEDIN_EASY_APPLY"
    AUTOFILL = "AUTOFILL"
    OPEN_JOB_LINKS = "OPEN_JOB_LINKS"


class FinalAction(StrEnum):
    REVIEW = "REVIEW"
    SUBMIT = "SUBMIT"


class Scope(StrEnum):
    CURRENT_PAGE = "CURRENT_PAGE"
    EXISTING_APPLICATION = "EXISTING_APPLICATION"


class State(StrEnum):
    QUEUED = "QUEUED"
    ELIGIBILITY_UNVERIFIED = "ELIGIBILITY_UNVERIFIED"
    ELIGIBLE = "ELIGIBLE"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    NEEDS_AUTHENTICATION = "NEEDS_AUTHENTICATION"
    EMAIL_PENDING = "EMAIL_PENDING"
    FILLING = "FILLING"
    PAGE_FILLED = "PAGE_FILLED"
    INCOMPLETE = "INCOMPLETE"
    REVIEW_READY = "REVIEW_READY"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNCONFIRMED = "SUBMISSION_UNCONFIRMED"
    SUBMITTED_CONFIRMED = "SUBMITTED_CONFIRMED"
    SUBMITTED_USER_REPORTED = "SUBMITTED_USER_REPORTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"
    CANCELLED = "CANCELLED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"


DONE = {State.SUBMITTED_CONFIRMED, State.SUBMITTED_USER_REPORTED}
FORBIDDEN_FILL = {
    "discover",
    "credentials",
    "register",
    "mail",
    "activate",
    "otp",
    "submit",
}
CAPABILITIES = {
    "fill",
    "upload",
    "research",
    "external_ai",
    "session",
    "discover",
    "credentials",
    "register",
    "mail",
    "activate",
    "otp",
    "submit",
}


@dataclass(frozen=True)
class Target:
    url: str
    employer: str
    role: str
    identity: str  # confirmed requisition/cycle or reviewed fallback
    scope: Scope = Scope.EXISTING_APPLICATION
    tab_id: str = ""
    account: str = ""
    country: str = ""
    ai_policy: str = "unknown"
    eligibility: str = "unknown"
    adapter: str = ""
    portal_application_id: str = ""
    final_action: FinalAction = FinalAction.REVIEW


@dataclass(frozen=True)
class RunPlan:
    workflow: Workflow
    targets: tuple[Target, ...]
    profile_version: str
    documents: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset({"fill", "session"})
    workers: int = 1
    submission_policy: str = "review"
    expires_at: float = 0
    approval: str = ""
    preview: bool = False
    research_sources: tuple[str, ...] = ()
    discovery_budget: int = 0
    supervised_acceptance: bool = False
    function: FunctionKind = FunctionKind.AUTOFILL
    result_limit: int = 0
    request_schema: int = 1

    @classmethod
    def parse(cls, raw: dict) -> "RunPlan":
        if not isinstance(raw, dict):
            raise ValueError("Plan must be an object")
        for flag in ("preview", "supervised_acceptance"):
            if flag in raw and type(raw[flag]) is not bool:
                raise ValueError(f"{flag} must be a boolean")
        if type(raw.get("discovery_budget", 0)) is not int:
            raise ValueError("Discovery budget must be an integer")
        request_schema = raw.get(
            "request_schema", 2 if raw.get("function") is not None else 1
        )
        if type(request_schema) is not int or request_schema not in {1, 2}:
            raise ValueError("Unsupported request schema")
        try:
            function = FunctionKind(raw.get("function", "AUTOFILL"))
        except ValueError as exc:
            raise ValueError("Unknown ApplyPilot function") from exc
        if type(raw.get("result_limit", 0)) is not int:
            raise ValueError("result_limit must be an integer")
        workers = raw.get("workers", max(1, len(raw.get("targets", []))))
        if type(workers) is not int or not 1 <= workers <= 10:
            raise ValueError("workers must be an integer from 1 to 10")
        try:
            targets = []
            for item in raw.get("targets", []):
                target = dict(item)
                target["scope"] = Scope(target.get("scope", "EXISTING_APPLICATION"))
                default_action = (
                    "SUBMIT"
                    if request_schema == 1
                    and raw.get("submission_policy") == "submit"
                    else "REVIEW"
                )
                target["final_action"] = FinalAction(
                    target.get("final_action", default_action)
                )
                targets.append(Target(**target))
            targets = tuple(targets)
        except (TypeError, KeyError) as exc:
            raise ValueError(
                "Each target needs URL, employer, role and identity"
            ) from exc
        except ValueError as exc:
            raise ValueError("Invalid target scope or final action") from exc
        if type(workers) is not int or not 1 <= workers <= 10:
            raise ValueError("workers must be an integer from 1 to 10")
        discovery_only = (
            not targets
            and (
                function == FunctionKind.FIND_JOBS
                or raw.get("workflow") == "AUTO_APPLY"
            )
            and raw.get("discovery_budget", 0) > 0
            and raw.get("research_sources")
        )
        if not discovery_only and not 1 <= len(targets) <= 10:
            raise ValueError("Select 1–10 targets; explicitly split larger batches")
        if len({t.identity for t in targets}) != len(targets):
            raise ValueError("Duplicate target identity")
        for t in targets:
            u = urlsplit(t.url)
            if (
                u.scheme not in {"https", "http"}
                or not u.hostname
                or u.username
                or u.password
            ):
                raise ValueError("Invalid target URL")
            if not all((t.identity.strip(), t.employer.strip(), t.role.strip())):
                raise ValueError(
                    "Each target needs employer, role and reviewed identity"
                )
            if t.ai_policy not in {"unknown", "allowed", "prohibited"}:
                raise ValueError("Invalid AI policy")
            if t.eligibility not in {"unknown", "eligible", "ineligible"}:
                raise ValueError("Invalid eligibility")
        any_submit = any(t.final_action == FinalAction.SUBMIT for t in targets)
        workflow = (
            Workflow.AUTO_APPLY
            if request_schema == 2
            and (function == FunctionKind.FIND_JOBS or any_submit)
            else Workflow(raw.get("workflow", "FILL_ONLY"))
        )
        submission_policy = (
            "submit"
            if request_schema == 2 and any_submit
            else str(raw.get("submission_policy", "review"))
        )
        p = cls(
            workflow,
            targets,
            str(raw.get("profile_version", "")),
            tuple(raw.get("documents", [])),
            frozenset(raw.get("permissions", ["fill", "session"])),
            workers,
            submission_policy,
            float(raw.get("expires_at", 0)),
            str(raw.get("approval", "")),
            bool(raw.get("preview", False)),
            tuple(raw.get("research_sources", [])),
            int(raw.get("discovery_budget", 0)),
            raw.get("supervised_acceptance") is True,
            function,
            int(raw.get("result_limit", 0)),
            request_schema,
        )
        if not p.profile_version and p.function not in {
            FunctionKind.FIND_JOBS,
            FunctionKind.OPEN_JOB_LINKS,
        }:
            raise ValueError("Select a profile version")
        if not p.permissions <= CAPABILITIES:
            raise ValueError("Unknown permission")
        if (
            not p.approval
            or not math.isfinite(p.expires_at)
            or p.expires_at <= time.time()
        ):
            raise ValueError("Current explicit approval and expiry required")
        if p.submission_policy not in {"review", "submit"}:
            raise ValueError("Invalid submission policy")
        if p.workflow == Workflow.FILL_ONLY and (
            p.permissions & FORBIDDEN_FILL
            or p.submission_policy != "review"
            or p.discovery_budget
        ):
            raise ValueError("FILL_ONLY cannot acquire AUTO capabilities")
        if p.submission_policy == "submit" and "submit" not in p.permissions:
            raise ValueError("Submission authority required")
        if p.request_schema == 2:
            if p.function == FunctionKind.LINKEDIN_EASY_APPLY and any_submit:
                raise ValueError("LinkedIn Easy Apply stops at final review")
            if any_submit and "submit" not in p.permissions:
                raise ValueError("Each SUBMIT target requires submission authority")
            if "submit" in p.permissions and not any_submit:
                raise ValueError("Submission permission requires a SUBMIT target")
            if p.function not in {
                FunctionKind.AUTOFILL,
                FunctionKind.LINKEDIN_EASY_APPLY,
                FunctionKind.FIND_JOBS,
            }:
                raise ValueError("This function does not create application runs")
        if not 0 <= p.discovery_budget <= 100:
            raise ValueError("Discovery budget must be 0–100")
        if not 0 <= p.result_limit <= 10:
            raise ValueError("result_limit must be 0–10")
        if p.function == FunctionKind.LINKEDIN_EASY_APPLY and not 1 <= p.result_limit <= 10:
            raise ValueError("LinkedIn Easy Apply requires a result limit from 1 to 10")
        return p

    def json(self):
        d = asdict(self)
        d["permissions"] = sorted(self.permissions)
        return d

    def require(self, capability: str):
        if self.preview:
            raise PermissionError("Preview cannot mutate or disclose data")
        if time.time() >= self.expires_at:
            raise PermissionError("Approval expired")
        if capability not in self.permissions:
            raise PermissionError(f"{capability} permission required")
        if self.workflow == Workflow.FILL_ONLY and capability in FORBIDDEN_FILL:
            raise PermissionError("Prohibited in FILL_ONLY")

    def will_submit(self, target: Target) -> bool:
        """Return target-scoped authority without trusting the legacy batch flag."""

        if self.request_schema == 2:
            return target.final_action == FinalAction.SUBMIT
        return (
            self.workflow == Workflow.AUTO_APPLY
            and self.submission_policy == "submit"
        )


@dataclass
class Result:
    state: State
    blockers: list[dict] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    detail: str = ""
