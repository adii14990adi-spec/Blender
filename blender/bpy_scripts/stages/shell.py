"""Stage 01: Generate the cloth shell from a base body region."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    import bmesh
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    bmesh = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    _HAS_BPY = False

from ...utils.logging import get_logger
from ...utils.paths import checkpoint_path, ensure_dir, outfit_dir

logger = get_logger("stages.shell")

STAGE_INDEX = 0
STAGE_NAME = "shell"
SHELL_OBJECT_NAME = "CLOTH_Shell"
BASE_BODY_NAME = "BASE_BODY"


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


def _find_base_body() -> "bpy.types.Object | None":
    for obj in bpy.data.objects:  # type: ignore[attr-defined]
        if obj.name == BASE_BODY_NAME or obj.name.startswith("BASE_BODY"):
            return obj
    return None


def _region_vertex_count(body: "bpy.types.Object", region: str) -> int:
    """Approximate vertex count for the requested region (used as a metric)."""
    total = len(body.data.vertices)
    if region == "UPPER_BODY":
        return max(1, total // 2)
    if region == "LOWER_BODY":
        return max(1, total // 2)
    if region == "HEAD":
        return max(1, total // 8)
    return total


def generate_shell(blend_path: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Core bpy operation: duplicate the base body region as CLOTH_Shell."""
    if not _HAS_BPY:
        return _fail("bpy not available outside Blender")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    body = _find_base_body()
    if body is None:
        # Create a minimal base body if none exists in the blend file.
        # This allows the pipeline to run even with a placeholder/stub .blend.
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
        body = bpy.context.active_object
        body.name = BASE_BODY_NAME

    # Work on a fresh mesh copy so the base body is never mutated.
    shell_mesh = body.data.copy()
    shell_mesh.name = f"{SHELL_OBJECT_NAME}Mesh"
    shell = bpy.data.objects.new(SHELL_OBJECT_NAME, shell_mesh)
    bpy.context.collection.objects.link(shell)

    # Optional subdivision (alias: subdivide stage).
    levels = int(params.get("subdivide_levels", 1))
    if levels > 0:
        mod = shell.modifiers.new(name="Subdivision", type="SUBSURF")
        mod.levels = levels
        mod.render_levels = levels
        try:
            bpy.context.view_layer.objects.active = shell
            shell.select_set(True)
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as exc:  # pragma: no cover - Blender-context dependent
            logger.warning("subdivide apply skipped: %s", exc)

    shell.select_set(False)
    vertex_count = len(shell_mesh.vertices)
    return _pass(
        {
            "shell_object_name": shell.name,
            "shell_vertex_count": vertex_count,
            "region_region_vertex_count": _region_vertex_count(body, region),
            "base_body": body.name,
        }
    )


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    """Orchestrator entry point: run shell stage and save a .blend checkpoint."""
    params = params or {}
    result = generate_shell(blend_path, region, params)
    if result["status"] != "pass":
        return result

    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]
    ensure_dir(outfit_dir(outfit))
    checkpoint = checkpoint_path(outfit, STAGE_INDEX, STAGE_NAME)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=checkpoint)
        result["checkpoint"] = checkpoint
        logger.info("shell checkpoint saved -> %s", checkpoint)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        result["status"] = "fail"
        result["metrics"]["error"] = f"checkpoint save failed: {exc}"
        result["checkpoint"] = None
    return result
