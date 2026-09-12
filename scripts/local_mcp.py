#!/usr/bin/env python3
"""Stable executable entry point independent of Codex's working directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pilot.mcp_server import main

if __name__ == "__main__":
    main()
