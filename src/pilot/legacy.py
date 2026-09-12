"""Old executable application scripts forward only an explicitly approved plan."""

import argparse
import json
from pathlib import Path


def main():
    from .runtime import client

    parser = argparse.ArgumentParser(
        description="Use the shared foreground ApplyPilot runtime; legacy browser workers are retired."
    )
    parser.add_argument(
        "--runtime-plan", required=True, help="Explicit reviewed RunPlan JSON"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            client(
                {"op": "start", "plan": json.loads(Path(args.runtime_plan).read_text())}
            ),
            indent=2,
        )
    )
