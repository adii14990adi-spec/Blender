"""Topology validator: non-manifold edges + degenerate geometry on the shell."""

from __future__ import annotations

from typing import Any, Dict

try:
    import bmesh
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    bmesh = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger

logger = get_logger("validators.topology_check")

SHELL_OBJECT_NAME = "CLOTH_Shell"
DEGENERATE_AREA = 1e-6


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
        bm.edges.ensure_lookup_table()
        non_manifold = [e for e in bm.edges if not e.is_manifold]
        degenerate = [f for f in bm.faces if f.calc_area() < DEGENERATE_AREA]
        passed = len(non_manifold) == 0 and len(degenerate) == 0
        return {
            "status": "pass" if passed else "fail",
            "metrics": {
                "non_manifold_edges": len(non_manifold),
                "degenerate_faces": len(degenerate),
                "vertex_count": len(bm.verts),
                "edge_count": len(bm.edges),
                "face_count": len(bm.faces),
            },
        }
    finally:
        bm.free()
