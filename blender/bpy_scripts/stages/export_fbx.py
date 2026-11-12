"""Stage 05: Export the final shell to FBX (Unity-compatible flags) and GLB (web review)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

try:
    import bpy
    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ...utils.logging import get_logger
from ...utils.paths import (
    ensure_dir,
    fbx_export_path,
    glb_export_path,
    outfit_dir,
    validation_report_path,
)

logger = get_logger("stages.export_fbx")

STAGE_INDEX = 4
STAGE_NAME = "export"
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


def export_fbx(shell: "bpy.types.Object", outfit: str, fbx_path: str) -> Dict[str, Any]:
    """Core bpy operation: export CLOTH_Shell to FBX with Unity flags."""
    # Deselect everything, select only the shell.
    bpy.ops.object.select_all(action="DESELECT")
    shell.select_set(True)
    bpy.context.view_layer.objects.active = shell

    os.makedirs(os.path.dirname(fbx_path), exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        check_existing=True,
        use_selection=True,
        apply_modifiers=True,
        apply_scaling_options="FBX_SCALE_ALL",
        axis_forward="-Z",
        axis_up="Y",
        object_types={"ARMATURE", "MESH"},
        use_mesh_modifiers=True,
        use_mesh_modifiers_render=True,
        mesh_smooth_type="OFF",
        add_leaf_bones=False,
        primary_bone_axis="Y",
        secondary_bone_axis="X",
    )

    file_size = os.path.getsize(fbx_path) if os.path.exists(fbx_path) else 0
    return {
        "status": "pass",
        "fbx_path": fbx_path,
        "fbx_file_size_bytes": file_size,
        "unity_flags": {
            "apply_modifiers": True,
            "axis_forward": "-Z",
            "axis_up": "Y",
            "mesh_smooth_type": "OFF",
            "add_leaf_bones": False,
        },
    }


def export_glb(shell: "bpy.types.Object", outfit: str, glb_path: str) -> Dict[str, Any]:
    """Core bpy operation: export CLOTH_Shell to GLB for web review.

    GLB is the web-friendly counterpart to the Unity FBX. Animations are
    exported so rigging/clipping can be validated in a browser viewer.
    """
    # Deselect everything, select only the shell.
    bpy.ops.object.select_all(action="DESELECT")
    shell.select_set(True)
    bpy.context.view_layer.objects.active = shell

    os.makedirs(os.path.dirname(glb_path), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=glb_path,
        check_existing=True,
        use_selection=True,
        export_format="GLB",
        export_apply=True,
        export_animations=True,
        export_yup=True,
    )

    file_size = os.path.getsize(glb_path) if os.path.exists(glb_path) else 0
    return {
        "status": "pass",
        "glb_path": glb_path,
        "glb_file_size_bytes": file_size,
        "web_flags": {
            "export_animations": True,
            "export_apply": True,
            "axis_up": "Y",
        },
    }


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    """Orchestrator entry point: export FBX and GLB, write validation_report.json."""
    params = params or {}
    outfit = outfit or os.path.splitext(os.path.basename(blend_path))[0]
    fbx_path = fbx_export_path(outfit)
    glb_path = glb_export_path(outfit)
    report_path = validation_report_path(outfit)

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
    if shell is None:
        return _fail("CLOTH_Shell not found — run all prior stages first")

    # FBX export for Unity.
    op_result = export_fbx(shell, outfit, fbx_path)
    if op_result.get("status") != "pass":
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": op_result,
            "checkpoint": None,
        }

    # GLB export for web review.
    glb_result = export_glb(shell, outfit, glb_path)
    if glb_result.get("status") != "pass":
        return {
            "status": "fail",
            "stage": STAGE_NAME,
            "metrics": {**op_result, **glb_result, "error": "glb export failed"},
            "checkpoint": None,
        }

    # Merge FBX + GLB metrics.
    op_result = {**op_result, **glb_result}

    # Final checkpoint + validation report.
    ensure_dir(outfit_dir(outfit))
    checkpoint = fbx_path.replace(".fbx", "_final.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=checkpoint)
    except Exception as exc:  # pragma: no cover
        logger.warning("final checkpoint save skipped: %s", exc)
        checkpoint = None

    report = {
        "outfit": outfit,
        "stage": STAGE_NAME,
        "status": "pass",
        "metrics": op_result,
        "checkpoint": checkpoint,
    }
    try:
        with open(report_path, "w") as fh:
            json.dump(report, fh, indent=2)
    except Exception as exc:  # pragma: no cover
        logger.warning("validation report write skipped: %s", exc)

    return {
        "status": "pass",
        "stage": STAGE_NAME,
        "metrics": op_result,
        "checkpoint": checkpoint,
    }
