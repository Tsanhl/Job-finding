from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.answers import answer_many
from src.application_flow import (
    application_intake_questions,
    direct_application_intake_questions,
    missing_application_details,
)
from src.browser_session import get_context
from src.config import ensure_dirs, load_config
from src.cover_letter import generate_cover_letter, save_cover_letter
from src.external_apply import fill_generic_application_form
from src.linkedin_apply import run_linkedin_auto_apply, slugify
from src.profile import extract_cv_text, load_profile, save_profile

st.set_page_config(page_title="ApplyPilot", page_icon="📄", layout="wide")

cfg = load_config()
ensure_dirs(cfg)

st.title("ApplyPilot")
st.caption(
    "Local job-application helper: cover letters, screening answers, CV attach, "
    "and assisted LinkedIn Easy Apply from your logged-in browser session."
)

with st.expander("Important notes", expanded=False):
    st.markdown(
        """
- LinkedIn automation can conflict with LinkedIn's Terms of Service. Use at your own risk,
  keep daily volume low, and review every application when possible.
- You must already be logged into LinkedIn in the browser window the tool opens
  (or log in when prompted). This tool does **not** store your LinkedIn password.
- Always verify visa/sponsorship answers before submitting real applications.
- Optional `OPENAI_API_KEY` in `.env` improves cover letters and open-ended answers.
  Without it, solid CV-based templates are used.
"""
    )

tabs = st.tabs(
    [
        "Profile & CV",
        "Cover letter",
        "Screening answers",
        "LinkedIn Easy Apply",
        "Direct Apply",
        "Run logs",
    ]
)

# ---------- Profile ----------
with tabs[0]:
    profile = load_profile()
    st.subheader("Your application profile")
    c1, c2 = st.columns(2)
    with c1:
        profile["full_name"] = st.text_input("Full name", profile.get("full_name", ""))
        profile["email"] = st.text_input("Email", profile.get("email", ""))
        profile["phone"] = st.text_input("Phone", profile.get("phone", ""))
        profile["location"] = st.text_input("Location", profile.get("location", ""))
    with c2:
        profile["linkedin_url"] = st.text_input("LinkedIn URL", profile.get("linkedin_url", ""))
        profile["github_url"] = st.text_input("GitHub URL", profile.get("github_url", ""))
        answers = profile.setdefault("answers", {})
        answers["authorized_to_work"] = st.text_input(
            "Authorized to work", answers.get("authorized_to_work", ""),
            placeholder="Yes or No — confirm before applying",
        )
        answers["require_sponsorship"] = st.text_input(
            "Require sponsorship?",
            answers.get("require_sponsorship", ""),
            placeholder="Yes or No — confirm before applying",
        )
        answers["start_date"] = st.text_input(
            "Start date / availability", answers.get("start_date", "")
        )

    profile["summary"] = st.text_area("Summary", profile.get("summary", ""), height=120)
    profile["skills"] = st.text_area(
        "Skills (one per line)",
        "\n".join(profile.get("skills", [])),
        height=120,
    ).splitlines()
    profile["experience_highlights"] = st.text_area(
        "Experience highlights (one per line)",
        "\n".join(profile.get("experience_highlights", [])),
        height=160,
    ).splitlines()

    cv_path = st.text_input("CV PDF path", cfg.get("cv_path", ""))
    if st.button("Save profile", type="primary"):
        save_profile(profile)
        cfg["cv_path"] = cv_path
        st.success("Profile saved.")

    if cv_path and Path(cv_path).is_file():
        with st.expander("Preview extracted CV text"):
            st.text(extract_cv_text(cv_path)[:5000])
    else:
        st.warning("CV path not found — update it before applying.")

# ---------- Cover letter ----------
with tabs[1]:
    profile = load_profile()
    st.subheader("Generate a cover letter")
    col1, col2 = st.columns(2)
    with col1:
        company = st.text_input("Company", "Example LLP")
        role = st.text_input("Role", "Legal Intern")
        location = st.text_input("Role location", cfg.get("defaults", {}).get("location", ""))
    with col2:
        use_ai = st.checkbox("Use OpenAI if API key is set", value=True)
        job_description = st.text_area("Paste job description (optional)", height=180)

    if st.button("Generate cover letter", type="primary"):
        letter = generate_cover_letter(
            profile,
            company=company,
            role=role,
            location=location,
            job_description=job_description,
            use_ai=use_ai,
        )
        st.session_state["cover_letter"] = letter
        fname = f"cover_letter_{slugify(company)}_{slugify(role)}.txt"
        path = save_cover_letter(letter, cfg["output_dir"], fname)
        st.success(f"Saved to {path}")

    if "cover_letter" in st.session_state:
        st.text_area("Cover letter", st.session_state["cover_letter"], height=420)
        st.download_button(
            "Download .txt",
            st.session_state["cover_letter"],
            file_name="cover_letter.txt",
            mime="text/plain",
        )

