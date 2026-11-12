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

from ...utils.logging import get_logger
from ...utils.paths import ASSETS_ROOT, checkpoint_path, ensure_dir, outfit_dir

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
    """Core bpy operation: smart UV project + assign a PBR-ish basecolor material."""
    if shell.type != "MESH":
        return {"status": "fail", "error": "shell must be a mesh object"}

    bpy.context.view_layer.objects.active = shell
    shell.select_set(True)

    # Smart UV Project.
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=66, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as exc:  # pragma: no cover - Blender-context dependent
        return {"status": "fail", "error": f"uv unwrap failed: {exc}"}

    # Count UV islands from the UV layer.
    uv_layer = shell.data.uv_layers.active
    island_count = len(uv_layer.data) if uv_layer is not None else 0

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

    shell.select_set(False)
    return {
        "status": "pass",
        "uv_islands": island_count,
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

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    shell = _find(SHELL_OBJECT_NAME)
    if shell is None:
        return _fail("CLOTH_Shell not found — run shell/shrinkwrap/weights first")

    op_result = unwrap_and_material(shell, outfit, texture_path=texture_path)
    if op_result.get("status") != "pass":
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": op_result,
            "checkpoint": None,
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
