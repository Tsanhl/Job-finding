# ApplyPilot

The current local dashboard combines JobSignal's design and discovery adapters
with ApplyPilot's private profile, autofill and application history. It exposes
Discover jobs, Search portfolios, My Information, Saved opportunities, Autofill,
Applied History, Discovery status and Settings. Gmail tracking is opt-in, once
every 24 hours with Sync now. Open it with `Open ApplyPilot.command` or
`.venv-upgrade/bin/python -m src.pilot.desktop`.

Read the [local workspace guide](docs/LOCAL_WORKSPACE.md) for setup, Codex tools,
daily Gmail synchronization and data recovery. Autofill completes
supported non-final sections and stops at final review by default. Each target
can separately receive explicit submission authority. Internal capability and
qualification checks remain enforced by one foreground local runtime. Start with the [upgrade
guide](docs/UPGRADE.md), [test report](docs/TEST_REPORT.md), [capability
matrix](docs/ADAPTER_CAPABILITIES.md) and [schema](docs/SCHEMA.md). Use
`.venv-upgrade`; the original environment and private records are preserved.

The material below describes the legacy interface and retained helper commands. Application execution now routes through `cli.py runtime`; legacy modes and assessment controls are not exposed by the current dashboard.

Local job-application helper for a user-supplied CV and profile:

1. Tailored cover letters, using a template or OpenAI when enabled.
2. Screening-question answers from the local profile/CV.
3. Local-preview and assisted-review LinkedIn/external ATS workflows through a logged-in Chromium session.
4. A comprehensive direct-application intake for law programmes, graduate schemes, and other external roles, including structured university and A-level/IB/HKDSE/GCSE/other qualification results. The general question set is always available, while a missing academic fact blocks progress only when the pasted application instructions or portal questions request it.
5. A local batch coordinator that prepares up to ten validated direct applications concurrently in separate tabs of the same browser context unless a lower worker limit is requested. Tabs are not isolated employer sessions. Final submission remains manual.
6. A read-only Assessment Monitor + Solver with a volatile answer panel, manual clicking, Start/Stop controls, and bounded worker lifetime.
7. A local search-and-prepare mode that screens vacancies, tracks distinct outcomes, and pauses applications at review. Automated final submission is disabled. AI drafting is off until the employer policy is explicitly marked allowed; an on-page prohibition always wins and no reference answer is inserted.

