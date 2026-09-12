"""Shared, fail-closed modes and outcomes for every application route."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class RunMode(_StringEnum):
    LOCAL_PREVIEW = "local_preview"
    ASSISTED_REVIEW = "assisted_review"
    GUARDED_AUTO_SUBMIT = "guarded_auto_submit"

    @classmethod
    def parse(cls, value: RunMode | str | None, *, dry_run: bool = False) -> RunMode:
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().lower().replace("-", "_")
        aliases = {
            "": cls.LOCAL_PREVIEW if dry_run else cls.ASSISTED_REVIEW,
            "preview": cls.LOCAL_PREVIEW,
            "dry_run": cls.LOCAL_PREVIEW,
            "review": cls.ASSISTED_REVIEW,
            "auto_submit": cls.GUARDED_AUTO_SUBMIT,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported application mode: {value!r}") from exc


class AiPolicy(_StringEnum):
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"

    @classmethod
    def parse(cls, value: AiPolicy | str | None) -> AiPolicy:
        if isinstance(value, cls):
            return value
        normalized = str(value or "unknown").strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported AI policy: {value!r}") from exc


class OutcomeStatus(_StringEnum):
    PAGE_FILLED = "page-filled"
    INCOMPLETE = "incomplete"
    SUBMITTED_USER_REPORTED = "submitted-user-reported"
    PREVIEW_READY = "preview-ready"
    REVIEW_READY = "review-ready"
    NEEDS_INFORMATION = "needs-information"
    NEEDS_AUTHENTICATION = "needs-authentication"
    POLICY_BLOCKED = "policy-blocked"
    UNSUPPORTED = "unsupported"
    SKIPPED_UNSUITABLE = "skipped-unsuitable"
    EMPLOYER_REJECTED = "employer-rejected"
    FAILED_RETRYABLE = "failed-retryable"
    SUBMISSION_DISABLED = "submission-disabled"
    SUBMISSION_UNCONFIRMED = "submission-unconfirmed"
    SUBMITTED_CONFIRMED = "submitted-confirmed"
    CANCELLED = "cancelled"

    @classmethod
    def parse(cls, value: OutcomeStatus | str) -> OutcomeStatus:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        legacy = {
            "dry_run": cls.PREVIEW_READY,
            "ready_for_manual_review": cls.REVIEW_READY,
            "needs_review": cls.REVIEW_READY,
            "needs_info": cls.NEEDS_INFORMATION,
            "needs_user_attention": cls.NEEDS_INFORMATION,
            "needs_signup": cls.NEEDS_AUTHENTICATION,
            "skipped": cls.SKIPPED_UNSUITABLE,
            "rejected": cls.EMPLOYER_REJECTED,
            "error": cls.FAILED_RETRYABLE,
            "failed": cls.FAILED_RETRYABLE,
            "applied": cls.SUBMISSION_UNCONFIRMED,
        }
        if normalized in legacy:
            return legacy[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported application outcome: {value!r}") from exc


_LEGACY_STATUSES: dict[OutcomeStatus, str] = {
    OutcomeStatus.PREVIEW_READY: "dry_run",
    OutcomeStatus.REVIEW_READY: "needs_review",
    OutcomeStatus.NEEDS_INFORMATION: "needs_info",
    OutcomeStatus.NEEDS_AUTHENTICATION: "needs_signup",
    OutcomeStatus.SKIPPED_UNSUITABLE: "skipped",
    OutcomeStatus.EMPLOYER_REJECTED: "rejected",
    OutcomeStatus.FAILED_RETRYABLE: "error",
    OutcomeStatus.SUBMITTED_CONFIRMED: "applied",
    OutcomeStatus.CANCELLED: "cancelled",
}


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    """Sanitized evidence: field values are deliberately never serialized."""

    field_id: str
    section: str
    question: str
    field_type: str
    classification: str
    required: bool
    options: tuple[str, ...] = ()
    max_characters: int | None = None
    max_words: int | None = None
    had_observed_value: bool = False
    resolution: str = "unresolved"
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["options"] = list(self.options)
        return data


@dataclass(frozen=True, slots=True)
class FillOutcome:
    status: OutcomeStatus
    detail: str = ""
    blockers: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    field_evidence: tuple[FieldEvidence, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", OutcomeStatus.parse(self.status))
        object.__setattr__(self, "blockers", tuple(dict.fromkeys(self.blockers)))
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "field_evidence", tuple(self.field_evidence))

    @property
    def legacy_status(self) -> str | None:
        return _LEGACY_STATUSES.get(self.status)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status.value,
            "detail": self.detail,
            "blockers": list(self.blockers),
            "actions": list(self.actions),
            "field_evidence": [item.to_dict() for item in self.field_evidence],
        }
        if self.legacy_status:
            data["legacy_status"] = self.legacy_status
        return data

    @classmethod
    def build(
        cls,
        status: OutcomeStatus | str,
        detail: str = "",
        *,
        blockers: Iterable[str] = (),
        actions: Iterable[str] = (),
        field_evidence: Iterable[FieldEvidence] = (),
    ) -> FillOutcome:
        return cls(
            status=OutcomeStatus.parse(status),
            detail=detail,
            blockers=tuple(blockers),
            actions=tuple(actions),
            field_evidence=tuple(field_evidence),
        )


def submission_disabled_outcome() -> FillOutcome:
    return FillOutcome(
        OutcomeStatus.SUBMISSION_DISABLED,
        "Automated final submission is disabled in this safety release; use assisted review.",
        blockers=("Final submission requires a later receipt-backed portal release.",),
    )
