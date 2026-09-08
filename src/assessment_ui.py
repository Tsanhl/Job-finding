"""Session-only UI. No chat history, export, cache, or persistence hooks."""
import streamlit as st

from .assessment_monitor import create_monitor


def _stop_and_clear():
    monitor = st.session_state.get("assessment_worker")
    if monitor:
        monitor.stop()
    for key in ("assessment_url", "assessment_selector", "assessment_frame"):
        st.session_state[key] = ""
    st.session_state.assessment_ai_consent = False


@st.fragment(run_every=2)
def render_assessment_monitor():
    st.subheader("Assessment Monitor + Solver")
    st.write("Watch one question region and show the latest suggested answer here while you click.")
    st.caption(
        "Captures and answers stay in memory only; Stop clears the answer. "
        "AI sends the selected region to OpenAI with response storage disabled. "
        "Provider retention rules still apply. No content is sent until Start."
    )
    with st.expander("Connection and question region"):
        st.write(
            "Use the app's Chromium session, already running on local port 9333. "
            "This feature cannot attach through Codex's Chrome extension. "
            "Choose a CSS selector matching one region containing the full question, "
            "diagrams and choices, excluding clocks, animations and personal details. "
            "A changing clock in the region prevents stable answers."
        )
        st.text_input("Exact open tab URL", key="assessment_url")
        st.text_input("Question region CSS selector", key="assessment_selector",
                      placeholder="Example: #question-panel")
        st.text_input("Iframe CSS selector (if needed)", key="assessment_frame")
    consent = st.checkbox(
        "Send this selected question region to OpenAI for answers",
        key="assessment_ai_consent",
    )
    monitor = st.session_state.get("assessment_worker")
    if monitor and not consent:
        monitor.stop()
    status, answer, requests, running = monitor.view() if monitor else ("Not started", None, 0, False)
    start, stop = st.columns(2)
    if start.button("Start monitoring", disabled=running or not consent):
        if monitor:
            monitor.stop()
        try:
            monitor = create_monitor(
                st.session_state.assessment_url.strip(),
                st.session_state.assessment_selector.strip(),
                st.session_state.assessment_frame.strip(),
            )
            st.session_state.assessment_worker = monitor
            monitor.start()
            status, answer, requests, running = monitor.view()
        except ValueError as exc:
            st.error(str(exc))  # Only static setup validation messages.
    stop.button("Stop and clear", disabled=monitor is None, on_click=_stop_and_clear)
    st.write(status)
    st.caption(f"AI requests: {requests}/60. Stops after completion, 10 minutes idle, or 30 minutes total.")
    if answer:
        with st.chat_message("assistant"):
            # Plain text prevents model-generated links or images from loading remote content.
            st.text(answer.answer or answer.status.capitalize())
            st.text(answer.explanation)
    st.caption("Answers appear in this app panel, not automatically in a Codex conversation.")


def assessment_panel():
    render_assessment_monitor()
