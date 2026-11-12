"""Weights validator: max 4 weights/vertex (mobile ceiling) + group coverage."""

from __future__ import annotations

from typing import Any, Dict

try:
    import bmesh
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bmesh = None  # type: ignore[assignment]
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger

logger = get_logger("validators.weights_check")

SHELL_OBJECT_NAME = "CLOTH_Shell"
MAX_WEIGHTS = 4  # CHANGELOG v1.3.0 mobile ceiling


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

    shell = bpy.data.objects.get(SHELL_OBJECT_NAME)
    if shell is None or shell.type != "MESH":
        return {
            "status": "fail",
            "metrics": {"error": "CLOTH_Shell mesh not found"},
        }

    bm = bmesh.new()
    try:
        bm.from_mesh(shell.data)
        deform_layer = bm.verts.layers.deform.active
        max_weights = 0
        verts_exceeding = 0
        if deform_layer is not None:
            for vert in bm.verts:
                n = len(vert[deform_layer])
                max_weights = max(max_weights, n)
                if n > MAX_WEIGHTS:
                    verts_exceeding += 1
        else:
            max_weights = 0

        passed = max_weights <= MAX_WEIGHTS and verts_exceeding == 0
        return {
            "status": "pass" if passed else "fail",
            "metrics": {
                "max_weights_per_vertex": max_weights,
                "weight_limit": MAX_WEIGHTS,
                "verts_exceeding_limit": verts_exceeding,
                "vertex_groups": [g.name for g in shell.vertex_groups],
            },
        }
    finally:
        bm.free()
