"""Local multi-worker coordinator for prepared direct applications."""

from __future__ import annotations

import json
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .application_models import AiPolicy, OutcomeStatus, RunMode
from .application_flow import missing_academic_details, missing_application_details
from .browser_session import CDP_URL
from .external_apply import fill_generic_application_form

MAX_APPLICATION_WORKERS = 10
MAX_APPLICATIONS_PER_BATCH = 10
REVIEW_READY_STATUS = OutcomeStatus.REVIEW_READY.value


def _portfolio_completion_fields(
    rows: list[dict[str, Any]],
    *,
    running: bool,
    expected_status: str = REVIEW_READY_STATUS,
) -> dict[str, Any]:
    """Describe portfolio completion without treating partial work as finished."""
    incomplete_ids = [
        str(row["task_id"])
        for row in rows
        if row.get("status") != expected_status
    ]
    if running:
        portfolio_status = "running"
    elif incomplete_ids:
        portfolio_status = "incomplete"
    else:
        portfolio_status = expected_status
    return {
        "portfolio_status": portfolio_status,
        "completion_definition": f"all_applications_{expected_status}",
        "review_ready_count": sum(
            1 for row in rows if row.get("status") == OutcomeStatus.REVIEW_READY.value
        ),
        "preview_ready_count": sum(
            1 for row in rows if row.get("status") == OutcomeStatus.PREVIEW_READY.value
        ),
        "incomplete_application_ids": incomplete_ids,
    }


def resolve_worker_count(application_count: int, requested_workers: int | None) -> int:
    """Choose one worker per application unless the caller sets a lower limit."""
    if application_count < 1:
        raise ValueError("Application queue is empty.")
    if application_count > MAX_APPLICATIONS_PER_BATCH:
        raise ValueError(
            f"A batch can contain at most {MAX_APPLICATIONS_PER_BATCH} applications."
        )
    if requested_workers is None:
        requested_workers = application_count
    if type(requested_workers) is not int or not 1 <= requested_workers <= MAX_APPLICATION_WORKERS:
        raise ValueError("workers must be an integer from 1 to 10")
    return min(requested_workers, application_count)


@dataclass(frozen=True)
class ApplicationTask:
    task_id: str
    application_type: str
    company: str
    role: str
    url: str
    office: str = ""
    deadline: str = ""
    job_description: str = ""
    portal_questions: str = ""
    cv_path: str = ""
    allow_ai: bool = False
    ai_policy: AiPolicy = AiPolicy.UNKNOWN
    intake_confirmed: bool = False
    ai_policy_confirmed: bool = False
    manual_submit_confirmed: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any], index: int) -> "ApplicationTask":
        if not isinstance(value, dict):
            raise ValueError(f"Application #{index + 1} must be a JSON object.")
        if "ai_policy" in value:
            ai_policy = AiPolicy.parse(value.get("ai_policy"))
        elif value.get("allow_ai") is True and value.get("ai_policy_confirmed") is True:
            ai_policy = AiPolicy.ALLOWED
        elif value.get("ai_policy_confirmed") is True:
            ai_policy = AiPolicy.PROHIBITED
        else:
            ai_policy = AiPolicy.UNKNOWN
        return cls(
            task_id=str(value.get("id") or value.get("task_id") or "").strip(),
            application_type=str(value.get("application_type") or "other").strip().lower(),
            company=str(value.get("company") or "").strip(),
            role=str(value.get("role") or value.get("programme") or "").strip(),
            url=str(value.get("url") or "").strip(),
            office=str(value.get("office") or "").strip(),
            deadline=str(value.get("deadline") or "").strip(),
            job_description=str(value.get("job_description") or "").strip(),
            portal_questions=str(value.get("portal_questions") or "").strip(),
            cv_path=str(value.get("cv_path") or "").strip(),
            allow_ai=value.get("allow_ai") is True,
            ai_policy=ai_policy,
            intake_confirmed=value.get("intake_confirmed") is True,
            ai_policy_confirmed=value.get("ai_policy_confirmed") is True,
            manual_submit_confirmed=value.get("manual_submit_confirmed") is True,
        )


