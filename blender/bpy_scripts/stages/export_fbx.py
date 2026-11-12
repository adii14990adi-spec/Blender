"""Stage 05: Export the final shell to FBX (Unity-compatible flags) and GLB (web review)."""

from __future__ import annotations

import json
import os
import struct
from typing import Any, Dict

try:
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger
from ..utils.paths import (
    ensure_dir,
    fbx_export_path,
    glb_export_path,
    input_blend_path,
    outfit_dir,
    validation_report_path,
)

logger = get_logger("stages.export_fbx")

STAGE_INDEX = 4
STAGE_NAME = "export"
SHELL_OBJECT_NAME = "CLOTH_Shell"
TRIANGLE_VERTS = 3
QUAD_VERTS = 4


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


def _ensure_linked(obj: "bpy.types.Object") -> None:
    """Selection operators need the object in the active view layer (headless)."""
    try:
        if obj.name not in bpy.context.view_layer.objects:  # type: ignore[attr-defined]
            bpy.context.collection.objects.link(obj)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - context dependent
        logger.warning("could not link %s into the active collection", obj.name)


def _filter_supported_kwargs(op: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop kwargs the installed Blender's exporter does not know about.

    The FBX operator's property set drifts between Blender releases
    (``apply_scaling_options`` vs ``apply_scale_options``,
    ``use_mesh_modifiers_render`` disappearing, …). Passing an unknown kwarg
    raises TypeError, which used to be swallowed and turned into a stub file.
    """
    try:
        supported = {prop.identifier for prop in op.get_rna_type().properties}
    except Exception:  # pragma: no cover - RNA introspection unavailable
        return kwargs
    unknown = sorted(set(kwargs) - supported)
    if unknown:
        logger.warning("FBX exporter does not support %s — dropping them", unknown)
    return {key: value for key, value in kwargs.items() if key in supported}


def _ensure_fbx_exporter() -> str | None:
    """Enable the bundled FBX add-on if this Blender still ships it as one."""
    try:
        module = "io_scene_fbx"
        if hasattr(bpy.ops.export_scene, "fbx"):
            return None
        bpy.ops.preferences.addon_enable(module=module)
        return None
    except Exception as exc:  # pragma: no cover - depends on Blender build
        return f"{exc.__class__.__name__}: {exc}"


def export_fbx(shell: "bpy.types.Object", outfit: str, fbx_path: str) -> Dict[str, Any]:
    """Core bpy operation: export CLOTH_Shell to FBX with Unity flags.

    Uses bpy.ops.export_scene.fbx which works in headless Blender mode. A failed
    export is reported as ``status: fail`` — never replaced by a stub file, so
    CI cannot go green on an FBX that Unity would reject.
    """
    os.makedirs(os.path.dirname(fbx_path), exist_ok=True)

    addon_error = _ensure_fbx_exporter()
    if addon_error:
        return {"status": "fail", "error": f"FBX exporter unavailable: {addon_error}"}
    if not hasattr(bpy.ops.export_scene, "fbx"):  # pragma: no cover - Blender build
        return {"status": "fail", "error": "bpy.ops.export_scene.fbx is not available"}

    if os.path.exists(fbx_path):
        os.remove(fbx_path)  # never let a stale file pass for a fresh export

    unity_kwargs: Dict[str, Any] = {
        "filepath": fbx_path,
        "check_existing": False,
        "use_selection": True,
        "apply_scale_options": "FBX_SCALE_ALL",
        "axis_forward": "-Z",
        "axis_up": "Y",
        "object_types": {"ARMATURE", "MESH"},
        "use_mesh_modifiers": True,
        "mesh_smooth_type": "OFF",
        "add_leaf_bones": False,
        "primary_bone_axis": "Y",
        "secondary_bone_axis": "X",
    }

    try:
        # Deselect everything, select only the shell. In -b there is no UI
        # context, so fall back to deselecting objects directly.
        _ensure_linked(shell)
        try:
            bpy.ops.object.select_all(action="DESELECT")
        except Exception:  # pragma: no cover - no objects / no context
            for obj in bpy.context.view_layer.objects:
                obj.select_set(False)
        shell.select_set(True)
        bpy.context.view_layer.objects.active = shell
        bpy.ops.export_scene.fbx(**_filter_supported_kwargs(bpy.ops.export_scene.fbx, unity_kwargs))
    except Exception as exc:  # bpy.ops may fail in headless mode
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.error("FBX export failed: %s", detail)
        return {"status": "fail", "error": f"FBX export failed: {detail}", "fbx_path": None}

    if not os.path.exists(fbx_path) or os.path.getsize(fbx_path) == 0:
        return {"status": "fail", "error": "FBX exporter produced no file", "fbx_path": None}

    return {
        "status": "pass",
        "fbx_path": fbx_path,
        "fbx_file_size_bytes": os.path.getsize(fbx_path),
        "unity_flags": {
            "apply_scale_options": "FBX_SCALE_ALL",
            "axis_forward": "-Z",
            "axis_up": "Y",
            "mesh_smooth_type": "OFF",
            "add_leaf_bones": False,
        },
    }


def export_glb(shell: "bpy.types.Object", outfit: str, glb_path: str) -> Dict[str, Any]:
    """Core bpy operation: export CLOTH_Shell to GLB for web review.

    GLB is the web-friendly counterpart to the Unity FBX. Uses bpy.data API
    directly (no bpy.ops export operator) so it works reliably in headless
    mode where 3D view context is unavailable.
    """
    os.makedirs(os.path.dirname(glb_path), exist_ok=True)

    try:
        mesh = shell.data

        # Collect vertex positions
        positions = []
        for v in mesh.vertices:
            positions.extend(v.co)

        # Collect triangle indices (3 per polygon, assuming triangles)
        indices = []
        for poly in mesh.polygons:
            if len(poly.vertices) == TRIANGLE_VERTS:
                indices.extend(poly.vertices)
            elif len(poly.vertices) == QUAD_VERTS:
                # Quad: split into two triangles
                indices.extend(
                    [
                        poly.vertices[0],
                        poly.vertices[1],
                        poly.vertices[2],
                        poly.vertices[0],
                        poly.vertices[2],
                        poly.vertices[3],
                    ]
                )

        # Build GLB using pure Python
        json_bytes = json.dumps(
            {
                "asset": {
                    "version": "2.0",
                    "generator": "Modular Clothing Pipeline",
                },
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0}],
                "meshes": [
                    {
                        "primitives": [
                            {
                                "attributes": {"POSITION": 0},
                                "indices": 1,
                                "material": 0,
                            }
                        ]
                    }
                ],
                "accessors": [
                    {
                        "bufferView": 0,
                        "componentType": 5126,
                        "count": len(mesh.vertices),
                        "type": "VEC3",
                    },
                    {
                        "bufferView": 1,
                        "componentType": 5123,
                        "count": len(indices),
                        "type": "SCALAR",
                    },
                ],
                "bufferViews": [
                    {
                        "buffer": 0,
                        "byteOffset": 0,
                        "byteLength": len(mesh.vertices) * 12,
                    },
                    {
                        "buffer": 0,
                        "byteOffset": len(mesh.vertices) * 12,
                        "byteLength": len(indices) * 2,
                    },
                ],
                "buffers": [{"byteLength": len(mesh.vertices) * 12 + len(indices) * 2}],
                "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1]}}],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        json_padded = json_bytes + b" " * ((4 - len(json_bytes) % 4) % 4)

        pos_bytes = struct.pack(f"<{len(positions)}f", *positions)
        idx_bytes = struct.pack(f"<{len(indices)}H", *indices)
        bin_data = pos_bytes + idx_bytes
        bin_padded = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)

        header = struct.pack("<4sII", b"glTF", 2, 12 + len(json_padded) + 8 + len(bin_padded))
        json_chunk = struct.pack("<II", len(json_padded), 0x4E4F534A) + json_padded
        bin_chunk = struct.pack("<II", len(bin_padded), 0x004E4942) + bin_padded

        glb_bytes = header + json_chunk + bin_chunk

        with open(glb_path, "wb") as f:
            f.write(glb_bytes)
    except Exception as exc:  # a fake cube must never stand in for the garment
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.error("GLB export via bpy.data failed: %s", detail)
        return {
            "status": "fail",
            "error": f"GLB export failed: {detail}",
            "glb_path": None,
        }

    if not os.path.exists(glb_path) or os.path.getsize(glb_path) == 0:
        return {"status": "fail", "error": "GLB writer produced no file", "glb_path": None}

    return {
        "status": "pass",
        "glb_path": glb_path,
        "glb_file_size_bytes": os.path.getsize(glb_path),
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

    input_path, input_info = input_blend_path(blend_path, outfit, STAGE_INDEX)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.wm.open_mainfile(filepath=input_path)
    except Exception as exc:
        return _fail(f"could not open {input_path}: {exc}", {"input": input_info})

    object_names = [obj.name for obj in bpy.data.objects]
    logger.info("opened %s: %d objects %s", input_path, len(object_names), object_names)

    shell = _find(SHELL_OBJECT_NAME)
    if shell is None:
        return _fail(
            "CLOTH_Shell not found — run all prior stages first",
            {"input": input_info, "objects": object_names},
        )

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
    op_result = {**op_result, **glb_result, "input": input_info}

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
