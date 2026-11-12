"""Stage 02: Shrinkwrap the cloth shell onto the base body."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, input_blend_path, outfit_dir

logger = get_logger("stages.shrinkwrap")

STAGE_INDEX = 1
STAGE_NAME = "shrinkwrap"
SHELL_OBJECT_NAME = "CLOTH_Shell"
BASE_BODY_NAME = "BASE_BODY"
DEFAULT_OFFSET = 0.004  # CHANGELOG v1.3.0


def _fail(error: str, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "fail",
        "stage": STAGE_NAME,
        "metrics": metrics or {},
        "checkpoint": None,
        "error": error,
    }


def _pass(metrics: Dict[str, Any], checkpoint: str | None = None) -> Dict[str, Any]:
    return {
        "status": "pass",
        "stage": STAGE_NAME,
        "metrics": metrics,
        "checkpoint": checkpoint,
    }


def _find(name: str):
    for obj in bpy.data.objects:  # type: ignore[attr-defined]
        if obj.name == name:
            return obj
    return None


def shrinkwrap_to_body(
    shell: "bpy.types.Object",
    body: "bpy.types.Object",
    offset: float = DEFAULT_OFFSET,
) -> Dict[str, Any]:
    """Core bpy operation: apply shrinkwrap offset via vertex displacement.

    Uses bpy.data API directly (no bpy.ops modifier apply) so it works
    reliably in headless mode where 3D view context is unavailable.
    """
    if shell.type != "MESH" or body.type != "MESH":
        return {"status": "fail", "error": "shell and body must be mesh objects"}

    # Displace shell vertices along their normals toward the body surface,
    # approximating a shrinkwrap offset. For each shell vertex, find the
    # nearest body vertex and move the shell vertex toward it by `offset`.
    shell_mesh = shell.data
    body_mesh = body.data
    body_matrix = body.matrix_world
    shell_matrix = shell.matrix_world

    # Build list of body vertex world positions.
    body_verts = [body_matrix @ v.co for v in body_mesh.vertices]

    displaced = 0
    for vert in shell_mesh.vertices:
        shell_pos = shell_matrix @ vert.co
        # Find nearest body vertex.
        nearest = min(body_verts, key=lambda p: (p - shell_pos).length)
        direction = (nearest - shell_pos).normalized()
        vert.co += direction * offset
        displaced += 1

    shell_mesh.update()

    return {
        "status": "pass",
        "shrinkwrap_offset": offset,
        "shell_vertex_count": len(shell_mesh.vertices),
        "displaced_vertices": displaced,
    }


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    """Orchestrator entry point: run shrinkwrap and save a .blend checkpoint."""
    params = params or {}
    offset = float(params.get("offset", DEFAULT_OFFSET))

    if not _HAS_BPY:
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {"error": "bpy not available outside Blender"},
            "checkpoint": None,
        }

    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]
    input_path, input_info = input_blend_path(blend_path, outfit, STAGE_INDEX)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.wm.open_mainfile(filepath=input_path)
    except Exception as exc:
        return _fail(f"could not open {input_path}: {exc}", {"input": input_info})

    shell = _find(SHELL_OBJECT_NAME)
    body = _find(BASE_BODY_NAME)
    if shell is None:
        return _fail("CLOTH_Shell not found — run shell stage first", {"input": input_info})
    if body is None:
        return _fail("BASE_BODY not found in blend", {"input": input_info})

    op_result = {**shrinkwrap_to_body(shell, body, offset=offset), "input": input_info}

    ensure_dir(outfit_dir(outfit))
    checkpoint = checkpoint_path(outfit, STAGE_INDEX, STAGE_NAME)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=checkpoint)
        return {
            "status": "pass",
            "stage": STAGE_NAME,
            "metrics": op_result,
            "checkpoint": checkpoint,
        }
    except Exception as exc:  # pragma: no cover - filesystem dependent
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {**op_result, "error": f"checkpoint save failed: {exc}"},
            "checkpoint": None,
        }
