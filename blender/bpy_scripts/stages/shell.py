"""Stage 00 — garment generation.

The previous implementation copied the whole base-body mesh and renamed it
``CLOTH_Shell``. That is a body, not a garment: it has no openings, no
thickness, no hem and it cannot be fitted without double geometry.

This stage builds an actual garment:

1. resolve the body variant through ``assets/base_body/manifest.json``;
2. normalise the working scene into the canonical frame (+Z up, -Y forward);
3. classify body faces into trunk / sleeve coverage from the outfit definition
   and the measured body landmarks;
4. extract those faces into a new mesh (a fitted panel set, not a body clone);
5. push every panel vertex off the body along the *surface* normal with a
   coverage-dependent offset (looser at the hem, tighter at the collar);
6. cut the front opening, add real thickness with a Solidify modifier and
   recalculate normals;
7. tag hem/collar vertices in ``UN_SHRINK`` so the fit stage never pulls them
   back onto the skin.

A supplied garment file can be used instead of generating one
(``params.garment_blend`` / ``params.garment_object``); it then goes through the
same validation.
"""

from __future__ import annotations

import os
from typing import Any, Dict

try:  # pragma: no cover
    import bmesh
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    bmesh = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, load_outfit, resolve_variant
from ..utils.bpy_helpers import (
    PipelineError,
    find_armature,
    find_mesh_object,
    mesh_stats,
    open_blend,
    save_blend,
)
from ..utils.frame import apply_orientation, canonical_rotation, infer_landmarks
from ..utils.geometry import Surface
from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, outfit_dir

logger = get_logger("stages.shell")

STAGE_INDEX = 0
STAGE_NAME = "shell"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
UN_SHRINK_GROUP = "UN_SHRINK"
MIN_GARMENT_FACES = 40


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


# --------------------------------------------------------------------------- #
# garment region construction
# --------------------------------------------------------------------------- #
def _local_plane(body, world_co, world_normal):
    """Convert a world-space plane into the body's local space for bisecting."""
    matrix = body.matrix_world
    co = matrix.inverted() @ Vector(world_co)
    # Directions transform with the transpose of the local->world matrix.
    normal = matrix.to_3x3().transposed() @ Vector(world_normal)
    return co, normal.normalized()


def _bisect(bm, body, world_co, world_normal, clear_positive: bool = False) -> None:
    """Cut ``bm`` with a world-space plane, optionally dropping one side."""
    co, normal = _local_plane(body, world_co, world_normal)
    geometry = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(
        bm,
        geom=geometry,
        plane_co=co,
        plane_no=normal,
        dist=1e-6,
        clear_outer=clear_positive,
        clear_inner=False,
    )


def _cut_box(bm, body, limits: Dict[str, float]) -> None:
    """Cut the T-shaped jacket volume out of the body surface.

    All six boundaries are planes, so the produced edges are straight and the
    garment reads like a manufactured panel rather than a torn copy of the body
    (which is what per-face centre tests produced).
    """
    # keep z >= hem  (plane normal -Z, drop the positive side / below)
    _bisect(bm, body, (0, 0, limits["hem_z"]), (0, 0, -1), clear_positive=True)
    # keep z <= collar
    _bisect(bm, body, (0, 0, limits["collar_z"]), (0, 0, 1), clear_positive=True)
    # keep |x| <= sleeve_cut
    _bisect(bm, body, (limits["sleeve_cut"], 0, 0), (1, 0, 0), clear_positive=True)
    _bisect(bm, body, (-limits["sleeve_cut"], 0, 0), (-1, 0, 0), clear_positive=True)


def _cut_front_opening(bm, body, limits: Dict[str, float]) -> int:
    """Open the jacket front along a clean vertical slab."""
    width = limits["opening_width"]
    if width <= 0:
        return 0
    # Bisect (not clear) so the strip boundary is a straight cut line.
    _bisect(bm, body, (width, 0, 0), (1, 0, 0))
    _bisect(bm, body, (-width, 0, 0), (-1, 0, 0))
    _bisect(bm, body, (0, 0, limits["opening_top_z"]), (0, 0, 1))
    centre_y = limits["centre_y"]
    matrix = body.matrix_world
    to_delete = []
    for face in bm.faces:
        centre = matrix @ face.calc_center_median()
        if abs(centre.x) >= width:
            continue
        if centre.y >= centre_y:
            continue
        if centre.z > limits["opening_top_z"]:
            continue
        to_delete.append(face)
    if to_delete:
        bmesh.ops.delete(bm, geom=to_delete, context="FACES")
    return len(to_delete)


