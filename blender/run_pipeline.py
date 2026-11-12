"""Headless Blender entry point.

Invoked by GitHub Actions and Arena Agent:

    blender -b --python blender/run_pipeline.py -- \
        --stage validate \
        --blend assets/base_body/BASE_BODY.blend \
        --region UPPER_BODY \
        --out /tmp/validation.json

Each stage saves its own .blend checkpoint under outputs/<outfit>/ so the
pipeline is resumable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bpy_scripts import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Modular clothing pipeline runner")
    parser.add_argument("--stage", required=True, help="Pipeline stage to run")
    parser.add_argument("--region", default="UPPER_BODY", help="Body region")
    parser.add_argument("--blend", required=True, help="Path to working .blend")
    parser.add_argument("--params", default="{}", help="JSON object of stage params")
    parser.add_argument("--out", required=True, help="Where to write result JSON")
    args = parser.parse_args()

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        print(f"Invalid --params JSON: {exc}", file=sys.stderr)
        sys.exit(2)

    result = pipeline.run_stage(args.stage, args.blend, args.region, params)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)

    sys.exit(0 if result.get("status") == "pass" else 1)


if __name__ == "__main__":
    main()
