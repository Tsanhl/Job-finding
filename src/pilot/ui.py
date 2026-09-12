"""Function-based dashboard; all browser work belongs to the foreground runtime."""

import json
import time
from pathlib import Path

import streamlit as st

from .discovery import format_job
from .runtime import client
from .store import default_home


def _filter_discovered_jobs(rows, *, keywords="", location="", limit=10):
    """Keep discovery filtering deterministic and local to the dashboard."""
    words = [word.casefold() for word in keywords.split() if len(word) > 2]
    wanted_location = location.strip().casefold()
    selected = []
    for row in rows:
        searchable = " ".join(
            str(row.get(key) or "")
            for key in ("role", "employer", "requirements", "employment_type")
        ).casefold()
        row_location = str(row.get("location") or "").casefold()
        if words and not any(word in searchable for word in words):
            continue
        if wanted_location and wanted_location not in row_location:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _job_table(rows):
    def checked(value):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(float(value)))
        except (TypeError, ValueError, OverflowError):
            return "Unknown"

    return [
        {
            "Role": row.get("role") or "",
            "Employer": row.get("employer") or "",
            "Location": row.get("location") or "Unspecified",
            "Opens": row.get("opening") or "Unspecified",
            "Closes": row.get("deadline") or "Unspecified",
            "Requirements": row.get("requirements") or "Not stated in source",
            "Type": row.get("employment_type") or "Unknown",
            "Pay": row.get("pay") or "Unknown",
            "Source": row.get("provider") or row.get("source") or "Unknown",
            "Checked": checked(row.get("checked_at")),
            "Link": row.get("url") or "",
        }
        for row in rows
    ]


def _profile_value(profile, path):
    value = profile
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return str(value or "")


def _set_profile_value(profile, path, value):
    target = profile
    parts = path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def _active_targets(rows):
    return [
        {key: value for key, value in row.items() if value not in (None, "")}
        for row in rows
        if str(row.get("url") or "").strip()
    ]


