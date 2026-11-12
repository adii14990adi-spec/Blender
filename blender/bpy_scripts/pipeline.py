"""Pipeline orchestrator.

Dispatches a single stage to the matching bpy stage module or validator, and
runs full validation (dry-run) for the ``validate`` stage. Every stage result is
a JSON-serializable dict: ``{"status", "stage", "metrics", "checkpoint"}``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

# Allow ``python blender/bpy_scripts/pipeline.py`` and
# ``blender -b -P blender/run_pipeline.py`` (run_pipeline inserts blender/).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER_DIR = os.path.dirname(_HERE)
if _BLENDER_DIR not in sys.path:
    sys.path.insert(0, _BLENDER_DIR)

from bpy_scripts.stages import (  # noqa: E402 - path must be set before package import
    export_fbx,
    shell,
    shrinkwrap,
    uv_material,
    weights,
)
from bpy_scripts.utils.paths import validation_report_path  # noqa: E402
from bpy_scripts.validators import (  # noqa: E402
    preflight,
    topology_check,
    uv_check,
    weights_check,
)

STAGE_REGISTRY: Dict[str, Any] = {
    "shell": shell,
    "subdivide": shell,
    "shrinkwrap": shrinkwrap,
    "weights": weights,
    "uv": uv_material,
    "material": uv_material,
    "export": export_fbx,
}

VALIDATOR_REGISTRY: Dict[str, Any] = {
    "preflight": preflight,
    "weights_check": weights_check,
    "topology_check": topology_check,
    "uv_check": uv_check,
}

# Canonical stage order — the agent may not skip a stage.
STAGE_ORDER = ["shell", "shrinkwrap", "weights", "uv", "material", "export"]


def _outfit_from_blend(blend_path: str) -> str:
    return os.path.splitext(os.path.basename(blend_path))[0]


def _write_report(outfit: str, result: Dict[str, Any]) -> None:
    report_path = validation_report_path(outfit)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as fh:
        json.dump(result, fh, indent=2)


def run_stage(
    stage: str,
    blend: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run one pipeline stage (or full validation) and return a result dict."""
    params = params or {}
    result: Dict[str, Any] = {
        "status": "fail",
        "stage": stage,
        "region": region,
        "metrics": {},
        "checkpoint": None,
    }

    # --- validate stage: dry-run, no modification ---------------------------
    if stage == "validate":
        return _run_validate(blend, region, params)

    mod = STAGE_REGISTRY.get(stage)
    if mod is None:
        result["metrics"]["error"] = f"unknown stage: {stage}"
        return result

    outfit = params.get("outfit") or _outfit_from_blend(blend)

    try:
        stage_result = mod.run(blend_path=blend, region=region, params=params, outfit=outfit)
        result.update(stage_result)
    except Exception as exc:  # stage boundary must never crash the orchestrator
        result["metrics"]["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    if result.get("status") != "pass":
        return result

    _write_report(outfit, result)
    return result


def _run_validate(blend: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Dry-run validation: open the blend and run every validator."""
    results = {}
    overall = "pass"
    for name, mod in VALIDATOR_REGISTRY.items():
        try:
            res = mod.run(blend_path=blend, region=region, params=params)
        except Exception as exc:  # validator boundary must never crash
            res = {"status": "fail", "metrics": {"error": f"{exc.__class__.__name__}: {exc}"}}
        results[name] = res
        if res.get("status") != "pass":
            overall = "fail"

    outfit = params.get("outfit") or _outfit_from_blend(blend)
    report = {
        "outfit": outfit,
        "stage": "validate",
        "status": overall,
        "metrics": {"validators": results},
        "checkpoint": None,
    }
    _write_report(outfit, report)
    return report


def run_full_pipeline(
    blend: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run all stages in order; stop at the first failure (guardrail)."""
    params = params or {}
    outfit = params.get("outfit") or _outfit_from_blend(blend)
    last: Dict[str, Any] = {"status": "pass"}
    for stage in STAGE_ORDER:
        last = run_stage(stage, blend, region, params)
        last["outfit"] = outfit
        if last.get("status") != "pass":
            break
    return last

# Trigger build on main for web viewer deploy
