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

from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, input_blend_path, outfit_dir

logger = get_logger("stages.shell")

STAGE_INDEX = 0
STAGE_NAME = "shell"
SHELL_OBJECT_NAME = "CLOTH_Shell"
BASE_BODY_NAME = "BASE_BODY"
REGION_GROUPS = {"UPPER_BODY": "UPPER_BODY", "LOWER_BODY": "LOWER_BODY", "HEAD": "HEAD"}


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


def _make_blockout_body(region: str = "UPPER_BODY"):
    """Build a stand-in body cube (explicit opt-in only).

    This keeps CI runnable while the real ``BASE_BODY.blend`` is still a
    committed placeholder, but it is never used implicitly: a green build on a
    blockout must be an explicit choice, not a silent substitution.
    """
    mesh = bpy.data.meshes.new("BASE_BODY_Mesh")
    verts = [
        (-1.0, -1.0, -1.0),
        (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, -1.0, 1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
    ]
    faces = [
        (0, 3, 2, 1),  # bottom
        (4, 5, 6, 7),  # top
        (0, 1, 5, 4),  # front
        (1, 2, 6, 5),  # right
        (2, 3, 7, 6),  # back
        (3, 0, 4, 7),  # left
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    body = bpy.data.objects.new(BASE_BODY_NAME, mesh)
    bpy.context.collection.objects.link(body)

    group = body.vertex_groups.new(name=REGION_GROUPS.get(region, region))
    group.add(list(range(len(verts))), 1.0, "REPLACE")
    return body


def generate_shell(blend_path: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Core bpy operation: create or use BASE_BODY, then derive CLOTH_Shell.

    Uses bpy.data API directly (no bpy.ops) so it works reliably in headless
    mode where 3D view context is unavailable.
    """
    if not _HAS_BPY:
        return _fail("bpy not available outside Blender")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.error("could not open %s (%s)", blend_path, detail)
        return _fail(f"could not open {blend_path}: {detail}")

    body = _find_base_body()
    body_source = "base_blend"
    if body is None:
        if not params.get("allow_blockout_body"):
            return _fail(
                "BASE_BODY not found in blend — commit a real base body, or run "
                'with params {"allow_blockout_body": true} to use the blockout cube',
                {"blend": blend_path},
            )
        logger.warning("BASE_BODY missing: building blockout body (explicitly allowed)")
        body = _make_blockout_body(region)
        body_source = "blockout"

    # Work on a fresh mesh copy so the base body is never mutated.
    shell_mesh = body.data.copy()
    shell_mesh.name = f"{SHELL_OBJECT_NAME}Mesh"
    shell = bpy.data.objects.new(SHELL_OBJECT_NAME, shell_mesh)
    bpy.context.collection.objects.link(shell)

    vertex_count = len(shell_mesh.vertices)
    face_count = len(shell_mesh.polygons)
    if face_count == 0:
        return _fail(
            "derived shell has no faces — the base body mesh is wireframe/empty",
            {"base_body": body.name, "shell_vertex_count": vertex_count, "shell_face_count": 0},
        )

    return _pass(
        {
            "shell_object_name": shell.name,
            "shell_vertex_count": vertex_count,
            "shell_face_count": face_count,
            "base_body": body.name,
            "base_body_source": body_source,
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
    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]
    input_path, input_info = input_blend_path(blend_path, outfit, STAGE_INDEX)

    result = generate_shell(input_path, region, params)
    if result["status"] != "pass":
        result["metrics"].setdefault("input", input_info)
        return result

    result["metrics"]["input"] = input_info
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
