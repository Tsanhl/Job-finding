# ApplyPilot

Local job-application helper for a user-supplied CV and profile:

1. Tailored cover letters, using a template or OpenAI when enabled.
2. Screening-question answers from the local profile/CV.
3. Assisted LinkedIn Easy Apply and external ATS form filling through a logged-in Chromium session.
4. A comprehensive direct-application intake for law programmes, graduate schemes, and other external roles, including structured university and A-level/IB/HKDSE/GCSE/other qualification results. The general question set is always available, while a missing academic fact blocks progress only when the pasted application instructions or portal questions request it.
5. A local batch coordinator that automatically prepares up to ten validated direct applications concurrently, with one isolated browser worker per application unless a lower worker limit is requested. Final submission remains manual for every application.
6. A read-only Assessment Monitor + Solver with a volatile answer panel, manual clicking, Start/Stop controls, and bounded worker lifetime.
7. A local full-automation mode that searches beyond the requested count until it finds enough viable entry-level roles, excludes previously completed employers, screens explicit unpaid/training-only listings and excessive experience requirements, tracks each application in a persistent ledger, uses dedicated ATS profiles, creates employer accounts with per-portal credentials when required, verifies recent trusted Gmail links, validates final field values and uploads, and either pauses at final review or submits according to the user's selected policy. AI-restricted applications still receive model reference drafts in their exact fields, but are always locked to review-only for candidate rewriting.

The separate [`law_firm_application_agent`](law_firm_application_agent/) folder contains a privacy-safe UK law-firm prompt template and a non-destructive helper for creating a structured workspace for each firm and programme. Personalized prompts, candidate records, and application workspaces stay ignored and local.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
cp config.example.yaml config.yaml
```

Copy `data/profile.example.json` to the ignored `data/profile.local.json`, fill in the candidate's facts, and set `cv_path` in the ignored local `config.yaml` or `APPLYPILOT_CV_PATH` in `.env`. Personal profile data, configuration, CVs, browser sessions, and run output are excluded from Git.

## Run the optional local UI

```bash
streamlit run app.py
```

Streamlit is only a local browser interface and is bound to `127.0.0.1` by the checked-in configuration; this project is not deployed as a public website. The CLI below can be used instead.

The LinkedIn tab shows the pre-application checklist first. The Direct Apply tab asks a full law/graduate-scheme intake before it can open an external application. Browser automation is blocked until required profile, CV, work-authorisation, sponsorship, and target details are present. Unknown required fields, declarations, tests, CAPTCHA, consent, and login walls remain with the user.

## CLI examples

```bash
python cli.py cover-letter --company "Example LLP" --role "Legal Intern"
python cli.py answer --questions-file questions.txt
python cli.py preflight --keywords "legal intern" --location "London"
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm --submit
python cli.py external --url "https://example.com/apply" --company "Example" --role "Intern" --application-type graduate
python cli.py external --url "https://example.com/apply" --company "Example" --role "Intern" --application-type graduate --confirm
cp applications.example.json applications.local.json
python cli.py batch --applications-file applications.local.json
python cli.py batch --applications-file applications.local.json --confirm
python cli.py batch --applications-file applications.local.json --workers 3 --confirm
cp full_automation.example.json full_automation.local.json
python cli.py full-auto --requirements-file full_automation.local.json
python cli.py full-auto --requirements-file full_automation.local.json --confirm
```

The first `external` command prints the full intake questionnaire and does not open a browser. After answering and reviewing it, `--confirm` allows form filling. `--submit` is opt-in and applies only to Easy Apply. External ATS flows leave the final Submit action for the user.

For a batch, complete each item in the ignored `applications.local.json`, including its exact portal questions, `allow_ai` choice, and the three confirmation flags. The first `batch` command validates every application without opening browser tabs. With `--confirm`, the coordinator automatically opens one local worker per application, up to ten at the same time. Use `--workers` only when you want a lower concurrency limit. AI is used only when both the batch option and that application's `allow_ai` value are enabled. One invalid application prevents the batch from starting, so no worker bypasses intake.

For `full-auto`, omit `--requirements-file` to answer the role, location, paid-role filter, maximum required experience, completed/excluded employers, application-count, and submission-policy prompts interactively. With `--confirm`, the command asks for the application email address. Its recommended credential mode generates a different employer-portal password for every company and stores it in macOS Keychain under an `ApplyPilot:` service name; the fallback accepts one hidden shared portal password and does not save it. It never asks for or stores the Gmail account password, and no portal password is written to configuration, results or logs. Gmail verification uses the Gmail account already signed in to the shared Chromium session and requires a recent message, a non-free sender, company evidence and a trusted employer/ATS verification host. Google login challenges, CAPTCHA, two-factor authentication, ambiguous links, unconfirmed verification and unverified required answers pause for the user. The application ledger under `output/application_ledger.json` prevents review-ready, submitted, manually completed and rejected vacancies from being reopened. The `review` policy fills applications to their final review page, while `auto-submit` permits explicit final Submit controls only after field, upload, declaration and AI-policy guards pass. AI-restricted applications are populated with model reference drafts but are always locked for the candidate to rewrite and submit. Required terms/privacy checkboxes are accepted only when `accept_required_terms` is enabled; marketing, declarations and job-alert opt-ins remain untouched.

## Safety

### Assessment Monitor + Solver

Open the new app tab, enter the exact URL of one tab in the app's existing Chromium session on local port 9333, and provide a CSS selector for the question region (plus an iframe selector if applicable). Include the question, choices and diagrams, but exclude timers, animations and personal details. This code uses the app's CDP connection; it does not attach through the Codex Chrome extension or start/reconfigure your personal Chrome browser.

Configure `OPENAI_API_KEY` and optionally `ASSESSMENT_MODEL` (otherwise `OPENAI_MODEL` is used). Enable sending the selected region to OpenAI, then press Start. The worker checks every five seconds and posts the latest answer in the app's chat-style panel. It does not post into Codex chat or click/submit responses. Exact URL changes, capture/API failures, a completion screen, 60 requests, ten minutes without changes, or thirty minutes total stop the worker. Closing/disconnecting the UI expires its lease after thirty seconds, subject to an in-flight request finishing (30-second API timeout).

Captures, fingerprints, URL and the latest answer are held only in process memory. There is no question history, screenshot file, export, application log, or saved response. Stop clears the displayed answer immediately and prevents in-flight answers from reappearing; pending requests cannot be recalled. Memory clearing is not secure erasure. The optional AI request uses `store=False`, which does not itself guarantee provider-side zero retention: see [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data). Existing browser history, provider controls and Codex conversation history are outside this feature's storage controls. Development tests use synthetic data only.

### Application review

- Review every answer, especially work authorisation, sponsorship, salary, demographic, and consent questions.
- Unknown questions are left blank and reported instead of being guessed.
- Keep application volume low and follow the terms of the sites you use.
- Never commit `.env`, personalized prompts, candidate data, CVs, application drafts, browser data, or generated output. The repository ignore rules exclude these by default.