@dataclass(frozen=True)
class ApplicationWorkerResult:
    task_id: str
    company: str
    role: str
    status: str
    detail: str
    logs: tuple[str, ...] = ()
    legacy_status: str | None = None
    blockers: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    field_evidence: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        from .application_models import FillOutcome

        canonical = OutcomeStatus.parse(self.status)
        object.__setattr__(self, "status", canonical.value)
        if self.legacy_status is None:
            object.__setattr__(self, "legacy_status", FillOutcome(canonical).legacy_status)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["logs"] = list(self.logs)
        data["blockers"] = list(self.blockers)
        data["actions"] = list(self.actions)
        data["field_evidence"] = list(self.field_evidence)
        if self.legacy_status is None:
            data.pop("legacy_status")
        return data


class BatchValidationError(ValueError):
    def __init__(self, blockers: dict[str, list[str]]) -> None:
        super().__init__("Application batch has unresolved intake questions.")
        self.blockers = blockers


class ApplicationBatchRun:
    """Run a validated application queue in the background with live status.

    At most ``worker_count`` tasks are submitted at once. This keeps the rest of
    the queue cancellable instead of filling the executor with work that has not
    begun. Running browser workers finish normally when cancellation is requested;
    queued applications are never opened.
    """

    def __init__(
        self,
        tasks: list[ApplicationTask],
        *,
        profile: dict[str, Any],
        defaults: dict[str, Any],
        default_cv_path: str,
        worker_count: int | None = None,
        use_ai: bool = False,
        worker_fn: Any | None = None,
        mode: RunMode | str | None = None,
    ) -> None:
        self.mode = RunMode.parse(mode)
        blockers = validate_application_batch(
            tasks,
            profile=profile,
            default_cv_path=default_cv_path,
            mode=self.mode,
        )
        if blockers:
            raise BatchValidationError(blockers)
        if not tasks:
            raise ValueError("Application queue is empty.")

        self.tasks = tuple(tasks)
        self.profile = deepcopy(profile)
        self.defaults = deepcopy(defaults)
        self.default_cv_path = default_cv_path
        self.worker_count = resolve_worker_count(len(tasks), worker_count)
        self.use_ai = bool(use_ai)
        self._worker_fn = worker_fn
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._statuses = {task.task_id: "queued" for task in self.tasks}
        self._results: dict[str, ApplicationWorkerResult] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("This application batch has already been started.")
            self._running = True
            self._thread = threading.Thread(
                target=self._coordinate,
                daemon=True,
                name="application-batch-coordinator",
            )
            self._thread.start()

    def cancel(self) -> None:
        """Prevent queued tasks from starting; do not interrupt active browser tabs."""
        self._cancel.set()
        with self._lock:
            for task in self.tasks:
                if self._statuses[task.task_id] == "queued":
                    self._statuses[task.task_id] = "cancelled"
                    self._results[task.task_id] = ApplicationWorkerResult(
                        task_id=task.task_id,
                        company=task.company,
                        role=task.role,
                        status="cancelled",
                        detail="Not started because cancellation was requested.",
                    )

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for task in self.tasks:
                result = self._results.get(task.task_id)
                if result:
                    row = result.to_dict()
                else:
                    row = {
                        "task_id": task.task_id,
                        "company": task.company,
                        "role": task.role,
                        "status": self._statuses[task.task_id],
                        "detail": "",
                        "logs": [],
                    }
                rows.append(row)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            summary = {
                "worker_count": self.worker_count,
                "application_count": len(self.tasks),
                "final_submission": (
                    "no_portal_changes"
                    if self.mode == RunMode.LOCAL_PREVIEW
                    else "manual_only"
                ),
                "running": self._running,
                "cancel_requested": self._cancel.is_set(),
                "counts": counts,
                "results": rows,
            }
            summary.update(
                _portfolio_completion_fields(
                    rows,
                    running=self._running,
                    expected_status=(
                        OutcomeStatus.PREVIEW_READY.value
                        if self.mode == RunMode.LOCAL_PREVIEW
                        else OutcomeStatus.REVIEW_READY.value
                    ),
                )
            )
            return summary

    def _coordinate(self) -> None:
        pending_tasks = iter(self.tasks)
        futures: dict[Future[ApplicationWorkerResult], ApplicationTask] = {}

        def submit_one(pool: ThreadPoolExecutor) -> bool:
            if self._cancel.is_set():
                return False
            try:
                task = next(pending_tasks)
            except StopIteration:
                return False
            with self._lock:
                if self._statuses[task.task_id] != "queued":
                    return False
                self._statuses[task.task_id] = "running"
            worker = self._worker_fn or _run_application_worker
            future = pool.submit(
                worker,
                task,
                profile=self.profile,
                defaults=self.defaults,
                default_cv_path=self.default_cv_path,
                use_ai=self.use_ai and task.allow_ai,
                mode=self.mode,
            )
            futures[future] = task
            return True

        try:
            with ThreadPoolExecutor(
                max_workers=self.worker_count,
                thread_name_prefix="application-worker",
            ) as pool:
                for _ in range(self.worker_count):
                    if not submit_one(pool):
                        break

                while futures:
                    done, _ = wait(
                        tuple(futures), timeout=0.25, return_when=FIRST_COMPLETED
                    )
                    if not done:
                        continue
                    for future in done:
                        task = futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = ApplicationWorkerResult(
                                task_id=task.task_id,
                                company=task.company,
                                role=task.role,
                            status=OutcomeStatus.FAILED_RETRYABLE.value,
                                detail=f"Worker failed: {type(exc).__name__}",
                            )
                        with self._lock:
                            self._statuses[task.task_id] = result.status
                            self._results[task.task_id] = result
                        submit_one(pool)
        finally:
            if self._cancel.is_set():
                self.cancel()
            with self._lock:
                self._running = False
            self._worker_fn = None


