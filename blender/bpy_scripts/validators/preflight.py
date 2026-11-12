"""Preflight validator: confirms required objects exist before the pipeline runs."""

from __future__ import annotations

from typing import Any, Dict

try:
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger

logger = get_logger("validators.preflight")

REQUIRED_OBJECTS = ["BASE_BODY"]


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not _HAS_BPY:
        return {
            "status": "fail",
            "metrics": {"error": "bpy not available outside Blender"},
        }

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    missing = [name for name in REQUIRED_OBJECTS if name not in bpy.data.objects]
    if missing:
        return {
            "status": "fail",
            "metrics": {"missing_objects": missing, "error": "base body missing"},
        }

    return {
        "status": "pass",
        "metrics": {
            "objects": len(bpy.data.objects),
            "meshes": sum(1 for o in bpy.data.objects if o.type == "MESH"),
            "base_body_present": True,
        },
    }
