# ApplyPilot

Local job-application helper for a user-supplied CV and profile:

1. Tailored cover letters, using a template or OpenAI when enabled.
2. Screening-question answers from the local profile/CV.
3. Assisted LinkedIn Easy Apply and external ATS form filling through a logged-in Chromium session.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Copy `data/profile.example.json` to the ignored `data/profile.local.json`, fill in the candidate's facts, and set `cv_path` in `config.yaml` or `APPLYPILOT_CV_PATH` in `.env`. Personal profile data, CVs, browser sessions, and run output are excluded from Git.

## Run the UI

```bash
streamlit run app.py
```

The LinkedIn tab shows the pre-application checklist first. Browser automation is blocked until the profile, CV, search target, work authorisation, sponsorship, and availability details are present. Unknown required fields, CAPTCHA, consent, and login walls pause the flow for the user.

## CLI examples

```bash
python cli.py cover-letter --company "Example LLP" --role "Legal Intern"
python cli.py answer --questions-file questions.txt
python cli.py preflight --keywords "legal intern" --location "London"
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm
python cli.py linkedin --keywords "legal intern" --location "London" --max 3 --confirm --submit
python cli.py external --url "https://example.com/apply" --company "Example" --role "Intern" --confirm
```

`--submit` is opt-in and applies only to Easy Apply. External ATS flows open the link, wait for manual login when needed, fill known fields, advance through safe steps, and leave the final Submit action for the user.

## Safety

- Review every answer, especially work authorisation, sponsorship, salary, demographic, and consent questions.
- Unknown questions are left blank and reported instead of being guessed.
- Keep application volume low and follow the terms of the sites you use.
- Never commit `.env`, `data/profile.local.json`, CVs, browser data, or generated output.
