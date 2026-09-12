#!/usr/bin/env python3
"""Enable the repository-owned pre-push privacy gate for this checkout."""

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=root, check=True)
print("Git pre-push privacy gate enabled for this checkout.")
