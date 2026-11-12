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

from ...utils.logging import get_logger
from ...utils.paths import checkpoint_path, ensure_dir, outfit_dir

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
    """Core bpy operation: add + apply a Shrinkwrap modifier onto *body*."""
    if shell.type != "MESH" or body.type != "MESH":
        return {"status": "fail", "error": "shell and body must be mesh objects"}

    mod = shell.modifiers.new(name="Shrinkwrap", type="SHRINKWRAP")
    mod.target = body
    mod.wrap_method = "PROJECT"
    mod.use_positive_direction = True
    mod.use_negative_direction = True
    mod.offset = offset

    bpy.context.view_layer.objects.active = shell
    shell.select_set(True)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception as exc:  # pragma: no cover - Blender-context dependent
        return {"status": "fail", "error": f"modifier apply failed: {exc}"}

    shell.select_set(False)
    return {
        "status": "pass",
        "shrinkwrap_offset": offset,
        "shell_vertex_count": len(shell.data.vertices),
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

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    shell = _find(SHELL_OBJECT_NAME)
    body = _find(BASE_BODY_NAME)
    if shell is None:
        return _fail("CLOTH_Shell not found — run shell stage first")
    if body is None:
        return _fail("BASE_BODY not found in blend")

    op_result = shrinkwrap_to_body(shell, body, offset=offset)
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
    except Exception as exc:  # pragma: no cover - filesystem dependent
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {**op_result, "error": f"checkpoint save failed: {exc}"},
            "checkpoint": None,
        }
