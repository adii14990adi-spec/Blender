"""Stage 04: UV unwrap + material assignment (material is an alias of uv)."""

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
from ..utils.paths import ASSETS_ROOT, checkpoint_path, ensure_dir, input_blend_path, outfit_dir

logger = get_logger("stages.uv_material")

STAGE_INDEX = 3
STAGE_NAME = "uv"
SHELL_OBJECT_NAME = "CLOTH_Shell"


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


def unwrap_and_material(
    shell: "bpy.types.Object",
    outfit: str,
    texture_path: str | None = None,
) -> Dict[str, Any]:
    """Core bpy operation: create UVs + assign a basecolor material.

    Uses bpy.data API directly (no bpy.ops mode_set / smart_project) so it
    works reliably in headless mode where 3D view context is unavailable.
    """
    if shell.type != "MESH":
        return {"status": "fail", "error": "shell must be a mesh object"}

    # Create a simple UV map (box projection) using bpy.data API.
    mesh = shell.data
    uv_layer = mesh.uv_layers.new(name="UVMap")
    if len(mesh.uv_layers) > 1:
        mesh.uv_layers.active = uv_layer

    # Assign simple UVs: project vertices onto XY plane.
    for loop in mesh.loops:
        vert = mesh.vertices[loop.vertex_index]
        world_pos = shell.matrix_world @ vert.co
        # Simple planar projection: use X and Y as UV coordinates.
        uv_layer.data[loop.index].uv = (world_pos.x, world_pos.y)

    mesh.update()

    # Material.
    mat_name = f"MAT_Cloth_{outfit}"
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")

    texture_loaded = False
    if texture_path and os.path.exists(texture_path) and bsdf is not None:
        try:
            tex_node = nodes.new(type="ShaderNodeTexImage")
            tex_node.image = bpy.data.images.load(texture_path)
            tex_node.name = f"Tex_{outfit}_BaseColor"
            mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            texture_loaded = True
        except Exception as exc:  # pragma: no cover
            logger.warning("texture load failed: %s", exc)

    # Assign material to shell.
    if shell.data.materials:
        shell.data.materials[0] = mat
    else:
        shell.data.materials.append(mat)

    return {
        "status": "pass",
        "uv_islands": len(mesh.uv_layers.active.data) if mesh.uv_layers.active else 0,
        "material_name": mat.name,
        "texture_loaded": texture_loaded,
    }


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    """Orchestrator entry point: run uv/material and save a .blend checkpoint."""
    params = params or {}
    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]

    # Default texture path: assets/textures/<outfit>_basecolor.png
    texture_path = params.get(
        "texture_path",
        os.path.join(ASSETS_ROOT, "textures", f"{outfit}_basecolor.png"),
    )

    if not _HAS_BPY:
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {"error": "bpy not available outside Blender"},
            "checkpoint": None,
        }

    input_path, input_info = input_blend_path(blend_path, outfit, STAGE_INDEX)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.wm.open_mainfile(filepath=input_path)
    except Exception as exc:
        return _fail(f"could not open {input_path}: {exc}", {"input": input_info})

    shell = _find(SHELL_OBJECT_NAME)
    if shell is None:
        return _fail(
            "CLOTH_Shell not found — run shell/shrinkwrap/weights first",
            {"input": input_info},
        )

    op_result = {
        **unwrap_and_material(shell, outfit, texture_path=texture_path),
        "input": input_info,
    }

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
