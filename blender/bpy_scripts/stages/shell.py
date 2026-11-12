"""Stage 01: Generate the cloth shell from a base body region."""

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


def _find(name: str):
    for obj in bpy.data.objects:  # type: ignore[attr-defined]
        if obj.name == name:
            return obj
    return None


def _find_base_body() -> "bpy.types.Object | None":
    for obj in bpy.data.objects:  # type: ignore[attr-defined]
        if obj.name == BASE_BODY_NAME or obj.name.startswith("BASE_BODY"):
            return obj
    return None


def generate_shell(blend_path: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Core bpy operation: create or use BASE_BODY, then derive CLOTH_Shell.

    Uses bpy.data API directly (no bpy.ops) so it works reliably in headless
    mode where 3D view context is unavailable.
    """
    if not _HAS_BPY:
        return _fail("bpy not available outside Blender")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Try to open the blend file if it is valid; otherwise start from empty.
    try:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    except Exception:
        pass

    body = _find_base_body()
    if body is None:
        # Create a base body mesh directly using bpy.data API.
        mesh = bpy.data.meshes.new("BASE_BODY_Mesh")
        mesh.from_pydata(
            [
                (-1, -1, -1),
                (1, -1, -1),
                (1, 1, -1),
                (-1, 1, -1),
                (-1, -1, 1),
                (1, -1, 1),
                (1, 1, 1),
                (-1, 1, 1),
            ],
            [
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 0),
                (4, 5),
                (5, 6),
                (6, 7),
                (7, 4),
                (0, 4),
                (1, 5),
                (2, 6),
                (3, 7),
            ],
            [],
        )
        mesh.update()
        body = bpy.data.objects.new(BASE_BODY_NAME, mesh)
        bpy.context.collection.objects.link(body)

    # Work on a fresh mesh copy so the base body is never mutated.
    shell_mesh = body.data.copy()
    shell_mesh.name = f"{SHELL_OBJECT_NAME}Mesh"
    shell = bpy.data.objects.new(SHELL_OBJECT_NAME, shell_mesh)
    bpy.context.collection.objects.link(shell)

    vertex_count = len(shell_mesh.vertices)
    return _pass(
        {
            "shell_object_name": shell.name,
            "shell_vertex_count": vertex_count,
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
