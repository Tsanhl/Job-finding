"""Local multi-worker coordinator for prepared direct applications."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .application_flow import missing_academic_details, missing_application_details
from .browser_session import CDP_URL
from .external_apply import fill_generic_application_form

MAX_APPLICATION_WORKERS = 4
MAX_APPLICATIONS_PER_BATCH = 20


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
    intake_confirmed: bool = False
    ai_policy_confirmed: bool = False
    manual_submit_confirmed: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any], index: int) -> "ApplicationTask":
        if not isinstance(value, dict):
            raise ValueError(f"Application #{index + 1} must be a JSON object.")
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

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["logs"] = list(self.logs)
        return data


class BatchValidationError(ValueError):
    def __init__(self, blockers: dict[str, list[str]]) -> None:
        super().__init__("Application batch has unresolved intake questions.")
        self.blockers = blockers


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
) -> list[str]:
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
    if not task.ai_policy_confirmed:
        blockers.append("Confirm that the employer's AI policy was checked.")
    if not task.manual_submit_confirmed:
        blockers.append("Confirm that final review and submission will be completed manually.")
    return list(dict.fromkeys(blockers))


def validate_application_batch(
    tasks: list[ApplicationTask],
    *,
    profile: dict[str, Any],
    default_cv_path: str,
) -> dict[str, list[str]]:
    blockers: dict[str, list[str]] = {}
    for task in tasks:
        task_blockers = application_task_blockers(
            task,
            profile=profile,
            default_cv_path=default_cv_path,
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
                use_ai=use_ai,
                dry_run=False,
                log=logs.append,
            )
        status = "ready_for_manual_review"
        if detail.startswith("needs_") or "login" in detail:
            status = "needs_user_attention"
        elif "error" in detail:
            status = "failed"
        return ApplicationWorkerResult(
            task_id=task.task_id,
            company=task.company,
            role=task.role,
            status=status,
            detail=detail,
            logs=tuple(logs),
        )
    except Exception as exc:
        return ApplicationWorkerResult(
            task_id=task.task_id,
            company=task.company,
            role=task.role,
            status="failed",
            detail=f"{type(exc).__name__}: {exc}",
            logs=tuple(logs),
        )


def run_application_batch(
    tasks: list[ApplicationTask],
    *,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    default_cv_path: str,
    worker_count: int = 2,
    use_ai: bool = False,
) -> dict[str, Any]:
    blockers = validate_application_batch(
        tasks,
        profile=profile,
        default_cv_path=default_cv_path,
    )
    if blockers:
        raise BatchValidationError(blockers)

    workers = max(1, min(int(worker_count), MAX_APPLICATION_WORKERS, len(tasks)))
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
            ): task.task_id
            for task in tasks
        }
        for future in as_completed(futures):
            result = future.result()
            indexed_results[result.task_id] = result

    ordered_results = [indexed_results[task.task_id] for task in tasks]
    return {
        "worker_count": workers,
        "application_count": len(tasks),
        "final_submission": "manual_only",
        "results": [result.to_dict() for result in ordered_results],
    }
