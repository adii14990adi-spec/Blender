"""Preflight validator: prove the body asset is usable before any stage runs.

This is the gate the old pipeline was missing. It checks the resolved variant
against its manifest and reports what the asset actually contains instead of
assuming a ``BASE_BODY`` object with an armature and vertex groups.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, inspect_blend_file, load_outfit, resolve_variant
from ..utils.bpy_helpers import mesh_stats, open_blend
from ..utils.logging import get_logger
from ..utils.rig import deform_bone_names

logger = get_logger("validators.preflight")

MIN_FACES = 100


def _result(status: str, metrics: Dict[str, Any], error: str | None = None) -> Dict[str, Any]:
    return {"status": status, "metrics": metrics, "error": error}


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    params = params or {}
    if not _HAS_BPY:
        return _result("fail", {"error": "bpy is not available: run inside Blender"})

    try:
        variant = resolve_variant(params.get("variant"), params.get("_body_manifest"))
        load_outfit(str(params.get("outfit") or "jacket_urban_01"), params.get("_outfit_manifest"))
    except ConfigError as exc:
        return _result("fail", {"error": str(exc)})

    file_check = inspect_blend_file(blend_path)
    if not file_check["ok"]:
        return _result("fail", {"file": file_check, "error": file_check["reason"]})

    try:
        open_blend(blend_path)
    except Exception as exc:
        return _result("fail", {"error": f"could not open {blend_path}: {exc}"})

    objects = {obj.name: obj.type for obj in bpy.data.objects}
    body = bpy.data.objects.get(params.get("body_object") or variant.body_object)
    errors: List[str] = []
    if body is None or body.type != "MESH":
        return _result(
            "fail",
            {
                "objects": objects,
                "expected_body": variant.body_object,
                "error": (
                    f"body object {variant.body_object!r} is missing from {blend_path}. "
                    f"Objects present: {sorted(objects)}. "
                    "Update `body_object` in assets/base_body/manifest.json."
                ),
            },
        )

    stats = mesh_stats(body)
    if stats["face_count"] < MIN_FACES:
        errors.append(f"body has only {stats['face_count']} faces (minimum {MIN_FACES})")
    if stats["triangle_count"] == 0:
        errors.append("body has no triangles")

    armature = None
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            armature = obj
            break
    bones = deform_bone_names(armature) if armature else []
    skinned = bool(bones) and any(group.name in bones for group in body.vertex_groups)

    metrics: Dict[str, Any] = {
        "blend": blend_path,
        "variant": variant.name,
        "variant_file": variant.path,
        "objects": objects,
        "body": {
            "name": body.name,
            "stats": stats,
            "material_slots": stats["material_slots"],
            "vertex_group_count": len(body.vertex_groups),
        },
        "armature": {
            "present": armature is not None,
            "name": armature.name if armature else None,
            "deform_bones": len(bones),
            "body_skinned": skinned,
        },
        "textures_present": len([img for img in bpy.data.images if img.source == "FILE"]),
        "auxiliary_objects": [name for name in variant.auxiliary_objects if name in objects],
        "orientation": {"up_axis": variant.up_axis, "forward_axis": variant.forward_axis},
    }
    if errors:
        metrics["error"] = "; ".join(errors)
        return _result("fail", metrics, metrics["error"])

    # No rig is a supported-but-reported state: the weights stage builds one.
    notes = []
    if not skinned:
        notes.append(
            "base body has no armature/skin weights; the weights stage will generate a "
            "procedural humanoid rig (reported as rig_source=procedural)"
        )
    metrics["notes"] = notes
    return _result("pass", metrics)