The separate [`law_firm_application_agent`](law_firm_application_agent/) folder contains a privacy-safe UK law-firm prompt template and a non-destructive helper for creating a structured workspace for each firm and programme. Personalized prompts, candidate records, and application workspaces stay ignored and local.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env
cp config.example.yaml config.yaml
```

The current dashboard provides first-run reusable profile setup. Existing users
can instead preview and import ignored local records. Personal profile data,
configuration, CVs, browser sessions, and run output are excluded from Git.

The tracked `data/preset_questions.json` catalogue contains more than 60 blank,
categorized definitions for employer relationships, work rights, availability,
education, integrity, financial screening, conflicts, references, compensation,
adjustments, voluntary disclosures, social mobility and declarations. Each
definition states whether it is required during setup, optionally reusable, tied
to a country or employer, or asked only when encountered. It must never contain
candidate answers. Reusable answers belong in ignored local profile versions;
salary, dated work-rights wording, declarations, signatures and employer consent
remain application-specific.

## Run the optional local UI

```bash
streamlit run app.py
```

Streamlit is only a local browser interface and is bound to `127.0.0.1` by the checked-in configuration; this project is not deployed as a public website. The CLI below can be used instead.

Autofill and LinkedIn Easy Apply are blocked until reusable setup and an approved
CV are ready. Find Jobs and Open Job Links need no candidate profile. Unknown
required fields are grouped; declarations, CAPTCHA, passwords, passkeys and
email verification remain with the user.

## Legacy helper outcomes

- `local-preview` may navigate to and inspect a supplied page, but it does not fill fields, upload documents, advance controls, create accounts, or submit.
- `assisted-review` may enter confirmed profile values, upload the explicitly approved CV, and use adapter-declared non-final navigation. It stops at final, ambiguous, unsupported, or unresolved controls.
- `guarded_auto_submit` is represented internally for compatibility but is unavailable in this release. Legacy submission requests fail before browser access.

Results use canonical statuses such as `preview-ready`, `review-ready`, `needs-information`, `policy-blocked`, `unsupported`, `submission-disabled`, and `submission-unconfirmed`. A temporary `legacy_status` is included where the old status has an unambiguous equivalent. Only `submitted-confirmed` means a completed submission; current automated workflows never produce it from a click, closed modal, URL change, or generic success text.

## CLI examples

```bash
.venv-upgrade/bin/python cli.py runtime find-jobs --area "technology" --location London --count 10
.venv-upgrade/bin/python cli.py runtime open-job-links --count 2 https://employer.example/job/1 https://employer.example/job/2
.venv-upgrade/bin/python cli.py runtime linkedin-easy-apply --keywords "graduate software engineer" --location London --count 3 --profile PROFILE_VERSION --document DOCUMENT_ID
.venv-upgrade/bin/python cli.py runtime run --plan reviewed-plan.json
```

The first three commands correspond directly to the dashboard functions.
LinkedIn Easy Apply always stops at final review. An Autofill plan uses
`function: AUTOFILL` and sets `final_action` to `REVIEW` or `SUBMIT` on each
target; omission defaults to `REVIEW`. See [the local upgrade guide](docs/UPGRADE.md).

Find Jobs performs a bounded crawl of enabled sources and their permitted
same-site vacancy links. Results are filtered by the requested job area and shown
as a portal checklist with employer, role, dates, location, type, pay,
requirements and a clickable link. Unknown live status remains **CHECK PORTAL**.

The following commands are retained legacy helpers:

```bash
python cli.py cover-letter --company "Example LLP" --role "Legal Intern"
python cli.py answer --questions-file questions.txt
python cli.py preflight --keywords "legal intern" --location "London"
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm --mode local-preview
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm --mode assisted-review --ai-policy allowed
python cli.py external --url "https://example.com/apply" --company "Example" --role "Intern" --application-type graduate
python cli.py external --url "https://example.com/apply" --company "Example" --role "Intern" --application-type graduate --confirm --mode assisted-review --ai-policy unknown
cp applications.example.json applications.local.json
python cli.py batch --applications-file applications.local.json
python cli.py batch --applications-file applications.local.json --confirm
python cli.py batch --applications-file applications.local.json --workers 3 --confirm --mode assisted-review
cp full_automation.example.json full_automation.local.json
python cli.py full-auto --requirements-file full_automation.local.json
python cli.py full-auto --requirements-file full_automation.local.json --confirm --mode assisted-review --ai-policy unknown
```

The first `external` command prints the full intake questionnaire and does not open a browser. After answering and reviewing it, `--confirm` allows the selected mode. The legacy `--submit` flag and `submission_policy: auto-submit` remain parseable but return `submission-disabled` without opening the browser.

For a batch, complete each item in the ignored `applications.local.json`, including its exact portal questions, `ai_policy`, `allow_ai` choice, and confirmation flags. AI runs only when `ai_policy` is `allowed` and `allow_ai` is true. Legacy files without `ai_policy` enable AI only when both `allow_ai` and `ai_policy_confirmed` are true; otherwise policy is unknown and deterministic filling continues without AI.

For `full-auto`, employer passwords are never requested. Create or sign into the
account and complete verification in the managed browser, then resume. CAPTCHA,
MFA, recovery, ambiguous email, declarations, and unverified answers pause for
the user. Unreadable ledger history blocks duplicate-sensitive submission, and
an `in_progress` record remains reconciliation-required instead of expiring into
an automatic retry.

Before every form step, ApplyPilot creates an in-memory manifest of the visible fields and records sanitized evidence without serializing field values. Identity fields are section-aware; employer names are not applicant names. Textareas are classified as cover letter, motivation, competency, responsibilities, additional information, or unknown. Answers over a detected word/character limit are left unresolved rather than truncated. Non-native dropdowns and unapproved cover-letter/transcript upload slots pause as unsupported or needs-information.

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
- Enable the mandatory local pre-push gate once with `.venv-upgrade/bin/python scripts/install_hooks.py`; run it directly with `make privacy`.
