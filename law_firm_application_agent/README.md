# UK Law Firm Application Agent

This folder keeps a privacy-safe master-prompt template and a small local helper for creating one organised workspace per firm and programme. Copy `MASTER_SYSTEM_PROMPT.example.md` to the ignored `MASTER_SYSTEM_PROMPT.md` and add candidate facts locally. The canonical CV remains unchanged; each application gets its own requirement map, working draft, review checklist and approved final copy.

## Canonical CV

Register the approved baseline locally in the ignored `candidate.local.json`. Start from this untracked structure:

```json
{
  "canonical_cv": {
    "path": "/absolute/path/to/approved-cv.pdf",
    "sha256": "sha256-of-approved-file",
    "registered_on": "YYYY-MM-DD",
    "status": "approved baseline"
  }
}
```

The local record also stores the CV's SHA-256 fingerprint so a future application can detect that the source has changed. It contains personal information and must not be committed, uploaded or shared without express approval.

Before tailoring a TC CV, the agent must:

1. Read the current CV and the firm's official application instructions.
2. Record the programme's priorities in `cv/requirements.md`.
3. Map each priority to confirmed evidence in `cv/tailoring_map.md`.
4. Create a separate draft without modifying the canonical CV.
5. Truth-audit the draft and visually inspect the final PDF.
6. Put only the candidate-approved version in `cv/final/`.

## Create an application workspace

From the project root, run:

```bash
python3 law_firm_application_agent/workspace.py \
  --firm "Example LLP" \
  --programme "2027 Training Contract" \
  --office "London" \
  --deadline "2026-12-01" \
  --url "https://example.com/graduate-careers"
```

The helper creates a folder under `law_firm_application_agent/applications/` for intake, research, evidence, application-answer drafts, reviews, final copy, supporting documents and the firm-specific CV workflow. It automatically records a reference to the canonical CV but does not copy it. Running the same command again reuses the workspace and does not overwrite existing files.

Application workspaces are ignored by Git because they may contain personal or sensitive material. Only the blank `MASTER_SYSTEM_PROMPT.example.md` is public; the personalized `MASTER_SYSTEM_PROMPT.md` remains local.
