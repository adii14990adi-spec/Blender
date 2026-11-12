"""Stage 04 — export and verify FBX + GLB.

Replaces the hand-written GLB writer (which emitted only positions and
triangles: no UVs, normals, textures, skin, armature or animation) with
Blender's native glTF 2.0 exporter, and fixes the FBX selection bug where only
``CLOTH_Shell`` was selected so the armature never reached Unity.

Both files are then parsed and validated on disk:

* GLB: container, accessors, skin, joints/weights, UVs, normals, materials and
  embedded images (:mod:`bpy_scripts.utils.glb_inspect`);
* FBX: binary node tree, ``Geometry`` + UV/normal layers, ``Skin`` deformer and
  clusters, bone models (:mod:`bpy_scripts.utils.fbx_inspect`);
* optionally the Khronos glTF Validator, when ``node`` and the npm package are
  available.

An export that cannot be verified fails the stage — no placeholder asset is ever
written in its place.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, load_outfit, resolve_variant
from ..utils.bpy_helpers import (
    find_armature,
    find_mesh_object,
    mesh_stats,
    open_blend,
    select_only,
)
from ..utils.fbx_inspect import inspect_fbx
from ..utils.glb_inspect import inspect_glb
from ..utils.logging import get_logger
from ..utils.paths import (
    REPO_ROOT,
    checkpoint_for_stage,
    ensure_dir,
    export_report_path,
    fbx_export_path,
    glb_export_path,
    outfit_dir,
)

logger = get_logger("stages.export")

STAGE_INDEX = 4
STAGE_NAME = "export"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
MIN_EXPORT_BYTES = 4096


def _fail(error: str, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "fail",
        "stage": STAGE_NAME,
        "metrics": metrics or {},
        "checkpoint": None,
        "error": error,
    }


def _filter_kwargs(op, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop kwargs this Blender build's exporter does not expose."""
    try:
        supported = {prop.identifier for prop in op.get_rna_type().properties}
    except Exception:  # pragma: no cover
        return kwargs
    unknown = sorted(set(kwargs) - supported)
    if unknown:
        logger.warning("exporter does not support %s — dropping", unknown)
    return {key: value for key, value in kwargs.items() if key in supported}