# ---------- Screening answers ----------
with tabs[2]:
    profile = load_profile()
    st.subheader("Answer screening questions")
    defaults = dict(cfg.get("defaults", {}))
    questions_raw = st.text_area(
        "Paste questions (one per line)",
        "\n".join(
            [
                "What is your email address?",
                "Are you authorized to work in the UK?",
                "Do you require visa sponsorship?",
                "When can you start?",
                "Why are you interested in this role?",
                "How many years of experience do you have?",
            ]
        ),
        height=200,
    )
    job_context = st.text_area("Job context (optional)", height=100)
    use_ai_q = st.checkbox("Use OpenAI for unanswered questions", value=True, key="ai_q")

    if st.button("Generate answers", type="primary"):
        questions = [q.strip() for q in questions_raw.splitlines() if q.strip()]
        answers = answer_many(
            questions,
            profile,
            job_context=job_context,
            defaults=defaults,
            use_ai=use_ai_q,
        )
        st.session_state["screening_answers"] = answers

    if "screening_answers" in st.session_state:
        for q, a in st.session_state["screening_answers"].items():
            st.markdown(f"**Q:** {q}")
            st.write(a)
            st.divider()

# ---------- LinkedIn ----------
with tabs[3]:
    profile = load_profile()
    st.subheader("LinkedIn Easy Apply (assisted)")
    st.info(
        "A Chromium window will open. Log into LinkedIn there if needed. "
        "Start with **Dry run** (fills forms, does not submit)."
    )

    d = cfg.get("defaults", {})
    li = cfg.get("linkedin", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        keywords = st.text_input("Job keywords / area", d.get("keywords", ""))
        location = st.text_input("Location filter", d.get("location", ""), key="li_loc")
    with c2:
        max_apps = st.number_input(
            "Max applications this run",
            min_value=1,
            max_value=30,
            value=int(li.get("max_applications_per_run", 5)),
        )
        delay = st.number_input(
            "Seconds between applications",
            min_value=3,
            max_value=60,
            value=int(li.get("delay_seconds_between_apps", 8)),
        )
    with c3:
        dry_run = st.checkbox("Dry run (do not submit)", value=True)
        easy_only = st.checkbox("Easy Apply only", value=bool(li.get("only_easy_apply", True)))
        use_ai_li = st.checkbox("Use OpenAI while filling", value=True, key="ai_li")
        i_understand = st.checkbox(
            "I reviewed my details and will answer any paused/unknown fields",
            value=False,
        )

    cv_for_apply = st.text_input("CV to upload", cfg.get("cv_path", ""), key="cv_apply")
    missing = missing_application_details(
        profile,
        cv_for_apply,
        keywords=keywords,
        location=location,
        mode="linkedin",
    )
    st.subheader("Before applying")
    if missing:
        st.warning("Complete these details before browser automation can start:")
        for item in missing:
            st.write(f"- {item}")
    else:
        st.success("Required profile, CV, search, and work-eligibility details are present.")
    with st.expander("What will be requested from you", expanded=bool(missing)):
        for question in application_intake_questions(
            profile,
            cv_for_apply,
            keywords=keywords,
            location=location,
            mode="linkedin",
        ):
            st.write(f"- {question}")
    st.caption(
        "Easy Apply can advance and submit only when explicitly enabled. External ATS pages "
        "wait for login, fill safe known fields, and stop before final Submit."
    )
    log_box = st.empty()
    logs: list[str] = []

    def _logger(msg: str) -> None:
        logs.append(msg)
        log_box.code("\n".join(logs[-40:]), language="text")

    if st.button(
        "Start LinkedIn apply run",
        type="primary",
        disabled=not i_understand or bool(missing),
    ):
        if not cv_for_apply or not Path(cv_for_apply).is_file():
            st.error("CV file not found.")
        else:
            with st.spinner("Running browser automation — watch the Chromium window..."):
                summary = run_linkedin_auto_apply(
                    keywords=keywords,
                    location=location,
                    cv_path=cv_for_apply,
                    browser_data_dir=cfg["browser_data_dir"],
                    max_applications=int(max_apps),
                    delay_seconds=float(delay),
                    easy_apply_only=easy_only,
                    headless=False,
                    dry_run=dry_run,
                    use_ai=use_ai_li,
                    defaults=d,
                    profile=profile,
                    output_dir=cfg["output_dir"],
                    log=_logger,
                )
            st.success(f"Finished: {len(summary.results)} job(s) processed")
            st.json(summary.to_dict())

# ---------- Direct Apply ----------
with tabs[4]:
    profile = load_profile()
    st.subheader("Direct application (law, graduate scheme, or other role)")
    st.info(
        "Complete the intake first. The helper can fill verified facts and advance "
        "through safe steps, but it leaves final submission, declarations, tests, "
        "CAPTCHAs, and consent to you."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        application_type = st.selectbox(
            "Application type",
            options=["law", "graduate", "other"],
            format_func=lambda value: {
                "law": "Law firm programme",
                "graduate": "Graduate scheme",
                "other": "Other direct role",
            }[value],
        )
        direct_company = st.text_input("Employer", key="direct_company")
    with c2:
        direct_role = st.text_input("Programme / role", key="direct_role")
        direct_office = st.text_input("Office / location", key="direct_office")
    with c3:
        direct_deadline = st.text_input("Deadline", key="direct_deadline")
        direct_use_ai = st.checkbox(
            "Use OpenAI only if the employer permits it",
            value=False,
            key="direct_use_ai",
        )

    direct_url = st.text_input("Official application URL", key="direct_url")
    direct_cv = st.text_input("Approved CV PDF to upload", cfg.get("cv_path", ""), key="direct_cv")
    direct_job_context = st.text_area(
        "Job description and eligibility rules",
        height=140,
        key="direct_job_context",
    )
    direct_portal_questions = st.text_area(
        "Exact portal questions and word / character limits",
        height=180,
        key="direct_portal_questions",
        placeholder="Paste every question exactly as displayed before starting the fill.",
    )

    direct_missing = missing_application_details(
        profile,
        direct_cv,
        mode="external",
        target_url=direct_url,
    )
    if not direct_company.strip():
        direct_missing.append("Please provide the employer's exact name.")
    if not direct_role.strip():
        direct_missing.append("Please provide the programme or role title.")

    st.subheader("Questions to answer before automation")
    for question in direct_application_intake_questions(
        profile,
        direct_cv,
        application_type=application_type,
        target_url=direct_url,
    ):
        st.write(f"- {question}")

    if direct_missing:
        st.warning("The direct-apply browser cannot start yet:")
        for item in direct_missing:
            st.write(f"- {item}")

    questions_complete = st.checkbox(
        "I copied every portal question and limit, or confirmed that none are shown yet",
        value=False,
        key="direct_questions_complete",
    )
    facts_complete = st.checkbox(
        "I reviewed the profile, eligibility answers, education, dates, and approved CV",
        value=False,
        key="direct_facts_complete",
    )
    policy_complete = st.checkbox(
        "I checked the employer's AI policy and selected the AI option accordingly",
        value=False,
        key="direct_policy_complete",
    )
    manual_submit = st.checkbox(
        "I understand that I must review and submit the application myself",
        value=False,
        key="direct_manual_submit",
    )

    direct_log_box = st.empty()
    direct_logs: list[str] = []

    def _direct_logger(msg: str) -> None:
        direct_logs.append(msg)
        direct_log_box.code("\n".join(direct_logs[-40:]), language="text")

    direct_ready = (
        not direct_missing
        and questions_complete
        and facts_complete
        and policy_complete
        and manual_submit
    )
    if st.button(
        "Open and fill direct application",
        type="primary",
        disabled=not direct_ready,
    ):
        context_bits = [
            f"Application type: {application_type}",
            f"Employer: {direct_company}",
            f"Programme or role: {direct_role}",
            f"Office: {direct_office}",
            f"Deadline: {direct_deadline}",
            direct_job_context,
            "Portal questions and limits:",
            direct_portal_questions,
        ]
        try:
            context = get_context(cfg["browser_data_dir"])
            page = context.new_page()
            page.goto(direct_url, wait_until="domcontentloaded", timeout=90000)
            detail = fill_generic_application_form(
                page,
                profile=profile,
                defaults=dict(cfg.get("defaults", {})),
                cv_path=direct_cv,
                job_context="\n".join(part for part in context_bits if part),
                company=direct_company,
                role=direct_role,
                use_ai=direct_use_ai,
                dry_run=False,
                log=_direct_logger,
            )
            st.success(detail)
        except Exception as exc:
            st.error(f"Direct application could not start: {exc}")

# ---------- Logs ----------
with tabs[5]:
    out = Path(cfg["output_dir"])
    files = sorted(out.glob("linkedin_run_*.json"), reverse=True)
    if not files:
        st.write("No LinkedIn run logs yet.")
    else:
        choice = st.selectbox("Log file", [f.name for f in files])
        data = json.loads((out / choice).read_text(encoding="utf-8"))
        st.json(data)
