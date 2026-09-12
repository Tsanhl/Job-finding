"""Deterministic request interpretation; proposals never carry execution authority."""

import re
from copy import deepcopy


def propose(text, *, targets, profile_version, documents=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("Enter a request of 1–4000 characters")
    words = text.casefold()
    negative_submit = bool(
        re.search(
            r"(?:do not|don't|never|without|no)\s+(?:auto(?:matically)?\s+)?submit|不要提交|不提交",
            words,
        )
    )
    worker_matches = re.findall(
        r"\b(\d+)\s*(?:workers?|concurrent applications?|at a time)\b", words
    )
    if len(set(worker_matches)) > 1:
        raise ValueError("Conflicting worker counts; specify one limit")
    workers = int(worker_matches[0]) if worker_matches else max(1, len(targets))
    if workers not in range(1, 11) or len(targets) > 10:
        raise ValueError("Split the selection into batches of at most ten")
    scope = (
        "CURRENT_PAGE"
        if re.search(r"current page|this page only|目前頁面|當前頁面", words)
        else "EXISTING_APPLICATION"
    )
    chosen = deepcopy(list(targets))
    submit = not negative_submit and bool(
        re.search(r"submit automatically|automatically submit|自動提交", words)
    )
    for target in chosen:
        target["scope"] = scope
        target["final_action"] = "SUBMIT" if submit else "REVIEW"
    permissions = (
        ["fill", "session"]
        + (["upload"] if documents else [])
        + (["submit"] if submit else [])
    )
    draft = {
        "request_schema": 2,
        "function": "AUTOFILL",
        "targets": chosen,
        "profile_version": profile_version,
        "documents": list(documents),
        "permissions": permissions,
        "workers": workers,
        "preview": bool(re.search(r"preview|inspect only|預覽", words)),
        "approval": "",
        "expires_at": 0,
    }
    return {
        "draft": draft,
        "requires_confirmation": True,
        "missing": ([] if chosen else ["Select the exact application tabs or URLs"])
        + ([] if profile_version else ["Select a profile version"]),
        "interpretation": {
            "function": "AUTOFILL",
            "scope": scope,
            "workers": workers,
            "final_action": "SUBMIT" if submit else "REVIEW",
        },
        "warnings": [
            "Create accounts, enter passwords and complete verification yourself before resuming."
        ],
    }