def _remove_stale(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def repair_zero_tangents(glb_path: str, max_ratio: float = 0.01) -> Dict[str, Any]:
    """Give zero-length tangents a usable value and report how many were fixed.

    Blender's tangent builder emits ``(0, 0, 0, 0)`` for triangles whose UVs
    collapse - typically sub-millimetre slivers on cut boundaries. A zero
    tangent is unusable data (the Khronos validator rejects it as
    ``ACCESSOR_VECTOR3_NON_UNIT``), so the affected vertices get a valid unit
    tangent. The count is reported and a large number fails the export: that
    would mean the UV layout, not a handful of slivers, is broken.
    """
    import struct

    from ..utils.glb_inspect import parse_glb

    parsed = parse_glb(glb_path)
    gltf, binary = parsed["json"], parsed["bin"]
    if not binary:
        return {"repaired": 0, "checked": 0}

    view_bytes = bytearray(binary)
    repaired = 0
    checked = 0
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            tangent_index = primitive.get("attributes", {}).get("TANGENT")
            if tangent_index is None:
                continue
            accessor = gltf["accessors"][tangent_index]
            view = gltf["bufferViews"][accessor["bufferView"]]
            count = accessor["count"]
            checked += count
            base = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            stride = view.get("byteStride") or 16
            for element in range(count):
                offset = base + element * stride
                values = struct.unpack_from("<4f", view_bytes, offset)
                if abs(values[0]) + abs(values[1]) + abs(values[2]) < 1e-6:
                    struct.pack_into("<4f", view_bytes, offset, 1.0, 0.0, 0.0, 1.0)
                    repaired += 1

    if repaired:
        with open(glb_path, "rb") as handle:
            data = bytearray(handle.read())
        # The binary chunk is byte-identical in size, so patch it in place.
        start = data.find(bytes(binary))
        if start < 0:  # pragma: no cover - defensive
            raise RuntimeError("could not locate the binary chunk to patch tangents")
        data[start : start + len(binary)] = view_bytes
        with open(glb_path, "wb") as handle:
            handle.write(data)
        logger.warning("repaired %d zero-length tangents in %s", repaired, glb_path)

    ratio = repaired / checked if checked else 0.0
    return {
        "repaired": repaired,
        "checked": checked,
        "ratio": round(ratio, 6),
        "ok": ratio <= max_ratio,
    }


def export_glb(garment, armature, glb_path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    _remove_stale(glb_path)
    selection: List[Any] = [garment]
    if armature is not None:
        selection.append(armature)
    select_only(selection, active=garment)

    kwargs = {
        "filepath": glb_path,
        "check_existing": False,
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": True,
        "export_texcoords": True,
        "export_normals": True,
        # A normal map without tangents forces the runtime to generate a
        # tangent space, which the Khronos validator flags as non-portable.
        "export_tangents": True,
        "export_materials": "EXPORT",
        "export_image_format": "AUTO",
        "export_skins": True,
        "export_influence_nb": 4,
        "export_all_influences": False,
        "export_animations": bool(params.get("export_animations", True)),
        "export_animation_mode": "ACTIONS",
        "export_nla_strips": True,
        "export_bake_animation": False,
        "export_optimize_animation_size": False,
        "export_yup": True,
        "export_cameras": False,
        "export_lights": False,
        "export_extras": True,
        "export_def_bones": False,
        "export_anim_single_armature": True,
        # Keeps the skinned mesh node at the scene root (no parent transform
        # silently ignored by renderers, flagged by the validator).
        "export_armature_object_remove": True,
        "export_rest_position_armature": True,
        "export_leaf_bone": False,
        "export_morph": False,
        "export_original_specular": False,
    }
    try:
        bpy.ops.export_scene.gltf(**_filter_kwargs(bpy.ops.export_scene.gltf, kwargs))
    except Exception as exc:
        return {"status": "fail", "error": f"glTF export raised {exc.__class__.__name__}: {exc}"}
    if not os.path.exists(glb_path) or os.path.getsize(glb_path) < MIN_EXPORT_BYTES:
        return {"status": "fail", "error": "glTF exporter produced no usable file"}
    return {"status": "pass", "path": glb_path, "bytes": os.path.getsize(glb_path)}


def export_fbx(garment, armature, fbx_path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    _remove_stale(fbx_path)
    if not hasattr(bpy.ops.export_scene, "fbx"):
        return {
            "status": "fail",
            "error": "bpy.ops.export_scene.fbx is unavailable in this Blender build",
        }
    selection: List[Any] = [garment]
    if armature is not None:
        selection.append(armature)
    # The armature MUST be selected: the old bug selected only the shell, so the
    # FBX had no skeleton even though ARMATURE was in object_types.
    select_only(selection, active=armature or garment)

    kwargs = {
        "filepath": fbx_path,
        "check_existing": False,
        "use_selection": True,
        "object_types": {"ARMATURE", "MESH"},
        "apply_scale_options": "FBX_SCALE_ALL",
        "axis_forward": "-Z",
        "axis_up": "Y",
        "use_mesh_modifiers": True,
        "mesh_smooth_type": "FACE",
        "use_tspace": False,
        "add_leaf_bones": False,
        "use_armature_deform_only": True,
        "primary_bone_axis": "Y",
        "secondary_bone_axis": "X",
        "bake_anim": bool(params.get("export_animations", True)),
        "bake_anim_use_all_actions": False,
        "bake_anim_force_startend_keying": True,
        "bake_anim_step": 1.0,
        "bake_anim_simplify_factor": 0.0,
        "path_mode": "COPY",
        "embed_textures": True,
        "use_custom_props": True,
        "use_metadata": True,
    }
    try:
        bpy.ops.export_scene.fbx(**_filter_kwargs(bpy.ops.export_scene.fbx, kwargs))
    except Exception as exc:
        return {"status": "fail", "error": f"FBX export raised {exc.__class__.__name__}: {exc}"}
    if not os.path.exists(fbx_path) or os.path.getsize(fbx_path) < MIN_EXPORT_BYTES:
        return {"status": "fail", "error": "FBX exporter produced no usable file"}
    return {"status": "pass", "path": fbx_path, "bytes": os.path.getsize(fbx_path)}


def run_khronos_validator(glb_path: str) -> Dict[str, Any]:
    """Run the Khronos glTF Validator when node + the npm package are present."""
    node = shutil.which("node")
    script = os.path.join(REPO_ROOT, "tools", "gltf_validate.js")
    if not node or not os.path.exists(script):
        return {"status": "skipped", "reason": "node or tools/gltf_validate.js unavailable"}
    probe = [node, "-e", "require.resolve('gltf-validator')"]
    if subprocess.run(probe, cwd=REPO_ROOT, capture_output=True, check=False).returncode != 0:
        return {
            "status": "skipped",
            "reason": "npm package `gltf-validator` is not installed (npm install gltf-validator)",
        }
    try:
        completed = subprocess.run(
            [node, script, glb_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "errors": ["Khronos validator timed out"]}
    payload: Dict[str, Any] = {}
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            payload = {"raw": completed.stdout[-2000:]}
    payload["status"] = (
        "pass" if completed.returncode == 0 and not payload.get("errors") else "fail"
    )
    if completed.returncode != 0 and "errors" not in payload:
        payload["errors"] = [completed.stderr[-2000:] or "validator exited non-zero"]
    return payload


def _triangulate(obj) -> Dict[str, Any]:
    """Triangulate the garment and preserve its UVs/vertex groups.

    The glTF exporter cannot build a tangent space from n-gons ("Could not
    calculate tangents. Please try to triangulate the mesh first"), which makes
    the Khronos validator flag the normal map as runtime-generated. Game meshes
    are triangles anyway, so the exported asset - and its .blend - are
    triangulated once, here.
    """
    import bmesh

    mesh = obj.data
    quads_and_ngons = sum(1 for polygon in mesh.polygons if len(polygon.vertices) > 3)
    if not quads_and_ngons:
        return {"triangulated": 0, "faces_before": len(mesh.polygons)}

    uv_layers = [layer.name for layer in mesh.uv_layers]
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.to_mesh(mesh)
    except Exception as exc:  # pragma: no cover - defensive
        return {"failed": f"{exc.__class__.__name__}: {exc}"}
    finally:
        bm.free()
    mesh.update()
    mesh.uv_layers.active = mesh.uv_layers.get(uv_layers[0]) if uv_layers else None
    return {
        "triangulated": quads_and_ngons,
        "faces_before": len(mesh.polygons) - quads_and_ngons,
        "faces_after": len(mesh.polygons),
        "uv_layers": [layer.name for layer in mesh.uv_layers],
        "vertex_groups": len(obj.vertex_groups),
    }


def export_checkpoint(
    blend_path: str, region: str, params: Dict[str, Any], outfit: str
) -> Dict[str, Any]:
    if not _HAS_BPY:
        return _fail("bpy is not available: run inside Blender")
    try:
        variant = resolve_variant(params.get("variant"), params.get("_body_manifest"))
        settings = load_outfit(outfit, params.get("_outfit_manifest"))
    except ConfigError as exc:
        return _fail(str(exc))

    open_blend(blend_path)
    garment = find_mesh_object(GARMENT_OBJECT_NAME)
    if garment is None:
        return _fail(
            f"{GARMENT_OBJECT_NAME!r} not found in {blend_path}",
            {"objects": [obj.name for obj in bpy.data.objects]},
        )
    armature = find_armature(params.get("armature_object") or variant.armature_object)
    require_skin = bool(params.get("require_skin", True))
    if armature is None and require_skin:
        return _fail(
            "no armature in the scene; a clothing export without a skeleton is not game-ready. "
            "Run the `weights` stage (it builds/attaches the rig) or pass "
            '{"require_skin": false} for a static prop export.'
        )

    # Make sure the garment is actually bound before we claim it is skinned.
    if armature is not None and require_skin:
        bound = any(
            modifier.type == "ARMATURE" and modifier.object == armature
            for modifier in garment.modifiers
        )
        if not bound:
            return _fail(
                f"{GARMENT_OBJECT_NAME} has no Armature modifier pointing at {armature.name}; "
                "run the `weights` stage"
            )

    triangulated = _triangulate(garment)
    if triangulated.get("failed"):
        return _fail(f"could not triangulate the garment before export: {triangulated['failed']}")

    variant_name = variant.name
    fbx_path = fbx_export_path(outfit, variant_name)
    glb_path = glb_export_path(outfit, variant_name)
    ensure_dir(outfit_dir(outfit, variant_name))

    fbx_result = export_fbx(garment, armature, fbx_path, params)
    if fbx_result["status"] != "pass":
        return _fail(f"FBX export failed: {fbx_result.get('error')}", {"fbx": fbx_result})

    glb_result = export_glb(garment, armature, glb_path, params)
    if glb_result["status"] != "pass":
        return _fail(
            f"GLB export failed: {glb_result.get('error')}", {"fbx": fbx_result, "glb": glb_result}
        )

    tangent_repair = repair_zero_tangents(glb_path)
    if not tangent_repair["ok"]:
        return _fail(
            f"{tangent_repair['repaired']} of {tangent_repair['checked']} tangents "
            "had zero length; the UV layout is broken, not just a few slivers",
            {"fbx": fbx_result, "glb": glb_result, "tangents": tangent_repair},
        )

    glb_validation = inspect_glb(
        glb_path,
        expect_skin=require_skin,
        expect_textures=bool(params.get("require_textures", True)),
        expect_normals=True,
        expect_uvs=True,
        max_influences=int(params.get("weight_limit", 4)),
    )
    fbx_validation = inspect_fbx(
        fbx_path,
        expect_skin=require_skin,
        expect_uvs=True,
        expect_normals=True,
        expect_materials=True,
        min_bones=int(params.get("min_bones", 4)) if require_skin else 0,
    )
    khronos = run_khronos_validator(glb_path)
    khronos_failure = khronos.get("status") == "fail"

    metrics: Dict[str, Any] = {
        "exports": {
            "fbx": {**fbx_result, "path": fbx_path},
            "glb": {**glb_result, "path": glb_path},
        },
        "glb_validation": glb_validation,
        "fbx_validation": fbx_validation,
        "khronos_validator": khronos,
        "tangents": tangent_repair,
        "triangulation": triangulated,
        "garment_stats": mesh_stats(garment),
        "armature": armature.name if armature else None,
        "variant": variant.name,
        "material": settings.get("material", {}).get("name"),
    }

    problems: List[str] = []
    if not glb_validation["ok"]:
        problems += [f"GLB: {error}" for error in glb_validation["errors"]]
    if not fbx_validation["ok"]:
        problems += [f"FBX: {error}" for error in fbx_validation["errors"]]
    if khronos_failure:
        problems += [f"Khronos: {error}" for error in khronos.get("errors", [])]

    report = {
        "outfit": outfit,
        "variant": variant.name,
        "stage": STAGE_NAME,
        "status": "fail" if problems else "pass",
        "artifacts": {"fbx": fbx_path, "glb": glb_path},
        "problems": problems,
        "metrics": metrics,
    }
    try:
        with open(export_report_path(outfit, variant_name), "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    except OSError as exc:  # pragma: no cover
        logger.warning("could not write export report: %s", exc)

    if problems:
        metrics["problems"] = problems
        return _fail("export verification failed — " + "; ".join(problems[:4]), metrics)
    return {"status": "pass", "stage": STAGE_NAME, "metrics": metrics, "checkpoint": None}


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
    outfit: str | None = None,
) -> Dict[str, Any]:
    params = dict(params or {})
    outfit = outfit or params.get("outfit") or os.path.splitext(os.path.basename(blend_path))[0]
    params.setdefault("outfit", outfit)
    variant = str(params.get("variant") or "male")

    result = export_checkpoint(blend_path, region, params, outfit)
    if result["status"] != "pass":
        return result

    # Save the final checkpoint from the validated scene (never before export).
    checkpoint = os.path.join(outfit_dir(outfit, variant), "04_final.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=checkpoint, compress=True, check_existing=False)
        result["checkpoint"] = checkpoint
    except Exception as exc:  # pragma: no cover
        logger.warning("final checkpoint save skipped: %s", exc)
        result["checkpoint"] = checkpoint_for_stage(outfit, "uv_material", variant)

    result["metrics"]["artifacts"] = {
        "fbx": fbx_export_path(outfit, variant),
        "glb": glb_export_path(outfit, variant),
        "final_blend": result["checkpoint"],
    }
    logger.info(
        "exported %s (%s bytes) and %s (%s bytes)",
        fbx_export_path(outfit, variant),
        result["metrics"]["exports"]["fbx"].get("bytes"),
        glb_export_path(outfit, variant),
        result["metrics"]["exports"]["glb"].get("bytes"),
    )
    return result