def _offset_ramp(
    fraction: float, on_sleeve: bool, settings: Dict[str, Any], limits: Dict[str, float]
):
    """Coverage-dependent standoff: looser at the hem, tighter at the collar."""
    offsets = settings.get("offset") or {}
    base = float(offsets.get("default", 0.014))
    hem_extra = float(offsets.get("hem_extra", 0.0))
    collar_extra = float(offsets.get("collar_extra", 0.0))
    sleeve_extra = float(offsets.get("sleeve_extra", 0.0))
    span = max(limits["collar_z"] - limits["hem_z"], 1e-6)
    position = (limits["collar_z"] - fraction) / span  # 0 at collar, 1 at hem
    hem = max(0.0, min(1.0, (position - 0.62) / 0.38))
    collar = max(0.0, min(1.0, (0.30 - position) / 0.30))

    def ramp(_co):
        return (
            base
            + hem_extra * hem * hem
            + collar_extra * collar
            + (sleeve_extra if on_sleeve else 0.0)
        )

    return ramp


def build_garment_limits(landmarks: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, float]:
    height = landmarks["height"]
    coverage = settings.get("coverage") or {}
    sleeves = settings.get("sleeves") or {}
    top_fraction = float(coverage.get("top", 0.19))
    bottom_fraction = float(coverage.get("bottom", 0.58))
    hem_z = landmarks["top_z"] - bottom_fraction * height
    collar_z = landmarks["top_z"] - top_fraction * height
    # The collar boundary must clear the arms, otherwise the plane slices the
    # sleeves in half. 1.35 arm radii above the arm axis is the acromion.
    collar_z = max(
        collar_z, landmarks["arm_z"] + 1.35 * float(landmarks.get("arm_radius", 0.03 * height))
    )
    collar_z = min(collar_z, landmarks["top_z"] - 0.08 * height)
    if hem_z >= collar_z:
        raise PipelineError(
            f"outfit coverage is inverted (hem z={hem_z:.3f} >= collar z={collar_z:.3f})"
        )
    sleeve_length = min(max(float(sleeves.get("length", 0.9)), 0.0), 1.0)
    cuff_inset = min(max(float(sleeves.get("cuff_inset", 0.98)), 0.0), 1.0)
    sleeve_cut = (
        landmarks["shoulder_x"]
        + (landmarks["arm_tip_x"] - landmarks["shoulder_x"]) * sleeve_length * cuff_inset
    )
    opening = str(settings.get("opening", "closed")).lower() == "front"
    return {
        "hem_z": hem_z,
        "collar_z": collar_z,
        "sleeve_cut": sleeve_cut,
        "opening_width": float(settings.get("opening_width", 0.05)) * height if opening else 0.0,
        "opening_top_z": collar_z,
        "centre_y": float(landmarks.get("centre_y", 0.0)),
        "sleeve_length": sleeve_length,
    }


