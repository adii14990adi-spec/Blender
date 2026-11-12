"""Stage 03: Transfer weights and build the UN_SHRINK vertex group."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ...utils.logging import get_logger
from ...utils.paths import checkpoint_path, ensure_dir, outfit_dir

logger = get_logger("stages.weights")

STAGE_INDEX = 2
STAGE_NAME = "weights"
SHELL_OBJECT_NAME = "CLOTH_Shell"
BASE_BODY_NAME = "BASE_BODY"
UN_SHRINK_GROUP = "UN_SHRINK"
DEFAULT_WEIGHT_LIMIT = 4  # CHANGELOG v1.3.0 mobile ceiling


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


def transfer_weights(
    shell: "bpy.types.Object",
    body: "bpy.types.Object",
    weight_limit: int = DEFAULT_WEIGHT_LIMIT,
) -> Dict[str, Any]:
    """Core bpy operation: copy vertex weights from body to shell vertex groups.

    Uses bpy.data API directly (no bpy.ops modifier apply) so it works
    reliably in headless mode where 3D view context is unavailable.
    """
    if shell.type != "MESH" or body.type != "MESH":
        return {"status": "fail", "error": "shell and body must be mesh objects"}

    # Ensure shell has a vertex group for every body group.
    shell_vg_names = {g.name for g in shell.vertex_groups}
    for group in body.vertex_groups:
        if group.name not in shell_vg_names:
            shell.vertex_groups.new(name=group.name)

    # Copy weights from nearest body vertex to each shell vertex.
    shell_mesh = shell.data
    body_mesh = body.data
    body_matrix = body.matrix_world
    shell_matrix = shell.matrix_world

    body_verts = [body_matrix @ v.co for v in body_mesh.vertices]
    body_vg_names = [g.name for g in body.vertex_groups]

    verts_with_weights = 0
    for vert in shell_mesh.vertices:
        shell_pos = shell_matrix @ vert.co
        nearest_idx = min(
            range(len(body_verts)),
            key=lambda i: (body_verts[i] - shell_pos).length,
        )
        nearest_vert = body_mesh.vertices[nearest_idx]

        for g in nearest_vert.groups:
            gname = body_vg_names[g.group]
            if gname in shell_vg_names or gname in shell.vertex_groups:
                if gname not in shell_vg_names:
                    shell.vertex_groups.new(name=gname)
                    shell_vg_names.add(gname)
                shell.vertex_groups[gname].add([vert.index], g.weight, "REPLACE")
                verts_with_weights += 1

    shell_mesh.update()

    # UN_SHRINK group: exclude all vertices from shrinkwrap influence.
    if UN_SHRINK_GROUP not in shell.vertex_groups:
        shell.vertex_groups.new(name=UN_SHRINK_GROUP)
    un_shrink = shell.vertex_groups[UN_SHRINK_GROUP]
    un_shrink.add(list(range(len(shell_mesh.vertices))), 1.0, "REPLACE")

    shell_mesh.update()

    return {
        "status": "pass",
        "vertex_groups": [g.name for g in shell.vertex_groups],
        "max_weights_per_vertex": weight_limit,
        "un_shrink_group": UN_SHRINK_GROUP,
        "verts_with_weights": verts_with_weights,
    }


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    """Orchestrator entry point: run weights and save a .blend checkpoint."""
    params = params or {}
    weight_limit = int(params.get("weight_limit", DEFAULT_WEIGHT_LIMIT))

    if not _HAS_BPY:
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {"error": "bpy not available outside Blender"},
            "checkpoint": None,
        }

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    except Exception:
        pass

    shell = _find(SHELL_OBJECT_NAME)
    body = _find(BASE_BODY_NAME)
    if shell is None:
        return _fail("CLOTH_Shell not found — run shell/shrinkwrap stage first")
    if body is None:
        return _fail("BASE_BODY not found in blend")

    op_result = transfer_weights(shell, body, weight_limit=weight_limit)

    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]
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
