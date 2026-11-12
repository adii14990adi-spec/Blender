"""Weights validator: deform-only weights, <= limit influences, normalised, bound."""

from __future__ import annotations

from typing import Any, Dict

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..stages.weights import HELPER_GROUPS, verify_weights
from ..utils.logging import get_logger
from ..utils.rig import deform_bone_names

logger = get_logger("validators.weights_check")

GARMENT_OBJECT_NAME = "CLOTH_Shell"
MAX_WEIGHTS = 4


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    params = params or {}
    if not _HAS_BPY:
        return {"status": "fail", "metrics": {"error": "bpy is not available: run inside Blender"}}
    try:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    except Exception as exc:
        return {"status": "fail", "metrics": {"error": f"could not open {blend_path}: {exc}"}}

    garment = bpy.data.objects.get(params.get("garment_object", GARMENT_OBJECT_NAME))
    if garment is None or garment.type != "MESH":
        return {"status": "fail", "metrics": {"error": f"{GARMENT_OBJECT_NAME} mesh not found"}}

    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if not armatures:
        return {"status": "fail", "metrics": {"error": "no armature in the scene"}}
    armature = armatures[0]
    bones = deform_bone_names(armature)
    limit = int(params.get("weight_limit", MAX_WEIGHTS))
    verification = verify_weights(garment, bones, limit)

    modifiers = [m for m in garment.modifiers if m.type == "ARMATURE" and m.object == armature]
    metrics = {
        "armature": armature.name,
        "deform_bones": len(bones),
        "weight_limit": limit,
        "bound": bool(modifiers),
        "vertex_groups": [group.name for group in garment.vertex_groups],
        "helper_groups": sorted(HELPER_GROUPS & {group.name for group in garment.vertex_groups}),
        "verification": verification,
    }
    problems = []
    if not modifiers:
        problems.append("garment has no Armature modifier bound to the character armature")
    if verification["unweighted_vertices"]:
        problems.append(f"{verification['unweighted_vertices']} unweighted vertices")
    if verification["unnormalised_vertices"]:
        problems.append(f"{verification['unnormalised_vertices']} vertices are not normalised")
    if verification["vertices_exceeding_limit"]:
        problems.append(
            f"{verification['vertices_exceeding_limit']} vertices exceed {limit} influences"
        )
    if verification["helper_groups_colliding_with_bones"]:
        problems.append("a helper group collides with a deform bone name")
    metrics["problems"] = problems
    return {"status": "fail" if problems else "pass", "metrics": metrics}