def render():
    st.title("ApplyPilot")
    st.caption("Private local job search, autofill and application review")
    home = st.sidebar.text_input("Runtime directory", str(default_home()))

    def call(op, **kw):
        return client({"op": op, **kw}, home)

    try:
        status = call("status")
    except (OSError, ValueError, RuntimeError):
        st.info("Start the managed browser and foreground runtime, then refresh.")
        st.code(
            ".venv-upgrade/bin/python scripts/open_browser.py\n.venv-upgrade/bin/python cli.py runtime start"
        )
        st.button("Refresh")
        return
    setup = call("setup_status")
    if not setup["ready"]:
        st.warning(
            "Application setup is incomplete: " + "; ".join(setup["missing"])
            + ". Find Jobs and Open Job Links remain available."
        )
    if st.button("Refresh status"):
        st.rerun()
    st.caption(
        "Choose a function below. You create employer accounts and verify email. "
        "By default, Autofill completes every supported non-final section and "
        "stops on the application's final Review page."
    )

    def status_marker(value):
        return (
            [(a["id"], a["state"], a.get("detail")) for a in value["applications"]],
            [(r["id"], r["status"]) for r in value["runs"]],
            [q["key"] for q in value["questions"]],
        )

    @st.fragment(run_every="3s")
    def live_progress():
        try:
            fresh = call("status")
        except (OSError, ValueError, RuntimeError):
            st.info("Runtime disconnected; saved forms remain available for reconnect.")
            return
        if status_marker(fresh) != status_marker(status):
            st.rerun(scope="app")
        cols = st.columns(4)
        cols[0].metric("Active workers", fresh["active_workers"])
        cols[1].metric("Retained contexts", fresh["retained_contexts"])
        cols[2].metric(
            "Review ready",
            sum(a["state"] == "REVIEW_READY" for a in fresh["applications"]),
        )
        cols[3].metric(
            "Done",
            sum(
                a["state"] in {"SUBMITTED_CONFIRMED", "SUBMITTED_USER_REPORTED"}
                for a in fresh["applications"]
            ),
        )

    live_progress()
    tabs = st.tabs(
        [
            "Applications",
            "Autofill",
            "Find jobs",
            "LinkedIn Easy Apply",
            "Open job links",
            "Profile & documents",
            "Settings",
        ]
    )
    with tabs[0]:
        if status["questions"]:
            st.subheader("Information needed across your applications")
            with st.form("questions"):
                answers = {}
                for q in status["questions"]:
                    st.caption(
                        f"{q['employer']} · {q.get('record') or 'Application'} · {q['reason']}"
                    )
                    if q["kind"] in {
                        "decision",
                        "document",
                        "upload_pending",
                        "technical",
                        "policy",
                    }:
                        st.write(q["question"])
                        continue
                    answers[q["key"]] = st.text_input(
                        q["question"],
                        value=q.get("suggested_value", ""),
                        key="q-" + q["key"],
                    )
                reuse = st.checkbox(
                    "Save compatible personal facts as a new reusable profile version"
                )
                confirmed = st.checkbox(
                    "These answers are accurate; use them to resume the affected forms"
                )
                if st.form_submit_button("Save answers together and resume"):
                    if not confirmed:
                        st.warning("Confirm the answers first.")
                    else:
                        call(
                            "answer_questions",
                            answers={k: v for k, v in answers.items() if v},
                            reuse=reuse,
                        )
                        st.rerun()
        for app in status["applications"]:
            with st.expander(f"{app['employer']} — {app['role']} · {app['state']}"):
                st.write(app["detail"])
                if app.get("updated"):
                    st.caption(
                        f"Last progress {max(0, int(time.time() - app['updated']))} seconds ago"
                    )
                st.caption(app["id"])
                c = st.columns(4)
                if c[0].button("Open form", key="open-" + app["id"]):
                    call("open", application_id=app["id"])
                if c[1].button("Review", key="review-" + app["id"]):
                    st.json(call("review", application_id=app["id"]))
                if c[2].button("I submitted it", key="done-" + app["id"]):
                    call("submitted", application_id=app["id"])
                    st.rerun()
                if c[3].button("Detect my edits", key="edits-" + app["id"]):
                    st.session_state["edits-" + app["id"]] = call(
                        "detect_edits", application_id=app["id"]
                    )
                edits = st.session_state.get("edits-" + app["id"])
                if edits:
                    st.json(edits["changes"])
                    if st.button(
                        "Confirm reusable additions and resume",
                        key="adopt-" + app["id"],
                    ):
                        call("adopt_edits", application_id=app["id"])
                        st.session_state.pop("edits-" + app["id"], None)
                        st.rerun()
                if st.button("Resume after my edits", key="resumeapp-" + app["id"]):
                    call("resume_application", application_id=app["id"])
                    st.rerun()
                if st.button(
                    "Refresh tabs for reconnect", key="reconnect-" + app["id"]
                ):
                    st.session_state["reconnect-tabs"] = call("tabs")
                reconnect = st.session_state.get("reconnect-tabs", [])
                if reconnect:
                    tab_id = st.selectbox(
                        "Application tab",
                        [r["tab_id"] for r in reconnect],
                        format_func=lambda id, rows=reconnect: next(
                            r["url"] for r in rows if r["tab_id"] == id
                        ),
                        key="tab-" + app["id"],
                    )
                    if st.button(
                        "Confirm this is the same application", key="bind-" + app["id"]
                    ):
                        call(
                            "bind_tab",
                            application_id=app["id"],
                            tab_id=tab_id,
                            confirmed=True,
                        )
                        st.rerun()
                release = st.checkbox(
                    "I saved/reviewed this form and permit closing it",
                    key="releaseconfirm-" + app["id"],
                )
                if st.button(
                    "Release retained form",
                    disabled=not release,
                    key="release-" + app["id"],
                ):
                    call("release", application_id=app["id"], confirmed=True)
                    st.rerun()
        for run in status["runs"]:
            plan = json.loads(run["plan"])
            effective = min(
                plan["workers"],
                status.get("resources", {}).get("memory_worker_budget", 10),
            )
            st.write(
                f"Run {run['id'][:8]} · {run['status']} · requested {plan['workers']} workers · current memory limit {effective}"
            )
            c = st.columns(3)
            for column, op in zip(c, ["pause", "resume", "stop"]):
                if column.button(op.title(), key=op + run["id"]):
                    call(op, run_id=run["id"])
                    st.rerun()
    with tabs[1]:
        profiles = call("profiles")
        documents = call("documents")
        if not profiles:
            st.info(
                "Review and import existing local records under Profile & documents first."
            )
        else:
            version = st.selectbox(
                "Confirmed profile version", [p["id"] for p in profiles]
            )
            selected = st.multiselect(
                "Approved documents",
                [d["id"] for d in documents if d["approved"]],
                format_func=lambda id: next(
                    f"{d['kind']} · {Path(d['path']).name} · v{d['version']}"
                    for d in documents
                    if d["id"] == id
                ),
            )
            if st.button("List managed browser tabs"):
                st.session_state["managed_tabs"] = call("tabs")
            if "managed_tabs" in st.session_state:
                st.dataframe(st.session_state["managed_tabs"])
            st.caption(
                "One row per application. For existing tabs, paste its tab_id and exact application URL. Identity is the requisition/cycle or your reviewed unique label."
            )
            targets = st.data_editor(
                [
                    {
                        "url": "",
                        "employer": "",
                        "role": "",
                        "identity": "",
                        "tab_id": "",
                        "portal_application_id": "",
                        "account": "",
                        "country": "",
                        "scope": "EXISTING_APPLICATION",
                        "ai_policy": "unknown",
                        "eligibility": "unknown",
                        "final_action": "REVIEW",
                    }
                ],
                num_rows="dynamic",
                column_config={
                    "final_action": st.column_config.SelectboxColumn(
                        "After filling",
                        options=["REVIEW", "SUBMIT"],
                        default="REVIEW",
                        help="REVIEW is the default. SUBMIT is target-specific authority.",
                    )
                },
                key="targets-autofill",
            )
            request_text = st.text_input(
                "Describe the filling scope (optional)",
                placeholder="Fill these applications with 5 workers; do not submit",
            )
            if st.button("Interpret request", disabled=not request_text.strip()):
                from .intent import propose

                st.session_state["interpreted-request"] = propose(
                    request_text,
                    targets=_active_targets(targets),
                    profile_version=version,
                    documents=selected,
                )
            interpreted = st.session_state.get("interpreted-request")
            if interpreted:
                st.json(interpreted["interpretation"])
                st.caption(
                    "Review the requested function, application count and submission preference before starting."
                )
            workers = st.number_input(
                "Requested workers",
                min_value=1,
                max_value=10,
                value=max(1, min(len(targets), 10)),
            )
            st.info(
                "Create or sign into employer accounts yourself, then Resume. "
                "ApplyPilot does not generate or retain employer passwords. Avoid "
                "passwords in chat because local deletion cannot erase chat history. "
                "Complete any email verification yourself and Resume."
            )
            permissions = st.multiselect(
                "Authorised capabilities",
                ["fill", "session", "upload", "external_ai", "research", "submit"],
                default=["fill", "session"] + (["upload"] if selected else []),
                key="permissions-autofill",
            )
            preview = st.checkbox("Inspect only; do not change the forms")
            chosen_targets = _active_targets(targets)
            has_submit = any(
                target.get("final_action") == "SUBMIT" for target in chosen_targets
            )
            supervised = st.checkbox(
                "I specifically authorise a supervised live acceptance submission",
                disabled=not has_submit,
            )
            approved = st.checkbox(
                "I authorise these targets, selected documents and capabilities for the next two hours",
                key="approve-autofill",
            )
            if st.button(
                "Start selected applications",
                disabled=not approved or not setup["ready"],
            ):
                if has_submit and "submit" not in permissions:
                    st.warning("Add the submit capability for every SUBMIT target.")
                    st.stop()
                result = call(
                    "start",
                    plan={
                        "request_schema": 2,
                        "function": "AUTOFILL",
                        "targets": chosen_targets,
                        "profile_version": version,
                        "documents": selected,
                        "workers": int(workers),
                        "permissions": permissions,
                        "preview": preview,
                        "supervised_acceptance": supervised,
                        "approval": "Explicit local dashboard confirmation",
                        "expires_at": time.time() + 7200,
                    },
                )
                st.success("Queued " + result["run_id"])
                st.rerun()
    with tabs[2]:
        st.write(
            "Search Bright Network, registered priority ATS career sites, and "
            "optional supplied vacancy pages. This function never applies."
        )
        include_builtin = st.checkbox(
            "Include Bright Network and registered Workday, AllHires and Apply4Law sources",
            value=True,
        )
        registered_sources = call("source_roots")
        if registered_sources:
            st.dataframe(registered_sources, hide_index=True)
        with st.expander("Register a priority ATS career site"):
            source_name = st.text_input("Source name")
            source_root = st.text_input("Public Workday, AllHires or Apply4Law URL")
            if st.button("Register public source", disabled=not source_root.strip()):
                call("register_source", name=source_name, url=source_root)
                st.rerun()
        keywords = st.text_input(
            "Job area or role", placeholder="technology, software engineering, law"
        )
        location = st.text_input(
            "Location filter", placeholder="London", key="discovery-location"
        )
        sources = st.text_area(
            "Additional permitted vacancy pages or feeds (one HTTPS URL per line)",
            key="discovery-sources",
        )
        requested = st.number_input(
            "Number of jobs requested",
            min_value=1,
            max_value=100,
            value=10,
            key="discovery-count",
        )
        approved = st.checkbox(
            "I authorise reading these public sources for this local search",
            key="discovery-approved",
        )
        if st.button("Find jobs", disabled=not approved or not keywords.strip()):
            source_urls = [
                value.strip() for value in sources.splitlines() if value.strip()
            ]
            result = call(
                "find_jobs",
                query=keywords,
                location=location,
                requested=int(requested),
                include_builtin=include_builtin,
                sources=source_urls,
            )
            st.session_state["discovery-run"] = result["run_id"]
            st.session_state["discovered-jobs"] = result["jobs"]
        discovered = st.session_state.get("discovered-jobs", [])
        if discovered:
            st.subheader("Portal checklist")
            for job in discovered:
                st.markdown(format_job(job, markdown=True))
            st.dataframe(
                _job_table(discovered),
                hide_index=True,
                column_config={"Link": st.column_config.LinkColumn("Link")},
            )
        elif "discovered-jobs" in st.session_state:
            st.info("No source entries matched the requested role and location.")
    with tabs[3]:
        st.write(
            "Fill eligible LinkedIn Easy Apply forms until the requested number "
            "reaches final review. Submit is always left for you."
        )
        profiles = call("profiles")
        documents = call("documents")
        linkedin_profile = (
            st.selectbox(
                "LinkedIn profile version",
                [profile["id"] for profile in profiles],
                key="linkedin-profile",
            )
            if profiles
            else ""
        )
        linkedin_documents = st.multiselect(
            "LinkedIn approved documents",
            [document["id"] for document in documents if document["approved"]],
            format_func=lambda identity: next(
                f"{document['kind']} · v{document['version']}"
                for document in documents
                if document["id"] == identity
            ),
        )
        linkedin_keywords = st.text_input(
            "LinkedIn role or keywords", placeholder="graduate software engineer"
        )
        linkedin_location = st.text_input("LinkedIn location", value="United Kingdom")
        linkedin_count = st.number_input(
            "Easy Apply applications requested",
            min_value=1,
            max_value=10,
            value=1,
        )
        linkedin_approved = st.checkbox(
            "I authorise this bounded LinkedIn Easy Apply run for two hours"
        )
        if st.button(
            "Start LinkedIn Easy Apply",
            disabled=(
                not linkedin_approved
                or not setup["ready"]
                or not linkedin_keywords.strip()
                or not linkedin_profile
            ),
        ):
            st.session_state["linkedin-request"] = call(
                "linkedin_easy_apply",
                keywords=linkedin_keywords,
                location=linkedin_location,
                requested=int(linkedin_count),
                profile_version=linkedin_profile,
                documents=linkedin_documents,
            )
        batches = status.get("linkedin_batches", [])
        if batches:
            st.dataframe(batches, hide_index=True)
            resumable = [
                batch
                for batch in batches
                if batch["status"] in {"NEEDS_AUTHENTICATION", "CAPACITY_WAIT"}
            ]
            if resumable:
                resume_batch = st.selectbox(
                    "LinkedIn batch to resume",
                    [batch["batch_id"] for batch in resumable],
                )
                if st.button("Resume selected LinkedIn batch"):
                    st.session_state["linkedin-request"] = call(
                        "linkedin_resume", batch_id=resume_batch
                    )
                    st.rerun()
        if st.session_state.get("linkedin-request"):
            st.json(st.session_state["linkedin-request"])
    with tabs[4]:
        discovered = st.session_state.get("discovered-jobs", [])
        default_links = "\n".join(
            str(row.get("url") or "") for row in discovered if row.get("url")
        )
        st.write("Open up to ten exact job links in the managed browser.")
        links = st.text_area(
            "Job links (one HTTPS URL per line)",
            value=default_links,
            key="job-links",
        )
        open_count = st.number_input(
            "Links to open", min_value=1, max_value=10, value=1
        )
        if st.button("Open requested job links"):
            urls = [value.strip() for value in links.splitlines() if value.strip()]
            if not urls:
                st.warning("Add at least one job link.")
            else:
                st.session_state["opened-job-links"] = call(
                    "open_job_links", urls=urls, requested=int(open_count)
                )
        if st.session_state.get("opened-job-links"):
            st.dataframe(st.session_state["opened-job-links"], hide_index=True)
    with tabs[5]:
        st.write(
            "Existing files remain untouched. Import requires a reviewed dry run and a database backup."
        )
        profiles = call("profiles")
        setup_parent = profiles[0]["id"] if profiles else ""
        setup_profile = call("profile", version=setup_parent) if setup_parent else {}
        education_values = setup_profile.get("education") or {}
        education_is_list = isinstance(education_values, list)
        education_value = (
            education_values[0]
            if education_is_list and education_values
            else ({} if education_is_list else education_values)
        )
        work_values = setup_profile.get("work_experience") or []
        work_value = work_values[0] if isinstance(work_values, list) and work_values else {}
        st.subheader("Reusable profile setup")
        st.caption(
            "Required before Autofill or LinkedIn Easy Apply. Values stay in the "
            "local Application Support database and are never added to Git."
        )
        with st.form("reusable-profile-setup"):
            setup_name = st.text_input(
                "Legal full name", value=str(setup_profile.get("full_name") or "")
            )
            setup_email = st.text_input(
                "Application email", value=str(setup_profile.get("email") or "")
            )
            setup_phone = st.text_input(
                "Phone number", value=str(setup_profile.get("phone") or "")
            )
            setup_location = st.text_input(
                "Current location or address",
                value=str(setup_profile.get("location") or ""),
            )
            no_education = st.checkbox(
                "I have no education record to add",
                value=setup_profile.get("education_none_confirmed") is True,
            )
            education_institution = st.text_input(
                "Education institution",
                value=str(education_value.get("institution") or education_value.get("school") or ""),
                disabled=no_education,
            )
            education_degree = st.text_input(
                "Degree or qualification",
                value=str(education_value.get("degree") or education_value.get("degree_type") or ""),
                disabled=no_education,
            )
            no_work = st.checkbox(
                "I have no work history to add",
                value=setup_profile.get("work_experience_none_confirmed") is True,
            )
            work_employer = st.text_input(
                "Most recent employer",
                value=str(work_value.get("employer") or ""),
                disabled=no_work,
            )
            work_position = st.text_input(
                "Most recent position",
                value=str(work_value.get("position") or work_value.get("title") or ""),
                disabled=no_work,
            )
            authorised = st.selectbox(
                "Authorised to work in the target country",
                ["", "Yes", "No"],
                index=(
                    ["", "Yes", "No"].index(
                        _profile_value(setup_profile, "answers.authorized_to_work")
                    )
                    if _profile_value(setup_profile, "answers.authorized_to_work")
                    in {"", "Yes", "No"}
                    else 0
                ),
            )
            sponsorship = st.selectbox(
                "Require employer sponsorship",
                ["", "Yes", "No"],
                index=(
                    ["", "Yes", "No"].index(
                        _profile_value(setup_profile, "answers.require_sponsorship")
                    )
                    if _profile_value(setup_profile, "answers.require_sponsorship")
                    in {"", "Yes", "No"}
                    else 0
                ),
            )
            setup_confirmed = st.checkbox(
                "I confirm these reusable facts are accurate"
            )
            if st.form_submit_button(
                "Save reusable profile", disabled=not setup_confirmed
            ):
                updated = json.loads(json.dumps(setup_profile))
                updated.update(
                    {
                        "full_name": setup_name,
                        "email": setup_email,
                        "phone": setup_phone,
                        "location": setup_location,
                        "education_none_confirmed": no_education,
                        "work_experience_none_confirmed": no_work,
                    }
                )
                if not no_education:
                    first_education = {
                        **education_value,
                        "institution": education_institution,
                        "degree": education_degree,
                    }
                    updated["education"] = (
                        [first_education] + education_values[1:]
                        if education_is_list
                        else first_education
                    )
                if not no_work:
                    first_work = {
                        **work_value,
                        "employer": work_employer,
                        "position": work_position,
                    }
                    updated["work_experience"] = [first_work] + (
                        work_values[1:] if isinstance(work_values, list) else []
                    )
                _set_profile_value(updated, "answers.authorized_to_work", authorised)
                _set_profile_value(updated, "answers.require_sponsorship", sponsorship)
                updated["setup_confirmed_at"] = time.time()
                result = call(
                    "save_profile",
                    payload=updated,
                    **({"parent": setup_parent} if setup_parent else {}),
                )
                st.success("Saved private profile version " + result["version"])
                st.rerun()
        if profiles:
            from .question_catalog import load as load_question_catalog

            catalogue = load_question_catalog()
            st.subheader("Screening question catalogue")
            st.caption(
                f"The catalogue contains {len(catalogue['questions'])} blank "
                "definitions. Confirmed reusable answers create a private local "
                "profile version. Git never contains candidate answers. Questions "
                "marked per application are asked only when encountered."
            )
            preset_version = st.selectbox(
                "Profile version to update",
                [p["id"] for p in profiles],
                key="preset-profile-version",
            )
            preset_profile = call("profile", version=preset_version)
            with st.form("preset-screening-questions"):
                preset_answers = {}
                category = ""
                for item in catalogue["questions"]:
                    if item["category"] != category:
                        category = item["category"]
                        st.markdown(f"**{category}**")
                    path = item.get("profile_path")
                    if not path:
                        timing = (
                            "asked only when required"
                            if item.get("ask_policy") == "required_only"
                            else "asked per application when encountered"
                        )
                        st.caption(item["prompt"] + " — " + timing)
                        continue
                    current = _profile_value(preset_profile, path)
                    if current is True:
                        current = "Yes"
                    elif current is False:
                        current = "No"
                    label = item["prompt"] + (
                        " (required for autofill setup)"
                        if item.get("setup_required")
                        else " (optional reusable answer)"
                    )
                    choices = item.get("choices")
                    if item["answer_type"] == "yes_no" or choices:
                        options = (
                            ["", "Yes", "No"]
                            if item["answer_type"] == "yes_no"
                            else [""] + choices
                        )
                        index = options.index(current) if current in options else 0
                        preset_answers[path] = st.selectbox(
                            label,
                            options,
                            index=index,
                            key="preset-" + item["id"],
                        )
                    else:
                        preset_answers[path] = st.text_input(
                            label,
                            value=str(current or ""),
                            key="preset-" + item["id"],
                        )
                    if item.get("review_reason"):
                        st.caption(item["review_reason"])
                preset_confirmed = st.checkbox(
                    "I confirm these reusable answers are accurate"
                )
                if st.form_submit_button(
                    "Save private screening answers", disabled=not preset_confirmed
                ):
                    updated = json.loads(json.dumps(preset_profile))
                    for path, value in preset_answers.items():
                        if value:
                            _set_profile_value(updated, path, value)
                    updated.setdefault("preset_answers_confirmed_at", {}).update(
                        {
                            path: time.time()
                            for path, value in preset_answers.items()
                            if value
                        }
                    )
                    result = call(
                        "save_profile", payload=updated, parent=preset_version
                    )
                    st.success("Saved private profile version " + result["version"])
        st.code(
            ".venv-upgrade/bin/python cli.py runtime migration-preview data/profile.local.json application_profile.json --output /private/tmp/applypilot-import-review.json"
        )
        st.caption(
            "Resolve reported conflicts in the reviewed preview, preserving the source payloads. Then explicitly approve import:"
        )
        st.code(
            ".venv-upgrade/bin/python cli.py runtime import --reviewed-preview /private/tmp/applypilot-import-review.json --backup /private/tmp/applypilot-before-import.sqlite3 --approve-live-import"
        )
        sources = st.text_area(
            "Existing profile files (one path per line)",
            value=str(Path(__file__).resolve().parents[2] / "data/profile.local.json")
            + "\n"
            + str(Path(__file__).resolve().parents[2] / "application_profile.json"),
        )
        if st.button("Preview existing records without changing them"):
            st.session_state["migration_preview"] = call(
                "migration_preview",
                paths=[p.strip() for p in sources.splitlines() if p.strip()],
            )
        preview = st.session_state.get("migration_preview")
        if preview:
            st.caption(
                "Original source payloads and unknown custom fields are retained. Confirm only facts you have reviewed."
            )
            resolutions = {}
            for key in preview["conflicts"]:
                candidates = [
                    source for source in preview["sources"] if key in source["payload"]
                ]
                chosen = st.selectbox(
                    "Resolve " + key,
                    [source["path"] for source in candidates],
                    key="conflict-" + key,
                )
                resolutions[key] = next(
                    source["payload"][key]
                    for source in candidates
                    if source["path"] == chosen
                )
                with st.expander("Preview " + key):
                    st.json(resolutions[key])
            confirmed_fields = st.multiselect(
                "Reviewed, confirmed profile sections", list(preview["merged"])
            )
            with st.expander("Full private preview"):
                st.json({**preview["merged"], **resolutions})
            approve_import = st.checkbox(
                "I approve creating a new local snapshot from this preview after a backup; keep my original files"
            )
            if st.button(
                "Back up and import this reviewed snapshot", disabled=not approve_import
            ):
                backup = str(
                    Path(home) / ("before-import-" + str(time.time_ns()) + ".sqlite3")
                )
                call("backup", destination=backup)
                updated = {
                    **preview,
                    "merged": {**preview["merged"], **resolutions},
                    "conflicts": [],
                    "confirmed_fields": confirmed_fields,
                }
                call("import_legacy", preview=updated, confirmed=True)
                st.session_state.pop("migration_preview", None)
                st.success("Snapshot imported; original files preserved")
                st.rerun()
        registered = call("documents")
        approved_docs = [d for d in registered if d["approved"]]
        if approved_docs:
            selected_preview = st.selectbox(
                "Preview an approved document",
                [d["id"] for d in approved_docs],
                format_func=lambda id: next(
                    Path(d["path"]).name for d in approved_docs if d["id"] == id
                ),
            )
            if st.button("Read document text locally"):
                result = call("document_preview", document_id=selected_preview)
                st.text_area(
                    "Extracted text — review before treating it as confirmed information",
                    result["text"],
                    height=250,
                )
        with st.form("document"):
            path = st.text_input("Local document path")
            kind = st.selectbox(
                "Document type",
                ["cv", "transcript", "cover_letter", "writing_sample", "other"],
            )
            approved = st.checkbox("I approve this exact file version for applications")
            employer = st.text_input("Limit to employer (optional)")
            authorship = st.selectbox(
                "Authorship", ["unknown", "candidate", "ai-assisted"]
            )
            if st.form_submit_button("Register document"):
                call(
                    "register_document",
                    path=path,
                    kind=kind,
                    approved=approved,
                    applicability={
                        **({"employer": employer} if employer else {}),
                        "authorship": authorship,
                    },
                )
                st.success("Document registered")
    with tabs[6]:
        if st.button("Doctor"):
            st.json(call("doctor"))
        if st.button("Clear derived cache"):
            call("cache_clear")
            st.success("Authoritative records preserved")
        st.info(
            "Employer passwords are not collected or saved. Create accounts and sign "
            "in directly on the employer website. If verification is required, finish "
            "it in your email or browser and select Resume."
        )

        with st.form("ai_setup"):
            st.write("Optional external AI — deterministic filling works without this")
            key = st.text_input("OpenAI API key", type="password")
            if st.form_submit_button("Save API key in Keychain"):
                call("ai_configure", api_key=key)
                st.success(
                    "Key saved; generation still requires explicit run permission and employer policy"
                )
