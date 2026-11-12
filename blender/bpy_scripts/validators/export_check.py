"""Export validator: verify the FBX/GLB artifacts on disk, not just their paths.

Runs without Blender, so it is also the last gate in CI before artifacts are
published.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from ..utils.fbx_inspect import inspect_fbx
from ..utils.glb_inspect import inspect_glb
from ..utils.logging import get_logger
from ..utils.paths import fbx_export_path, glb_export_path

logger = get_logger("validators.export_check")

MIN_BYTES = 4096


def run(
    blend_path: str = "",
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    params = params or {}
    outfit = str(params.get("outfit") or "jacket_urban_01")
    fbx = params.get("fbx_path") or fbx_export_path(outfit, params.get("variant"))
    glb = params.get("glb_path") or glb_export_path(outfit, params.get("variant"))
    require_skin = bool(params.get("require_skin", True))
    max_influences = int(params.get("weight_limit", 4))

    problems = []
    for label, path in (("FBX", fbx), ("GLB", glb)):
        if not os.path.exists(path):
            problems.append(f"{label} missing: {path}")
        elif os.path.getsize(path) < MIN_BYTES:
            problems.append(f"{label} is only {os.path.getsize(path)} bytes: {path}")
    if problems:
        return {"status": "fail", "metrics": {"error": "; ".join(problems), "fbx": fbx, "glb": glb}}

    glb_result = inspect_glb(
        glb,
        expect_skin=require_skin,
        expect_textures=True,
        expect_normals=True,
        expect_uvs=True,
        max_influences=max_influences,
    )
    fbx_result = inspect_fbx(fbx, expect_skin=require_skin, expect_uvs=True, expect_normals=True)
    issues = [f"GLB: {error}" for error in glb_result["errors"]]
    issues += [f"FBX: {error}" for error in fbx_result["errors"]]

    metrics = {
        "fbx": fbx,
        "glb": glb,
        "fbx_validation": fbx_result,
        "glb_validation": glb_result,
        "problems": issues,
    }
    return {"status": "fail" if issues else "pass", "metrics": metrics}
