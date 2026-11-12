"""Small, headless-safe wrappers around Blender data/operator APIs.

Every helper is written for ``blender -b`` (and the ``bpy`` module): no helper
depends on a 3D viewport, an active area, or a UI context. Operators are only
used where Blender provides no data API (UV unwrapping, rig auto-weighting,
export), and always with an explicit object selection.
"""

from __future__ import annotations

from typing import Any, Iterable, List

try:  # pragma: no cover - exercised inside Blender only
    import bpy

    HAS_BPY = True
except ImportError:  # pragma: no cover - lets pytest import the package
    bpy = None  # type: ignore[assignment]
    HAS_BPY = False


class PipelineError(RuntimeError):
    """Actionable pipeline failure (message is shown verbatim in CI logs)."""


def require_bpy() -> None:
    if not HAS_BPY:
        raise PipelineError(
            "bpy is not available: run this module inside Blender "
            "(blender -b --python ...) or with the bpy module installed"
        )


def objects() -> List[Any]:
    return list(bpy.data.objects)  # type: ignore[union-attr]


def find_object(name: str | None, predicate=None):
    """Find by exact name, else by an optional predicate. Never guesses silently."""
    if not name:
        return None
    obj = bpy.data.objects.get(name)  # type: ignore[union-attr]
    if obj is not None:
        return obj
    if predicate is not None:
        for candidate in bpy.data.objects:  # type: ignore[union-attr]
            if predicate(candidate):
                return candidate
    return None


def find_mesh_object(name: str | None, predicate=None):
    obj = find_object(name, predicate)
    return obj if obj is not None and obj.type == "MESH" else None


def find_armature(name: str | None = None):
    if name:
        obj = bpy.data.objects.get(name)  # type: ignore[union-attr]
        if obj is not None and obj.type == "ARMATURE":
            return obj
    for candidate in bpy.data.objects:  # type: ignore[union-attr]
        if candidate.type == "ARMATURE":
            return candidate
    return None


def ensure_linked(obj) -> None:
    """Guarantee ``obj`` is in the active view layer (needed for operators/export)."""
    try:
        if obj.name not in bpy.context.view_layer.objects:  # type: ignore[union-attr]
            bpy.context.collection.objects.link(obj)  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - context dependent
        pass


def select_only(obj_list: Iterable[Any], active=None) -> None:
    """Deselect everything, then select ``obj_list`` with ``active`` last."""
    objs = list(obj_list)
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:  # pragma: no cover - no window/context
        pass
    for obj in bpy.context.view_layer.objects:  # type: ignore[union-attr]
        try:
            obj.select_set(False)
        except Exception:  # pragma: no cover
            pass
    for obj in objs:
        ensure_linked(obj)
        try:
            obj.select_set(True)
        except Exception:  # pragma: no cover
            pass
    target = active or (objs[-1] if objs else None)
    if target is not None:
        bpy.context.view_layer.objects.active = target  # type: ignore[union-attr]


def set_active(obj) -> None:
    ensure_linked(obj)
    bpy.context.view_layer.objects.active = obj  # type: ignore[union-attr]


def object_bounds(obj, world: bool = True) -> "tuple[list, list]":
    """World-space AABB of ``obj`` from its mesh vertices (not the stale bound_box)."""

    matrix = obj.matrix_world if world else obj.matrix_identity
    pts = [matrix @ v.co for v in obj.data.vertices]
    if not pts:
        raise PipelineError(f"{obj.name} has no vertices; cannot measure it")
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo, hi


def world_vertices(obj) -> List[Any]:
    matrix = obj.matrix_world
    return [matrix @ v.co for v in obj.data.vertices]


def apply_modifier(obj, modifier_name: str) -> bool:
    set_active(obj)
    try:
        bpy.ops.object.modifier_apply(modifier=modifier_name)  # type: ignore[union-attr]
        return True
    except Exception:
        return False


def remove_object(obj) -> None:
    if obj is None:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)  # type: ignore[union-attr]
    except Exception:  # pragma: no cover
        pass


def purge_orphans() -> None:
    try:
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - background context
        for mesh in list(bpy.data.meshes):  # type: ignore[union-attr]
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)  # type: ignore[union-attr]


def open_blend(path: str) -> None:
    require_bpy()
    try:
        bpy.ops.wm.open_mainfile(filepath=path)  # type: ignore[union-attr]
    except Exception as exc:  # pragma: no cover - corrupt file
        raise PipelineError(f"could not open {path}: {exc.__class__.__name__}: {exc}") from exc


def save_blend(path: str, compress: bool = True) -> None:
    require_bpy()
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, compress=compress, check_existing=False)  # type: ignore[union-attr]
    except Exception as exc:  # pragma: no cover
        raise PipelineError(f"could not save checkpoint {path}: {exc}") from exc


def mesh_stats(obj) -> dict:
    mesh = obj.data
    tri_count = 0
    for poly in mesh.polygons:
        tri_count += max(1, len(poly.vertices) - 2)
    return {
        "vertex_count": len(mesh.vertices),
        "edge_count": len(mesh.edges),
        "face_count": len(mesh.polygons),
        "triangle_count": tri_count,
        "uv_layers": [layer.name for layer in mesh.uv_layers],
        "vertex_groups": len(obj.vertex_groups),
        "material_slots": [
            slot.material.name if slot.material else None for slot in obj.material_slots
        ],
    }


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
