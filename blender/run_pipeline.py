"""Headless Blender entry point.

Invoked by GitHub Actions:

    blender -b --python-exit-code 1 --python blender/run_pipeline.py -- \
        --stage validate \
        --blend assets/base_body/BASE_BODY.blend \
        --region UPPER_BODY \
        --out /tmp/validation.json

Each stage saves its own .blend checkpoint under outputs/<outfit>/ so the
pipeline is resumable.

Exit codes
----------
0   every requested stage passed
1   a stage failed, a module failed to import, or an input was unusable

The failure detail is always written to ``--out`` as JSON before exiting, so CI
can print it. Pass ``--allow-fallback-glb`` if a placeholder GLB should be
written when Blender fails (flagged with ``glb_is_fallback: true``); it is off
by default so a broken build never ships a fake asset.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict

# --- import bootstrap --------------------------------------------------------
# Blender runs this file as a top-level script (``__main__``), so there is no
# parent package for relative imports to resolve against. Put ``blender/`` on
# ``sys.path`` and use absolute ``bpy_scripts.*`` imports everywhere. Never add
# the repo root here too: importing the same module as both ``bpy_scripts.x``
# and ``blender.bpy_scripts.x`` is what triggers
# "attempted relative import beyond top-level package".
BLENDER_DIR = Path(__file__).resolve().parent
REPO_ROOT = BLENDER_DIR.parent
if str(BLENDER_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_DIR))

STAGES = ("validate", "all", "shell", "shrinkwrap", "weights", "uv", "material", "export")
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _minimal_glb(out_path: str) -> str:
    """Write a minimal valid GLB (empty cube) using pure Python, no bpy."""
    # Minimal GLB: a single cube mesh with 8 vertices, 12 triangles.
    # Header + JSON chunk + BIN chunk.
    json_bytes = json.dumps(
        {
            "asset": {"version": "2.0", "generator": "Modular Clothing Pipeline"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "meshes": [
                {"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}
            ],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 8,
                    "type": "VEC3",
                    "max": [1, 1, 1],
                    "min": [-1, -1, -1],
                },
                {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
            ],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": 96},
                {"buffer": 0, "byteOffset": 96, "byteLength": 72},
            ],
            "buffers": [{"byteLength": 168}],
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1]}}],
        },
        separators=(",", ":"),
    ).encode("utf-8")

    # Pad JSON to 4-byte alignment.
    json_padded = json_bytes + b" " * ((4 - len(json_bytes) % 4) % 4)

    # Binary data: vertex positions + indices.
    positions = []
    for x in (-1.0, 1.0):
        for y in (-1.0, 1.0):
            for z in (-1.0, 1.0):
                positions.extend([x, y, z])
    pos_bytes = struct.pack("<24f", *positions)
    indices = []
    # 12 triangles (2 per face)
    faces = [
        (0, 1, 2, 1, 3, 2),  # front
        (4, 6, 5, 6, 7, 5),  # back
        (0, 4, 5, 0, 5, 1),  # bottom
        (2, 3, 7, 2, 7, 6),  # top
        (0, 2, 6, 0, 6, 4),  # left
        (1, 5, 7, 1, 7, 3),  # right
    ]
    for f in faces:
        indices.extend(f)
    idx_bytes = struct.pack("<36H", *indices)

    bin_data = pos_bytes + idx_bytes
    bin_padded = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)

    # GLB header.
    header = struct.pack("<4sII", b"glTF", 2, 12 + len(json_padded) + 8 + len(bin_padded))

    # JSON chunk.
    json_chunk = struct.pack("<II", len(json_padded), 0x4E4F534A) + json_padded

    # BIN chunk.
    bin_chunk = struct.pack("<II", len(bin_padded), 0x004E4942) + bin_padded

    glb_bytes = header + json_chunk + bin_chunk

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(glb_bytes)
    return out_path


def _parse_args(argv: list[str]) -> Dict[str, Any]:
    """Parse argv manually (no argparse to avoid exit-on-error issues)."""
    args = {
        "stage": "validate",
        "region": "UPPER_BODY",
        "blend": "",
        "params": "{}",
        "out": "/tmp/result.json",
        "allow_fallback_glb": False,
    }
    flag_for = {
        "--stage": "stage",
        "--region": "region",
        "--blend": "blend",
        "--params": "params",
        "--out": "out",
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--allow-fallback-glb":
            args["allow_fallback_glb"] = True
            i += 1
        elif arg in flag_for and i + 1 < len(argv):
            args[flag_for[arg]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return args


def _check_blend(blend: str) -> str | None:
    """Return an actionable error string when ``blend`` is not a usable .blend."""
    if not blend:
        return "--blend was not provided"
    if not os.path.exists(blend):
        return f"base body not found: {blend}"
    if not os.path.isfile(blend):
        return f"--blend is not a file: {blend}"

    with open(blend, "rb") as fh:
        head = fh.read(8)
    # Real .blend files start with the magic header, or are zstd-compressed
    # (Blender 3.0+ "Compress" saves).
    if head.startswith(b"BLENDER") or head.startswith(ZSTD_MAGIC):
        return None
    return (
        f"{blend} is not a real .blend file (got {os.path.getsize(blend)} bytes, "
        f"header {head!r}). It is probably still the committed placeholder stub — "
        "export a real BASE_BODY.blend from Blender and track it with Git LFS."
    )


def _run_stage(stage: str, blend: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "fail",
        "stage": stage,
        "region": region,
        "metrics": {},
        "checkpoint": None,
        "outfit": "",
        "error": None,
    }

    # Resolve the outfit name the same way the pipeline reports do.
    outfit = params.get("outfit") or os.path.splitext(os.path.basename(blend))[0] or "outfit"
    result["outfit"] = outfit

    blend_error = _check_blend(blend)
    if blend_error:
        result["metrics"]["error"] = blend_error
        result["error"] = blend_error
        return result

    try:
        from bpy_scripts import pipeline  # noqa: PLC0415 - import after sys.path setup
    except Exception as exc:  # pragma: no cover - depends on Blender runtime
        detail = f"{exc.__class__.__name__}: {exc}"
        result["metrics"]["error"] = f"failed to import bpy_scripts.pipeline: {detail}"
        result["metrics"]["traceback"] = traceback.format_exc()
        result["error"] = result["metrics"]["error"]
        return result

    try:
        if stage == "all":
            res = pipeline.run_full_pipeline(blend, region, params)
        else:
            res = pipeline.run_stage(stage, blend, region, params)
    except Exception as exc:  # pipeline bug: report it, do not swallow it
        detail = f"{exc.__class__.__name__}: {exc}"
        result["metrics"]["error"] = detail
        result["metrics"]["traceback"] = traceback.format_exc()
        result["error"] = detail
        return result

    result["status"] = res.get("status", "fail")
    result["metrics"] = res.get("metrics", {})
    result["checkpoint"] = res.get("checkpoint")
    result["outfit"] = res.get("outfit", outfit)
    if result["status"] != "pass":
        result["error"] = (
            res.get("error") or result["metrics"].get("error") or "pipeline returned fail"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    stage = args["stage"]
    blend = args["blend"]
    region = args["region"]
    out = args["out"]

    try:
        params = json.loads(args["params"])
        if not isinstance(params, dict):
            raise TypeError("--params must be a JSON object")
    except Exception as exc:
        params = {}
        params_error = f"ignored invalid --params: {exc}"
    else:
        params_error = None

    if stage not in STAGES:
        result: Dict[str, Any] = {
            "status": "fail",
            "stage": stage,
            "region": region,
            "metrics": {},
            "checkpoint": None,
            "outfit": params.get("outfit", ""),
            "error": f"unknown stage {stage!r}; expected one of {', '.join(STAGES)}",
        }
    else:
        started = time.time()
        result = _run_stage(stage, blend, region, params)
        if params_error:
            result["metrics"]["params_warning"] = params_error

        outfit = result["outfit"] or params.get("outfit") or "outfit"
        glb_path = os.path.join(REPO_ROOT, "outputs", outfit, f"{outfit}_Modular.glb")

        if result["status"] == "pass" and os.path.exists(glb_path):
            result["metrics"].update(
                {
                    "glb_path": str(glb_path),
                    "glb_file_size_bytes": os.path.getsize(glb_path),
                    "glb_is_fallback": False,
                }
            )
        elif args["allow_fallback_glb"]:
            # Explicit opt-in only: a placeholder must never pass for a build.
            try:
                _minimal_glb(str(glb_path))
                result["metrics"].update(
                    {
                        "glb_path": str(glb_path),
                        "glb_file_size_bytes": os.path.getsize(glb_path),
                        "glb_is_fallback": True,
                    }
                )
            except Exception as exc:
                result["metrics"]["glb_fallback_error"] = f"{exc.__class__.__name__}: {exc}"
        else:
            result["metrics"]["glb_path"] = None
            result["metrics"]["glb_is_fallback"] = False

        result["metrics"].setdefault("duration_seconds", round(time.time() - started, 3))

    # Write result JSON (always, even on failure) so CI can print the reason.
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    if result["status"] == "pass":
        print(f"[run_pipeline] {stage}: PASS")
        return 0

    print(f"[run_pipeline] {stage}: FAIL — {result.get('error')}", file=sys.stderr)
    print(json.dumps(result.get("metrics", {}), indent=2)[:4000], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
