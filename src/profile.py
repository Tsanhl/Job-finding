from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .config import ROOT

PROFILE_PATH = ROOT / "data" / "profile.local.json"
EXAMPLE_PROFILE_PATH = ROOT / "data" / "profile.example.json"


def _configured_profile_path() -> Path:
    configured = os.getenv("APPLYPILOT_PROFILE", "").strip()
    if not configured:
        return PROFILE_PATH
    configured_path = Path(configured).expanduser()
    return configured_path if configured_path.is_absolute() else ROOT / configured_path


def load_profile(path: Path | None = None) -> dict[str, Any]:
    p = path or _configured_profile_path()
    if not p.exists():
        p = EXAMPLE_PROFILE_PATH
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_profile(profile: dict[str, Any], path: Path | None = None) -> None:
    p = path or _configured_profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")


def extract_cv_text(cv_path: str | Path) -> str:
    reader = PdfReader(str(cv_path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def profile_as_prompt_block(profile: dict[str, Any]) -> str:
    return (
        f"Name: {profile.get('full_name')}\n"
        f"Email: {profile.get('email')}\n"
        f"Phone: {profile.get('phone')}\n"
        f"Location: {profile.get('location')}\n"
        f"Summary: {profile.get('summary')}\n"
        f"Education: {json.dumps(profile.get('education', {}), ensure_ascii=False)}\n"
        f"School qualifications: {json.dumps(profile.get('school_qualifications', {}), ensure_ascii=False)}\n"
        f"Skills: {', '.join(profile.get('skills', []))}\n"
        f"Experience:\n- " + "\n- ".join(profile.get("experience_highlights", []))
    )
