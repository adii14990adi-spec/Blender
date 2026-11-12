"""Stage 03 — real UV unwrap and PBR material (single execution).

Fixes:

* duplicate UV layers / duplicate material applications (the old pipeline ran
  ``uv`` *and* ``material``, both pointing at this module);
* the XY planar projection that produced overlapping UVs and unusable
  texturing: replaced with a Smart UV Project unwrap, verified for coverage,
  flipped/zero-area UVs and NaN coordinates, with an angle-based unwrap
  fallback;
* texture-less materials: the material graph always carries base colour,
  normal, roughness and metallic maps that are packed into the .blend.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import (
    ConfigError,
    load_outfit,
    material_spec,
    outfit_details,
    outfit_pieces,
    resolve_variant,
)
from ..utils.bpy_helpers import (
    PipelineError,
    find_mesh_object,
    mesh_stats,
    open_blend,
    save_blend,
    set_active,
)
from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, outfit_dir
from ..utils.textures import (
    build_material,
    build_material_nodes,
    pack_images,
    resolve_textures,
    verify_texture_set,
)

logger = get_logger("stages.uv_material")

STAGE_INDEX = 3
STAGE_NAME = "uv_material"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
PIECE_META_KEY = "garment_pieces"
DETAIL_META_KEY = "garment_details"
UV_LAYER_NAME = "UVMap"
MIN_UV_COVERAGE = 0.02


def _fail(error: str, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "fail",
        "stage": STAGE_NAME,
        "metrics": metrics or {},
        "checkpoint": None,
        "error": error,
    }


def _pass(metrics: Dict[str, Any], checkpoint: str | None = None) -> Dict[str, Any]:
    return {"status": "pass", "stage": STAGE_NAME, "metrics": metrics, "checkpoint": checkpoint}


def _purge_uv_layers(mesh) -> int:
    """Remove every UV layer so a re-run can never stack duplicates."""
    removed = 0
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
        removed += 1
    return removed


def _uv_report(mesh) -> Dict[str, Any]:
    layer = mesh.uv_layers.active
    if layer is None:
        return {"has_uv": False, "error": "no active UV layer"}
    coords: List[float] = []
    nan_count = 0
    outside = 0
    degenerate = 0
    for loop in layer.data:
        u, v = loop.uv
        if math.isnan(u) or math.isnan(v):
            nan_count += 1
            continue
        if u < -1e-4 or u > 1 + 1e-4 or v < -1e-4 or v > 1 + 1e-4:
            outside += 1
        coords.extend((u, v))
    degenerate_geometry = 0
    slivers = 0
    for polygon in mesh.polygons:
        uvs = [layer.data[li].uv for li in polygon.loop_indices]
        area = 0.0
        for i in range(len(uvs)):
            a, b = uvs[i], uvs[(i + 1) % len(uvs)]
            area += a[0] * b[1] - b[0] * a[1]
        uv_area = abs(area) * 0.5
        if polygon.area < 1e-9:
            degenerate_geometry += 1
        elif polygon.area < 1e-6:
            slivers += 1
        elif uv_area < 1e-9:
            # A real face (>= 1 mm^2) collapsed in UV space: unusable layout.
            degenerate += 1
    if coords:
        us, vs = coords[0::2], coords[1::2]
        coverage = (max(us) - min(us)) * (max(vs) - min(vs))
        bounds = [round(min(us), 4), round(min(vs), 4), round(max(us), 4), round(max(vs), 4)]
    else:
        coverage = 0.0
        bounds = [0.0, 0.0, 0.0, 0.0]
    return {
        "has_uv": True,
        "uv_layer": layer.name,
        "uv_loops": len(layer.data),
        "loops": len(coords) // 2,
        "nan_loops": nan_count,
        "loops_outside_0_1": outside,
        "degenerate_faces": degenerate,
        "zero_area_geometry_faces": degenerate_geometry,
        "sliver_faces": slivers,
        "coverage": round(coverage, 4),
        "bounds": bounds,
        "uv_layer_count": len(mesh.uv_layers),
    }


def unwrap(garment, params: Dict[str, Any]) -> Dict[str, Any]:
    mesh = garment.data
    removed = _purge_uv_layers(mesh)
    layer = mesh.uv_layers.new(name=UV_LAYER_NAME)
    mesh.uv_layers.active = layer

    set_active(garment)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == garment)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        smart = bpy.ops.uv.smart_project(
            angle_limit=float(params.get("angle_limit", 66.0)) * 3.141592653589793 / 180.0,
            island_margin=float(params.get("island_margin", 0.02)),
            area_weight=0.0,
            correct_aspect=True,
            scale_to_bounds=False,
        )
    except Exception as exc:
        smart = {"ERROR": str(exc)}
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    report = _uv_report(mesh)
    method = "smart_project"
    if (
        not report.get("has_uv")
        or report["coverage"] < MIN_UV_COVERAGE
        or report["degenerate_faces"] > 0
    ):
        # Fallback: angle-based unwrap with seams marked on sharp edges.
        logger.warning(
            "smart project insufficient (%s); falling back to angle-based unwrap", report
        )
        method = "angle_based_with_seams"
        set_active(garment)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.mark_seam(clear=True)
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.02)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        report = _uv_report(mesh)
        report["fallback_reason"] = "smart project coverage/degenerate faces"

    report.update({"method": method, "smart_project_result": str(smart), "layers_removed": removed})
    return report


def _material_plan(settings: Dict[str, Any], outfit: str) -> List[Dict[str, Any]]:
    """One entry per material the outfit needs (single-garment outfits get one)."""
    pieces = outfit_pieces(settings)
    plan: List[Dict[str, Any]] = []
    seen = set()
    for piece in pieces:
        material = piece["material"]
        if material["name"] in seen:
            continue
        seen.add(material["name"])
        plan.append(
            {
                "key": piece["key"],
                "label": piece.get("label"),
                "material": material,
                "owner": "piece",
            }
        )
    for detail in outfit_details(settings, pieces):
        material = detail["material"]
        if material["name"] in seen:
            continue
        seen.add(material["name"])
        plan.append(
            {
                "key": detail["id"],
                "label": detail["id"].replace("_", " ").title(),
                "material": material,
                "owner": "detail",
            }
        )
    for key, raw in (settings.get("materials") or {}).items():
        material = material_spec(raw or {}, outfit, key)
        if material["name"] in seen:
            continue
        seen.add(material["name"])
        plan.append({"key": key, "label": key.title(), "material": material, "owner": "material"})
    return plan


def _apply_piece_materials(garment, plan, uv, variant, outfit: str) -> Dict[str, Any]:
    """Build one textured material per piece and keep the shell's slot order."""
    by_name = {entry["material"]["name"]: entry for entry in plan}
    slots = [slot.material.name if slot.material else None for slot in garment.material_slots]
    unknown = [name for name in slots if name and name not in by_name]
    if unknown:
        return _fail(
            "the garment carries material slots no outfit definition owns: "
            + ", ".join(sorted(unknown)),
            {"uv": uv, "material_slots": slots},
        )

    # Build every material first, then pack, then verify: a verification that
    # runs before packing reports packed=False for images the .blend does hold.
    built_materials: List[Dict[str, Any]] = []
    for slot in garment.material_slots:
        material = slot.material
        if material is None:  # pragma: no cover - shell always assigns one
            return _fail("a material slot on the garment is empty", {"uv": uv})
        entry = by_name[material.name]
        spec = entry["material"]
        try:
            textures = resolve_textures(f"{outfit}_{entry['key']}", spec)
        except (FileNotFoundError, ValueError) as exc:
            return _fail(f"texture resolution failed for {material.name}: {exc}", {"uv": uv})
        built_materials.append(
            {
                "material": material,
                "entry": entry,
                "spec": spec,
                "textures": textures,
                "built": build_material_nodes(material, textures["paths"], spec),
            }
        )

    packed = pack_images()

    reports: List[Dict[str, Any]] = []
    for item in built_materials:
        material, entry, spec, textures, built = (
            item["material"],
            item["entry"],
            item["spec"],
            item["textures"],
            item["built"],
        )
        check = verify_texture_set(textures["paths"], built.get("images_by_slot"))
        if not check["ok"]:
            return _fail(
                f"textures for {material.name} are unusable: " + "; ".join(check["problems"]),
                {"uv": uv, "textures": check},
            )
        missing = [key for key, present in built["texture_nodes"].items() if not present]
        if missing:
            return _fail(
                f"{material.name} is missing texture nodes: {', '.join(missing)}", {"uv": uv}
            )
        reports.append(
            {
                "name": material.name,
                "key": entry["key"],
                "owner": entry["owner"],
                "texture_nodes": built["texture_nodes"],
                "images": built["images"],
                "provenance": textures["provenance"],
                "base_color": spec.get("base_color"),
                "roughness": spec.get("roughness"),
                "metallic": spec.get("metallic"),
                "textures": check,
            }
        )

    main = reports[0]
    metrics = {
        "uv": uv,
        "material": {
            "name": main["name"],
            "texture_nodes": main["texture_nodes"],
            "images": main["images"],
            "provenance": main["provenance"],
            "packed_images": packed,
            "base_color": main["base_color"],
            "roughness": main["roughness"],
            "metallic": main["metallic"],
        },
        "textures": main["textures"],
        "material_count": len(reports),
        "materials": [
            {key: value for key, value in report.items() if key != "textures"} for report in reports
        ],
        "garment_stats": mesh_stats(garment),
        "variant": variant.name,
    }
    logger.info(
        "built %d piece materials (%s)",
        len(reports),
        ", ".join(report["name"] for report in reports),
    )
    return {"status": "pass", "stage": STAGE_NAME, "metrics": metrics, "checkpoint": None}


