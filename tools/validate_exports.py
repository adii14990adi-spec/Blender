#!/usr/bin/env python3
"""Validate exported FBX/GLB artifacts without launching Blender.

    python tools/validate_exports.py outputs/male/jacket_urban_01/*.glb --require-skin
    python tools/validate_exports.py --outfit jacket_urban_01 --khronos

Exits non-zero when any structural check fails, so CI can gate the deploy step
on real file contents rather than on file existence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "blender"))

from bpy_scripts.utils.fbx_inspect import inspect_fbx  # noqa: E402
from bpy_scripts.utils.glb_inspect import inspect_glb  # noqa: E402
from bpy_scripts.utils.paths import fbx_export_path, glb_export_path  # noqa: E402


def _khronos(glb: str) -> dict:
    node = subprocess.run(["node", "--version"], capture_output=True, check=False).returncode == 0
    script = os.path.join(REPO_ROOT, "tools", "gltf_validate.js")
    if not node or not os.path.exists(script):
        return {"status": "skipped", "reason": "node unavailable"}
    completed = subprocess.run(["node", script, glb], capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        payload = {"errors": [completed.stderr[-1000:] or "validator failed"]}
    payload["status"] = "pass" if completed.returncode == 0 else "fail"
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="GLB/FBX files to validate")
    parser.add_argument("--outfit", help="validate outputs/<variant>/<outfit>/ artifacts instead")
    parser.add_argument(
        "--variant", default="male", help="body variant used to resolve --outfit paths"
    )
    parser.add_argument("--require-skin", action="store_true", default=True)
    parser.add_argument("--allow-unskinned", dest="require_skin", action="store_false")
    parser.add_argument("--max-influences", type=int, default=4)
    parser.add_argument(
        "--khronos", action="store_true", help="also run the Khronos glTF Validator"
    )
    parser.add_argument("--json", help="write the full report to this path")
    args = parser.parse_args(argv)

    files = list(args.files)
    if args.outfit:
        files += [
            glb_export_path(args.outfit, args.variant),
            fbx_export_path(args.outfit, args.variant),
        ]
    if not files:
        parser.error("provide GLB/FBX paths or --outfit <name>")

    report: dict = {"status": "pass", "artifacts": {}}
    failures = 0
    for path in files:
        if path.endswith(".glb"):
            result = inspect_glb(
                path,
                expect_skin=args.require_skin,
                expect_textures=True,
                max_influences=args.max_influences,
            )
            if args.khronos:
                result["khronos"] = _khronos(path)
                if result["khronos"].get("status") == "fail":
                    result["ok"] = False
                    result["errors"] += [
                        f"Khronos: {issue}" for issue in result["khronos"].get("errors", [])
                    ]
        elif path.endswith(".fbx"):
            result = inspect_fbx(path, expect_skin=args.require_skin)
        else:
            result = {"ok": False, "errors": [f"unsupported file type: {path}"], "stats": {}}
        report["artifacts"][path] = result
        if not result.get("ok"):
            failures += 1

    report["status"] = "pass" if failures == 0 else "fail"
    print(json.dumps(report, indent=2, default=str))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    if failures:
        print(f"VALIDATION FAILED for {failures} artifact(s)", file=sys.stderr)
        return 1
    print("VALIDATION PASSED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