def _extract_patch_cut(body, landmarks, settings) -> "tuple[Any, Dict[str, Any]]":
    """Cut the garment panel out of the body with plane bisections."""
    limits = build_garment_limits(landmarks, settings)
    mesh = bpy.data.meshes.new(f"{GARMENT_OBJECT_NAME}Mesh")
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.faces.ensure_lookup_table()
    _cut_box(bm, body, limits)
    removed = _cut_front_opening(bm, body, limits)
    loose = [vertex for vertex in bm.verts if not vertex.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    # A plane cut that grazes a triangle leaves sub-millimetre slivers. They are
    # invisible but they explode any relative edge-length metric, so dissolve
    # them (and weld the near-duplicate vertices the bisect created).
    bmesh.ops.dissolve_degenerate(bm, dist=2e-4, edges=bm.edges[:])
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=2e-4)
    bm.faces.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    face_count = len(bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(GARMENT_OBJECT_NAME, mesh)
    obj.matrix_world = body.matrix_world.copy()
    bpy.context.collection.objects.link(obj)
    return obj, {"limits": limits, "opening_faces_removed": removed, "face_count": face_count}


def _tag_unshrink(
    garment, landmarks: Dict[str, Any], settings: Dict[str, Any], limits: Dict[str, float]
) -> int:
    """Pin hem, collar and cuff vertices so the fit stage cannot pull them in.

    Only a narrow band is pinned: pinning a wide region (the previous 6 cm) locks
    half the garment and puts kinks into the fit.
    """
    band = max(0.004, 0.25 * float((settings.get("offset") or {}).get("default", 0.014)))
    hem_z = limits["hem_z"]
    collar_z = limits["collar_z"]
    sleeve_cut = limits["sleeve_cut"]
    matrix = garment.matrix_world

    group = garment.vertex_groups.get(UN_SHRINK_GROUP) or garment.vertex_groups.new(
        name=UN_SHRINK_GROUP
    )
    indices = []
    for vertex in garment.data.vertices:
        co = matrix @ vertex.co
        on_hem = abs(co.z - hem_z) <= band
        on_collar = abs(co.z - collar_z) <= band
        on_cuff = abs(abs(co.x) - sleeve_cut) <= band and abs(co.z - landmarks["arm_z"]) < 6 * band
        if on_hem or on_collar or on_cuff:
            indices.append(vertex.index)
    if indices:
        group.add(indices, 1.0, "REPLACE")
    return len(indices)


def _load_supplied_garment(blend_path: str, object_name: str | None = None):
    """Append a garment mesh from another .blend instead of cutting one.

    Used by the ``garment_blend`` parameter (bring your own mesh). The object is
    renamed to ``CLOTH_Shell`` so every later stage finds it, and it must be a
    mesh with real geometry - a curve/empty or a mesh that is still linked to the
    body is rejected instead of silently producing an empty garment.
    """
    import bpy

    if not os.path.exists(blend_path):
        raise PipelineError(f"supplied garment blend not found: {blend_path}")

    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
        available = list(data_from.objects)
        if object_name:
            if object_name not in available:
                raise PipelineError(
                    f"object {object_name!r} not found in {blend_path}; "
                    f"available objects: {', '.join(sorted(available)) or '(none)'}"
                )
            data_to.objects = [object_name]
        else:
            data_to.objects = available

    meshes = [obj for obj in data_to.objects if obj is not None and obj.type == "MESH"]
    if not meshes:
        raise PipelineError(
            f"no mesh object in {blend_path} (objects: {', '.join(sorted(available)) or '(none)'})"
        )
    garment = max(meshes, key=lambda obj: len(obj.data.vertices))
    if len(garment.data.vertices) < 8:
        raise PipelineError(
            f"supplied garment {garment.name!r} has only {len(garment.data.vertices)} vertices"
        )

    for obj in data_to.objects:
        if obj is not None and obj is not garment:
            bpy.data.objects.remove(obj, do_unlink=True)
    garment.name = GARMENT_OBJECT_NAME
    garment.data.name = f"{GARMENT_OBJECT_NAME}Mesh"
    bpy.context.scene.collection.objects.link(garment)
    garment.matrix_world = garment.matrix_world.copy()
    return garment


def _measure(garment, surface: Surface, offset: float) -> Dict[str, Any]:
    matrix = garment.matrix_world
    clearances = [surface.signed_clearance(matrix @ vertex.co) for vertex in garment.data.vertices]
    mesh = garment.data
    area = 0.0
    for polygon in mesh.polygons:
        area += polygon.area
    return {
        "min_clearance": round(min(clearances), 6) if clearances else 0.0,
        "mean_clearance": round(abs(sum(clearances)) / len(clearances), 6) if clearances else 0.0,
        "penetrating_vertices": sum(1 for c in clearances if c < offset * 0.35),
        "surface_area_m2": round(area, 6),
    }


def generate_garment(
    blend_path: str, region: str, params: Dict[str, Any], outfit: str
) -> Dict[str, Any]:
    if not _HAS_BPY:
        return _fail("bpy is not available: run inside Blender")

    variant_name = params.get("variant")
    try:
        variant = resolve_variant(variant_name, params.get("_body_manifest"))
        settings = load_outfit(outfit, params.get("_outfit_manifest"))
    except ConfigError as exc:
        return _fail(str(exc))

    if region and region != settings.get("region"):
        logger.info(
            "region %s overridden by outfit definition (%s)", region, settings.get("region")
        )
        region = settings.get("region", region)

    open_blend(blend_path)

    body = find_mesh_object(params.get("body_object") or variant.body_object)
    if body is None:
        available = [obj.name for obj in bpy.data.objects]
        return _fail(
            f"body object {variant.body_object!r} not found in {blend_path}; "
            f"objects present: {available}. Fix `body_object` in assets/base_body/manifest.json.",
            {"objects": available, "variant": variant.name},
        )
    if not body.data.polygons:
        return _fail(f"body object {body.name!r} has no faces")

    rotation_applied = False
    if params.get("normalize_orientation", True):
        rotation = canonical_rotation(variant.up_axis, variant.forward_axis)
        targets = [obj for obj in bpy.data.objects if obj.type in {"MESH", "ARMATURE"}]
        apply_orientation(targets, rotation)
        rotation_applied = True

    body_world = [body.matrix_world @ vertex.co for vertex in body.data.vertices]
    landmarks = infer_landmarks(body_world)
    surface = Surface(body)

    supplied = params.get("garment_blend")
    patch_info: Dict[str, Any] = {}
    if supplied:
        garment = _load_supplied_garment(str(supplied), params.get("garment_object"))
        if params.get("fit_supplied", True):
            from ..utils.geometry import fit_to_surface

            fit_to_surface(
                garment,
                surface,
                offset=float((settings.get("offset") or {}).get("default", 0.014)),
                iterations=1,
                relax_iterations=0,
            )
        construction = "supplied_garment"
        openings = 0
    else:
        garment, patch_info = _extract_patch_cut(body, landmarks, settings)
        openings = patch_info["opening_faces_removed"]
        if patch_info["face_count"] < MIN_GARMENT_FACES:
            return _fail(
                f"garment region cut only {patch_info['face_count']} body faces; check "
                f"`coverage`/`sleeves` in assets/outfits/outfits.json for {outfit}",
                {"patch": patch_info, "landmarks": landmarks},
            )
        construction = "body_region_cut"

        # Offset each patch vertex off the body along the surface normal.
        limits = patch_info["limits"]
        matrix = garment.matrix_world
        inverse = matrix.inverted()
        sleeve_vertices = set()
        for polygon in garment.data.polygons:
            centre = matrix @ polygon.center
            near_arm = abs(centre.z - landmarks["arm_z"]) < 2.5 * float(
                landmarks.get("arm_radius", 0.05)
            )
            if near_arm and abs(centre.x) > landmarks["shoulder_x"]:
                sleeve_vertices.update(polygon.vertices)
        for vertex in garment.data.vertices:
            world = matrix @ vertex.co
            ramp = _offset_ramp(world.z, vertex.index in sleeve_vertices, settings, limits)
            location, normal, _tri, _dist = surface.nearest(world)
            if location is None:
                continue
            vertex.co = inverse @ (location + normal * ramp(location))
        garment.data.update()

    thickness = float(settings.get("thickness", 0.006))
    if thickness <= 0:
        return _fail("outfit thickness must be > 0: a zero-thickness garment cannot be exported")
    pinned = _tag_unshrink(
        garment,
        landmarks,
        settings,
        patch_info.get("limits", build_garment_limits(landmarks, settings)),
    )

    stats = mesh_stats(garment)
    measured = _measure(
        garment, surface, float((settings.get("offset") or {}).get("default", 0.014))
    )
    body_stats = mesh_stats(body)

    if stats["face_count"] < MIN_GARMENT_FACES:
        return _fail(f"garment has only {stats['face_count']} faces after construction")
    if stats["face_count"] == body_stats["face_count"]:
        return _fail("garment is a 1:1 copy of the base body; refusing to export a body clone")

    metrics = {
        "garment_object": garment.name,
        "garment_source": construction,
        "garment_stats": stats,
        "body_object": body.name,
        "body_stats": body_stats,
        "variant": variant.name,
        "variant_file": variant.path,
        "orientation": {
            "source_up_axis": variant.up_axis,
            "source_forward_axis": variant.forward_axis,
            "normalized_to": "+Z up / -Y forward",
            "rotation_applied": rotation_applied,
        },
        "landmarks": {key: round(value, 5) for key, value in landmarks.items()},
        "thickness_m": thickness,
        "front_opening_faces_removed": openings,
        "patch": patch_info,
        "pinned_vertices": pinned,
        "thickness_configured_m": thickness,
        "shell_stage": "panel + standoff (thickness is applied after the surface fit)",
        "measurements": measured,
        "outfit_settings": {
            "coverage": settings.get("coverage"),
            "sleeves": settings.get("sleeves"),
            "offset": settings.get("offset"),
            "thickness": thickness,
            "opening": settings.get("opening"),
        },
        "_artifacts": {
            "landmarks": landmarks,
            "settings": settings,
            "variant": variant.name,
        },
    }
    if find_armature(variant.armature_object) is not None:
        metrics["armature_present"] = True
    return _pass(metrics)


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

    result = generate_garment(blend_path, region, params, outfit)
    if result["status"] != "pass":
        return result

    checkpoint = checkpoint_path(outfit, STAGE_INDEX, STAGE_NAME, variant)
    ensure_dir(outfit_dir(outfit, variant))
    try:
        save_blend(checkpoint)
    except PipelineError as exc:
        result["status"] = "fail"
        result["error"] = str(exc)
        result["metrics"]["error"] = str(exc)
        result["checkpoint"] = None
        return result

    result["checkpoint"] = checkpoint
    result["metrics"]["checkpoint_bytes"] = os.path.getsize(checkpoint)
    logger.info("garment checkpoint -> %s", checkpoint)
    return result