def load_application_tasks(path: str | Path) -> list[ApplicationTask]:
    queue_path = Path(path).expanduser()
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Application queue was not found: {queue_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Application queue is not valid JSON: {queue_path}") from exc

    raw_tasks = data.get("applications") if isinstance(data, dict) else data
    if not isinstance(raw_tasks, list):
        raise ValueError("Application queue must be a list or contain an 'applications' list.")
    if not raw_tasks:
        raise ValueError("Application queue is empty.")
    if len(raw_tasks) > MAX_APPLICATIONS_PER_BATCH:
        raise ValueError(
            f"A batch can contain at most {MAX_APPLICATIONS_PER_BATCH} applications."
        )

    tasks = [ApplicationTask.from_mapping(item, index) for index, item in enumerate(raw_tasks)]
    task_ids = [task.task_id for task in tasks]
    if any(not task_id for task_id in task_ids):
        raise ValueError("Every application requires a non-empty unique id.")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Every application id must be unique within the batch.")
    return tasks


def application_task_blockers(
    task: ApplicationTask,
    *,
    profile: dict[str, Any],
    default_cv_path: str,
    mode: RunMode | str | None = None,
) -> list[str]:
    selected_mode = RunMode.parse(mode)
    cv_path = task.cv_path or default_cv_path
    blockers = missing_application_details(
        profile,
        cv_path,
        mode="external",
        target_url=task.url,
    )
    if task.application_type not in {"law", "graduate", "other"}:
        blockers.append("Choose application_type: law, graduate, or other.")
    if not task.company:
        blockers.append("Provide the employer's exact name.")
    if not task.role:
        blockers.append("Provide the programme or role title.")

    parsed_url = urlparse(task.url)
    if task.url and (parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc):
        blockers.append("Provide a valid HTTP or HTTPS application URL.")

    application_text = f"{task.job_description}\n{task.portal_questions}"
    blockers.extend(
        missing_academic_details(
            profile,
            application_text=application_text,
            require_all=False,
        )
    )
    if not task.intake_confirmed:
        blockers.append("Confirm that all visible portal questions and limits were captured.")
    if selected_mode == RunMode.ASSISTED_REVIEW and not task.manual_submit_confirmed:
        blockers.append("Confirm that final review and submission will be completed manually.")
    return list(dict.fromkeys(blockers))


def validate_application_batch(
    tasks: list[ApplicationTask],
    *,
    profile: dict[str, Any],
    default_cv_path: str,
    mode: RunMode | str | None = None,
) -> dict[str, list[str]]:
    blockers: dict[str, list[str]] = {}
    for task in tasks:
        task_blockers = application_task_blockers(
            task,
            profile=profile,
            default_cv_path=default_cv_path,
            mode=mode,
        )
        if task_blockers:
            blockers[task.task_id] = task_blockers
    return blockers


def _run_application_worker(
    task: ApplicationTask,
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    default_cv_path: str,
    use_ai: bool,
    mode: RunMode | str | None = None,
) -> ApplicationWorkerResult:
    from playwright.sync_api import sync_playwright

    logs: list[str] = []
    cv_path = task.cv_path or default_cv_path
    job_context = "\n".join(
        part
        for part in (
            f"Application type: {task.application_type}",
            f"Employer: {task.company}",
            f"Programme or role: {task.role}",
            f"Office: {task.office}",
            f"Deadline: {task.deadline}",
            task.job_description,
            "Portal questions and limits:",
            task.portal_questions,
        )
        if part
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            if not browser.contexts:
                raise RuntimeError("The shared Chromium session has no browser context.")
            context = next(
                (candidate for candidate in browser.contexts if candidate.pages),
                browser.contexts[0],
            )
            page = context.new_page()
            page.goto(task.url, wait_until="domcontentloaded", timeout=90000)
            detail = fill_generic_application_form(
                page,
                profile=profile,
                defaults=defaults,
                cv_path=cv_path,
                job_context=job_context,
                company=task.company,
                role=task.role,
                use_ai=use_ai and task.ai_policy == AiPolicy.ALLOWED,
                dry_run=RunMode.parse(mode) == RunMode.LOCAL_PREVIEW,
                log=logs.append,
                mode=RunMode.parse(mode),
                ai_policy=task.ai_policy,
            )
        return ApplicationWorkerResult(
            task_id=task.task_id,
            company=task.company,
            role=task.role,
            status=detail.status.value,
            detail=detail.detail,
            logs=tuple(logs),
            legacy_status=detail.legacy_status,
            blockers=detail.blockers,
            actions=detail.actions,
            field_evidence=tuple(item.to_dict() for item in detail.field_evidence),
        )
    except Exception as exc:
        return ApplicationWorkerResult(
            task_id=task.task_id,
            company=task.company,
            role=task.role,
            status=OutcomeStatus.FAILED_RETRYABLE.value,
            detail=f"{type(exc).__name__}: {exc}",
            logs=tuple(logs),
        )


def run_application_batch(
    tasks: list[ApplicationTask],
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    default_cv_path: str,
    worker_count: int | None = None,
    use_ai: bool = False,
    mode: RunMode | str | None = None,
) -> dict[str, Any]:
    selected_mode = RunMode.parse(mode)
    blockers = validate_application_batch(
        tasks,
        profile=profile,
        default_cv_path=default_cv_path,
        mode=selected_mode,
    )
    if blockers:
        raise BatchValidationError(blockers)

    workers = resolve_worker_count(len(tasks), worker_count)
    indexed_results: dict[str, ApplicationWorkerResult] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="application-worker") as pool:
        futures = {
            pool.submit(
                _run_application_worker,
                task,
                profile=profile,
                defaults=defaults,
                default_cv_path=default_cv_path,
                use_ai=use_ai and task.allow_ai,
                mode=selected_mode,
            ): task.task_id
            for task in tasks
        }
        for future in as_completed(futures):
            result = future.result()
            indexed_results[result.task_id] = result

    ordered_results = [indexed_results[task.task_id] for task in tasks]
    summary = {
        "worker_count": workers,
        "application_count": len(tasks),
        "final_submission": "no_portal_changes" if selected_mode == RunMode.LOCAL_PREVIEW else "manual_only",
        "results": [result.to_dict() for result in ordered_results],
    }
    summary.update(
        _portfolio_completion_fields(
            summary["results"],
            running=False,
            expected_status=(
                OutcomeStatus.PREVIEW_READY.value
                if selected_mode == RunMode.LOCAL_PREVIEW
                else OutcomeStatus.REVIEW_READY.value
            ),
        )
    )
    return summary
