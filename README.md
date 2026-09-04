# ApplyPilot

Local job-application helper for a user-supplied CV and profile:

1. Tailored cover letters, using a template or OpenAI when enabled.
2. Screening-question answers from the local profile/CV.
3. Assisted LinkedIn Easy Apply and external ATS form filling through a logged-in Chromium session.
4. A mandatory direct-application intake for law programmes, graduate schemes, and other external roles.

The separate [`law_firm_application_agent`](law_firm_application_agent/) folder contains a privacy-safe UK law-firm prompt template and a non-destructive helper for creating a structured workspace for each firm and programme. Personalized prompts, candidate records, and application workspaces stay ignored and local.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Copy `data/profile.example.json` to the ignored `data/profile.local.json`, fill in the candidate's facts, and set `cv_path` in `config.yaml` or `APPLYPILOT_CV_PATH` in `.env`. Personal profile data, CVs, browser sessions, and run output are excluded from Git.

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
```

The first `external` command prints the full intake questionnaire and does not open a browser. After answering and reviewing it, `--confirm` allows form filling. `--submit` is opt-in and applies only to Easy Apply. External ATS flows leave the final Submit action for the user.

## Safety

- Review every answer, especially work authorisation, sponsorship, salary, demographic, and consent questions.
- Unknown questions are left blank and reported instead of being guessed.
- Keep application volume low and follow the terms of the sites you use.
- Never commit `.env`, personalized prompts, candidate data, CVs, application drafts, browser data, or generated output. The repository ignore rules exclude these by default.
