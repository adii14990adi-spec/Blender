"""Topology validator: manifold garment, positive thickness, consistent normals."""

from __future__ import annotations

from typing import Any, Dict

try:  # pragma: no cover
    import bmesh
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    bmesh = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger

logger = get_logger("validators.topology_check")

GARMENT_OBJECT_NAME = "CLOTH_Shell"
DEGENERATE_AREA = 1e-7


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
        return {
            "status": "fail",
            "metrics": {
                "error": f"{GARMENT_OBJECT_NAME} mesh not found in {blend_path}",
                "objects": [obj.name for obj in bpy.data.objects],
            },
        }

    bm = bmesh.new()
    try:
        bm.from_mesh(garment.data)
        bm.faces.ensure_lookup_table()
        non_manifold = [edge for edge in bm.edges if not edge.is_manifold]
        degenerate = [face for face in bm.faces if face.calc_area() < DEGENERATE_AREA]
        # A watertight shell has every edge shared by exactly two faces.
        boundary_edges = [edge for edge in bm.edges if len(edge.link_faces) == 1]
        # True winding test: on a consistently wound mesh both faces sharing an
        # edge traverse it in *opposite* directions. Comparing adjacent face
        # normals (the previous check) flags the legitimate 180-degree folds of
        # a thin solidified shell instead of real problems.
        inconsistent_edges = 0
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            directions = []
            for face in edge.link_faces:
                for loop in face.loops:
                    if loop.edge == edge:
                        directions.append(1 if loop.vert == edge.verts[0] else -1)
                        break
            if len(directions) == 2 and directions[0] == directions[1]:
                inconsistent_edges += 1
        folds = 0
        for face in bm.faces:
            for edge in face.edges:
                if len(edge.link_faces) == 2:
                    other = edge.link_faces[1] if edge.link_faces[0] == face else edge.link_faces[0]
                    if face.normal.dot(other.normal) < -0.98:
                        folds += 1
                        break
        volume = bm.calc_volume(signed=True)
        metrics = {
            "vertex_count": len(bm.verts),
            "edge_count": len(bm.edges),
            "face_count": len(bm.faces),
            "non_manifold_edges": len(non_manifold),
            "boundary_edges": len(boundary_edges),
            "degenerate_faces": len(degenerate),
            "winding_inconsistencies": inconsistent_edges,
            "thin_shell_folds": folds,
            "volume_m3": round(volume, 8),
            "material_slots": [
                slot.material.name if slot.material else None for slot in garment.material_slots
            ],
        }
        problems = []
        if non_manifold:
            problems.append(f"{len(non_manifold)} non-manifold edges")
        if boundary_edges:
            problems.append(f"{len(boundary_edges)} boundary edges (garment is not watertight)")
        if degenerate:
            problems.append(f"{len(degenerate)} degenerate faces")
        if volume <= 0:
            problems.append(f"signed volume {volume:.6f} is not positive (inverted normals)")
        if inconsistent_edges:
            problems.append(
                f"{inconsistent_edges} edges have inconsistent winding (inside-out faces)"
            )
        if not garment.material_slots or garment.material_slots[0].material is None:
            problems.append("no material assigned")
        metrics["problems"] = problems
        return {"status": "fail" if problems else "pass", "metrics": metrics}
    finally:
        bm.free()