def material_checkpoint(
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
            f"{GARMENT_OBJECT_NAME!r} not found in {blend_path}; run the earlier stages first"
        )

    uv = unwrap(garment, params)
    if not uv.get("has_uv"):
        return _fail("UV unwrap failed: no active UV layer", {"uv": uv})
    if uv["coverage"] < MIN_UV_COVERAGE:
        return _fail(
            f"UV coverage {uv['coverage']:.3f} is below {MIN_UV_COVERAGE} "
            "(unusable texture layout)",
            {"uv": uv},
        )
    if uv["nan_loops"] or uv["loops_outside_0_1"] or uv["degenerate_faces"]:
        return _fail(
            "UV layout is invalid "
            f"(nan={uv['nan_loops']}, outside_0_1={uv['loops_outside_0_1']}, "
            f"degenerate={uv['degenerate_faces']})",
            {"uv": uv},
        )
    if uv["uv_layer_count"] != 1:
        return _fail(f"expected exactly one UV layer, found {uv['uv_layer_count']}", {"uv": uv})

    plan = _material_plan(settings, outfit)
    if len(plan) > 1:
        return _apply_piece_materials(garment, plan, uv, variant, outfit)

    material_settings = settings.get("material") or {}
    try:
        textures = resolve_textures(outfit, material_settings)
    except (FileNotFoundError, ValueError) as exc:
        return _fail(f"texture resolution failed: {exc}", {"uv": uv})

    material = build_material(
        garment,
        str(material_settings.get("name") or f"MAT_Cloth_{outfit}"),
        textures["paths"],
        material_settings,
    )
    packed = pack_images()

    texture_check = verify_texture_set(textures["paths"], material.get("images_by_slot"))
    if not texture_check["ok"]:
        return _fail(
            "material textures are unusable: " + "; ".join(texture_check["problems"]),
            {"uv": uv, "textures": texture_check},
        )

    node_images = material["texture_nodes"]
    missing = [slot for slot, present in node_images.items() if not present]
    if missing:
        return _fail(f"material is missing texture nodes: {', '.join(missing)}", {"uv": uv})

    metrics = {
        "uv": uv,
        "material": {
            "name": material["material"].name,
            "texture_nodes": node_images,
            "images": material["images"],
            "provenance": textures["provenance"],
            "packed_images": packed,
            "base_color": material_settings.get("base_color"),
            "roughness": material_settings.get("roughness"),
            "metallic": material_settings.get("metallic"),
        },
        "textures": texture_check,
        "material_count": 1,
        "garment_stats": mesh_stats(garment),
        "variant": variant.name,
    }
    logger.info(
        "material %s with %d images (uv coverage %.3f, method %s)",
        material["material"].name,
        len(material["images"]),
        uv["coverage"],
        uv["method"],
    )
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
    variant = params.get("variant") or "male"

    result = material_checkpoint(blend_path, region, params, outfit)
    if result["status"] != "pass":
        return result

    checkpoint = checkpoint_path(outfit, STAGE_INDEX, STAGE_NAME, variant)
    ensure_dir(outfit_dir(outfit, variant))
    try:
        save_blend(checkpoint)
    except PipelineError as exc:
        result.update({"status": "fail", "error": str(exc), "checkpoint": None})
        result["metrics"]["error"] = str(exc)
        return result

    result["checkpoint"] = checkpoint
    result["metrics"]["checkpoint_bytes"] = os.path.getsize(checkpoint)
    logger.info("material checkpoint -> %s", checkpoint)
    return result
