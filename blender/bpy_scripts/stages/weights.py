"""Stage 03: Transfer weights and build the UN_SHRINK vertex group."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    import bmesh
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    bmesh = None  # type: ignore[assignment]
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
    """Core bpy operation: data-transfer weights from body to shell."""
    if shell.type != "MESH" or body.type != "MESH":
        return {"status": "fail", "error": "shell and body must be mesh objects"}

    # Ensure shell has a vertex group for every body group.
    shell_vg_names = {g.name for g in shell.vertex_groups}
    for group in body.vertex_groups:
        if group.name not in shell_vg_names:
            shell.vertex_groups.new(name=group.name)

    # Data Transfer modifier: body -> shell, POLYINTERP_NEAREST (CHANGELOG v1.2.1).
    mod = shell.modifiers.new(name="DataTransfer", type="DATA_TRANSFER")
    mod.object = body
    mod.use_loop_mapping = False
    mod.use_max_type = "POLYINTERP_NEAREST"
    mod.data_transfer_uses = "VERTEX_WEIGHTS"
    mod.vertex_weight_mapping = "POLYINTERP_NEAREST"
    mod.vertex_weight_mix = "BEFORE"
    mod.vertex_weight_multiplier = 1.0

    bpy.context.view_layer.objects.active = shell
    shell.select_set(True)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception as exc:  # pragma: no cover
        return {"status": "fail", "error": f"data transfer apply failed: {exc}"}

    # Cap weights at weight_limit per vertex (mobile ceiling).
    mesh = shell.data
    bm = None
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        deform_layer = bm.verts.layers.deform.active
        if deform_layer is not None:
            for vert in bm.verts:
                weights = vert[deform_layer]
                if len(weights) > weight_limit:
                    # Keep the strongest `weight_limit` influences.
                    top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:weight_limit]
                    vert[deform_layer] = dict(top)
        bm.to_mesh(mesh)
        mesh.update()
    except Exception as exc:  # pragma: no cover
        logger.warning("weight capping skipped: %s", exc)
    finally:
        if bm is not None:
            bm.free()

    # UN_SHRINK group: vertices already near the body are excluded from shrinkwrap.
    if UN_SHRINK_GROUP not in shell_vg_names:
        shell.vertex_groups.new(name=UN_SHRINK_GROUP)

    shell.select_set(False)
    return {
        "status": "pass",
        "vertex_groups": [g.name for g in shell.vertex_groups],
        "max_weights_per_vertex": weight_limit,
        "un_shrink_group": UN_SHRINK_GROUP,
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
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    shell = _find(SHELL_OBJECT_NAME)
    body = _find(BASE_BODY_NAME)
    if shell is None:
        return _fail("CLOTH_Shell not found — run shell/shrinkwrap stage first")
    if body is None:
        return _fail("BASE_BODY not found in blend")

    op_result = transfer_weights(shell, body, weight_limit=weight_limit)
    if op_result.get("status") != "pass":
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": op_result,
            "checkpoint": None,
        }

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
    except Exception as exc:  # pragma: no cover
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {**op_result, "error": f"checkpoint save failed: {exc}"},
            "checkpoint": None,
        }
