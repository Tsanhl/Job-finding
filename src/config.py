from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or ROOT / "config.yaml"
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cv_value = os.getenv("APPLYPILOT_CV_PATH", data.get("cv_path", ""))
    if str(cv_value).strip():
        cv_path = Path(str(cv_value)).expanduser()
        if not cv_path.is_absolute():
            cv_path = ROOT / cv_path
        data["cv_path"] = str(cv_path.resolve())
    else:
        data["cv_path"] = ""
    profile_value = os.getenv("APPLYPILOT_PROFILE", data.get("profile_path", "data/profile.local.json"))
    profile_path = Path(str(profile_value)).expanduser()
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    data["profile_path"] = str(profile_path.resolve())
    data["browser_data_dir"] = str((ROOT / data.get("browser_data_dir", ".browser_data")).resolve())
    data["output_dir"] = str((ROOT / data.get("output_dir", "output")).resolve())
    return data


def ensure_dirs(cfg: dict[str, Any]) -> None:
    Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["browser_data_dir"]).mkdir(parents=True, exist_ok=True)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
