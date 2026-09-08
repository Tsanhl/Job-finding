"""Small, explicit selector profiles for common applicant-tracking systems."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class AtsAdapter:
    name: str
    host_markers: tuple[str, ...]
    page_markers: tuple[str, ...]
    root_selector: str
    file_selector: str
    input_selector: str
    select_selector: str
    submit_selector: str
    progress_labels: tuple[str, ...]


_COMMON_INPUTS = (
    "input[type='text'], input[type='email'], input[type='tel'], "
    "input[type='url'], input:not([type]), textarea"
)
_COMMON_SUBMIT = (
    "button:has-text('Submit application'), button:has-text('Submit Application'), "
    "button:has-text('Submit your application'), button:has-text('Send application'), "
    "input[type='submit'][value*='submit' i], input[type='submit'][value*='send' i]"
)


ATS_ADAPTERS: tuple[AtsAdapter, ...] = (
    AtsAdapter(
        name="ashby",
        host_markers=("ashbyhq.com",),
        page_markers=("ashby", "autofill from resume"),
        root_selector="#form, form",
        file_selector="input[type='file']",
        input_selector=_COMMON_INPUTS,
        select_selector="select",
        submit_selector="button:has-text('Submit Application'), button[type='submit']",
        progress_labels=("Continue", "Review"),
    ),
    AtsAdapter(
        name="greenhouse",
        host_markers=("greenhouse.io", "greenhouse.com"),
        page_markers=("greenhouse", "application_form"),
        root_selector="#application_form, form#application-form, form",
        file_selector="input[type='file']",
        input_selector=_COMMON_INPUTS,
        select_selector="select",
        submit_selector=(
            "button:has-text('Submit Application'), input[type='submit'][value*='Submit' i], "
            "button[type='submit']"
        ),
        progress_labels=("Continue", "Review"),
    ),
    AtsAdapter(
        name="workday",
        host_markers=("myworkdayjobs.com", "workday.com"),
        page_markers=("workday", "data-automation-id"),
        root_selector="[data-automation-id='applicationPage'], main, form",
        file_selector="input[type='file'], [data-automation-id='file-upload-input-ref']",
        input_selector=(
            "input[data-automation-id], textarea[data-automation-id], " + _COMMON_INPUTS
        ),
        select_selector="select, button[aria-haspopup='listbox']",
        submit_selector=(
            "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
            "button:has-text('Submit')"
        ),
        progress_labels=("Save and Continue", "Next", "Review"),
    ),
    AtsAdapter(
        name="taleo",
        host_markers=("taleo.net",),
        page_markers=("taleo", "candidateportal"),
        root_selector="form, #requisitionDescriptionInterface",
        file_selector="input[type='file']",
        input_selector=_COMMON_INPUTS,
        select_selector="select",
        submit_selector=(
            "input[type='submit'][value*='Submit' i], button:has-text('Submit'), "
            "a:has-text('Submit')"
        ),
        progress_labels=("Save and Continue", "Next", "Review"),
    ),
    AtsAdapter(
        name="lever",
        host_markers=("lever.co",),
        page_markers=("lever", "application-form"),
        root_selector="form.application-form, form",
        file_selector="input[type='file']",
        input_selector=_COMMON_INPUTS,
        select_selector="select",
        submit_selector="button:has-text('Submit application'), button[type='submit']",
        progress_labels=("Continue", "Review"),
    ),
    AtsAdapter(
        name="smartrecruiters",
        host_markers=("smartrecruiters.com",),
        page_markers=("smartrecruiters", "candidate portal"),
        root_selector="main form, form",
        file_selector="input[type='file']",
        input_selector=_COMMON_INPUTS,
        select_selector="select, [role='combobox']",
        submit_selector="button:has-text('Submit'), button[type='submit']",
        progress_labels=("Continue", "Next", "Review"),
    ),
)

GENERIC_ADAPTER = AtsAdapter(
    name="generic",
    host_markers=(),
    page_markers=(),
    root_selector="form, main, body",
    file_selector="input[type='file']",
    input_selector=_COMMON_INPUTS,
    select_selector="select",
    submit_selector=_COMMON_SUBMIT,
    progress_labels=("Next", "Continue", "Save and continue", "Review"),
)


def detect_ats(url: str, page_text: str = "") -> AtsAdapter:
    """Return the most specific supported ATS profile for a page."""
    host = (urlparse(url).hostname or "").lower()
    lowered = page_text.lower()[:12000]
    for adapter in ATS_ADAPTERS:
        if any(host == marker or host.endswith(f".{marker}") for marker in adapter.host_markers):
            return adapter
    for adapter in ATS_ADAPTERS:
        if any(marker in lowered for marker in adapter.page_markers):
            return adapter
    return GENERIC_ADAPTER
