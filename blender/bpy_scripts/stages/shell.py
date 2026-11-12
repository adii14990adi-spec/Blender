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

import json
import math
import os
from typing import Any, Dict, List, Tuple

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

from ..config import (
    ConfigError,
    load_outfit,
    outfit_details,
    outfit_pieces,
    resolve_variant,
)
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
PIECE_OBJECT_PREFIX = "CLOTH_Shell__"
DETAIL_OBJECT_PREFIX = "CLOTH_Detail__"
PIECE_META_KEY = "garment_pieces"
DETAIL_META_KEY = "garment_details"
UN_SHRINK_GROUP = "UN_SHRINK"
MIN_GARMENT_FACES = 40
MIN_PIECE_FACES = 12


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


def _cut_band(bm, body, limits: Dict[str, float]) -> None:
    """Cut a garment band out of the body surface.

    Every boundary is a plane, so the produced edges are straight and the piece
    reads like a manufactured panel rather than a torn copy of the body (which
    is what per-face centre tests produced). Upper-body pieces also get the two
    sleeve planes; trousers, boots and belts are pure vertical bands.
    """
    # keep z >= hem  (plane normal -Z, drop the positive side / below)
    _bisect(bm, body, (0, 0, limits["hem_z"]), (0, 0, -1), clear_positive=True)
    # keep z <= collar
    _bisect(bm, body, (0, 0, limits["collar_z"]), (0, 0, 1), clear_positive=True)
    sleeve_cut = float(limits.get("sleeve_cut", 0.0))
    if sleeve_cut > 0.0:
        _bisect(bm, body, (sleeve_cut, 0, 0), (1, 0, 0), clear_positive=True)
        _bisect(bm, body, (-sleeve_cut, 0, 0), (-1, 0, 0), clear_positive=True)


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


def build_piece_limits(landmarks: Dict[str, Any], piece: Dict[str, Any]) -> Dict[str, float]:
    """Turn a piece's ``coverage`` into the cutting planes for that piece.

    ``coverage`` is measured from the top of the head (0.0) to the soles (1.0),
    so one definition is valid for both body variants.
    """
    height = landmarks["height"]
    coverage = piece.get("coverage") or {}
    sleeves = piece.get("sleeves") or {}
    top_fraction = float(coverage.get("top", 0.19))
    bottom_fraction = float(coverage.get("bottom", 0.58))
    top_z = landmarks["top_z"] - top_fraction * height
    bottom_z = landmarks["top_z"] - bottom_fraction * height
    region = str(piece.get("region") or "UPPER_BODY").upper()
    upper = region == "UPPER_BODY"

    if upper:
        # The collar boundary must clear the arms, otherwise the plane slices
        # the sleeves in half. 1.35 arm radii above the arm axis is the acromion.
        arm_radius = float(landmarks.get("arm_radius", 0.03 * height))
        collar_z = max(top_z, landmarks["arm_z"] + 1.35 * arm_radius)
        collar_z = min(collar_z, landmarks["top_z"] - 0.08 * height)
    else:
        collar_z = min(top_z, landmarks["top_z"] - 0.02 * height)
    hem_z = max(bottom_z, landmarks["ground_z"] + 0.004)

    if hem_z >= collar_z:
        raise PipelineError(
            f"piece {piece.get('key')!r} coverage is inverted or empty "
            f"(hem z={hem_z:.3f} >= collar z={collar_z:.3f}); "
            "check `coverage.top` / `coverage.bottom`"
        )

    sleeve_cut = 0.0
    sleeve_length = 0.0
    if upper:
        sleeve_length = min(max(float(sleeves.get("length", 0.9)), 0.0), 1.0)
        cuff_inset = min(max(float(sleeves.get("cuff_inset", 0.98)), 0.0), 1.0)
        sleeve_cut = (
            landmarks["shoulder_x"]
            + (landmarks["arm_tip_x"] - landmarks["shoulder_x"]) * sleeve_length * cuff_inset
        )
    opening = upper and str(piece.get("opening", "closed")).lower() == "front"
    return {
        "hem_z": hem_z,
        "collar_z": collar_z,
        "sleeve_cut": sleeve_cut,
        "opening_width": float(piece.get("opening_width", 0.05)) * height if opening else 0.0,
        "opening_top_z": collar_z,
        "centre_y": float(landmarks.get("centre_y", 0.0)),
        "sleeve_length": sleeve_length,
        "region": region,
    }


def build_garment_limits(landmarks: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, float]:
    """Backwards-compatible wrapper around :func:`build_piece_limits`."""
    return build_piece_limits(landmarks, settings)


def _extract_piece(body, landmarks, piece, name: str) -> "tuple[Any, Dict[str, Any]]":
    """Cut one garment piece out of the body with plane bisections."""
    limits = build_piece_limits(landmarks, piece)
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.faces.ensure_lookup_table()
    _cut_band(bm, body, limits)
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
    obj = bpy.data.objects.new(name, mesh)
    obj.matrix_world = body.matrix_world.copy()
    bpy.context.collection.objects.link(obj)
    return obj, {"limits": limits, "opening_faces_removed": removed, "face_count": face_count}


def _offset_piece(
    garment, landmarks: Dict[str, Any], surface, piece: Dict[str, Any], limits: Dict[str, Any]
) -> int:
    """Push every piece vertex off the body along the surface normal."""
    matrix = garment.matrix_world
    inverse = matrix.inverted()
    sleeve_vertices = set()
    if str(piece.get("region") or "UPPER_BODY").upper() == "UPPER_BODY":
        arm_radius = float(landmarks.get("arm_radius", 0.05))
        for polygon in garment.data.polygons:
            centre = matrix @ polygon.center
            near_arm = abs(centre.z - landmarks["arm_z"]) < 2.5 * arm_radius
            if near_arm and abs(centre.x) > landmarks["shoulder_x"]:
                sleeve_vertices.update(polygon.vertices)
    moved = 0
    for vertex in garment.data.vertices:
        world = matrix @ vertex.co
        ramp = _offset_ramp(world.z, vertex.index in sleeve_vertices, piece, limits)
        location, normal, _tri, _dist = surface.nearest(world)
        if location is None:
            continue
        vertex.co = inverse @ (location + normal * ramp(location))
        moved += 1
    garment.data.update()
    return moved


def _tag_piece_unshrink(
    garment, landmarks: Dict[str, Any], piece: Dict[str, Any], limits: Dict[str, float]
) -> int:
    """Pin a narrow band of hem, top edge and cuffs so the fit cannot pull them in."""
    band = max(0.004, 0.25 * float((piece.get("offset") or {}).get("default", 0.014)))
    hem_z = limits["hem_z"]
    collar_z = limits["collar_z"]
    sleeve_cut = float(limits.get("sleeve_cut") or 0.0)
    matrix = garment.matrix_world

    group = garment.vertex_groups.get(UN_SHRINK_GROUP) or garment.vertex_groups.new(
        name=UN_SHRINK_GROUP
    )
    indices = []
    for vertex in garment.data.vertices:
        co = matrix @ vertex.co
        on_hem = abs(co.z - hem_z) <= band
        on_collar = abs(co.z - collar_z) <= band
        on_cuff = (
            sleeve_cut > 0.0
            and abs(abs(co.x) - sleeve_cut) <= band
            and abs(co.z - landmarks["arm_z"]) < 6 * band
        )
        if on_hem or on_collar or on_cuff:
            indices.append(vertex.index)
    if indices:
        group.add(indices, 1.0, "REPLACE")
    return len(indices)


# --------------------------------------------------------------------------- #
# hard details (pouches, pads, buckles): boxes anchored to body landmarks
# --------------------------------------------------------------------------- #
_ANCHOR_Z = {
    "waist": lambda lm: lm["hip_z"],
    "hip": lambda lm: lm["hip_z"] - 0.02 * lm["height"],
    "thigh": lambda lm: lm["crotch_z"] - 0.18 * (lm["crotch_z"] - lm["knee_z"]),
    "knee": lambda lm: lm["knee_z"],
    "calf": lambda lm: lm["knee_z"] - 0.5 * (lm["knee_z"] - lm["ankle_z"]),
    "ankle": lambda lm: lm["ankle_z"] + 0.02 * lm["height"],
    "chest": lambda lm: lm["arm_z"] - 0.12 * lm["height"],
}


def _detail_attachment(
    body_world: List[Any], landmarks: Dict[str, Any], detail: Dict[str, Any], surface=None
) -> "tuple[Any, Any]":
    """Return (surface point, outward horizontal normal) for a detail box.

    ``side`` picks the body side (left/right/center) and ``face`` picks which way
    the box faces (front/back/outer), so a knee pad sits on the front of one knee
    while a belt pouch sits on the outside of the hip.
    """
    from mathutils import Vector

    anchor_z = _ANCHOR_Z[detail["anchor"]](landmarks) + detail["z_offset"]
    window = 0.02 * landmarks["height"]
    band = [v for v in body_world if abs(v.z - anchor_z) <= window]
    if not band:  # pragma: no cover - only for a body with no vertices near it
        band = sorted(body_world, key=lambda v: abs(v.z - anchor_z))[:64]

    side = detail["side"]
    face = detail.get("face") or "front"
    span = max(abs(v.x) for v in band)
    x_range = detail.get("x_range")
    if x_range:
        # Explicit slice of the half-span (used to pin a pad to one leg rather
        # than to the widest point of that side).
        low, high = (float(value) for value in x_range)
        if side == "right":
            low, high = -high, -low
        band = [v for v in band if low * span <= v.x <= high * span]
    elif side == "left":
        band = [v for v in band if v.x > 0.25 * span]
    elif side == "right":
        band = [v for v in band if v.x < -0.25 * span]
    elif side in {"center", "both"}:
        band = [v for v in band if abs(v.x) < 0.45 * span]
    if not band:  # pragma: no cover - very narrow band
        band = sorted(body_world, key=lambda v: abs(v.z - anchor_z))[:32]

    centre_y = landmarks["centre_y"]
    if face == "back":
        point = max(band, key=lambda v: v.y)
    elif face == "outer":
        sign = 1.0 if side == "left" else -1.0
        point = max(band, key=lambda v: sign * v.x)
    else:  # front
        point = min(band, key=lambda v: v.y)

    normal = None
    if surface is not None:
        # The real surface normal at the attachment point: a heuristic built from
        # the body axis points a knee pad sideways on an off-centre leg.
        _location, normal, _index, _distance = surface.nearest(point)
    if normal is None:  # pragma: no cover - defensive
        normal = Vector((point.x, point.y - centre_y, 0.0))
    normal = Vector((normal.x, normal.y, 0.0))
    if normal.length < 1e-6:  # pragma: no cover - degenerate band
        normal = Vector((0.0, -1.0, 0.0))
    normal.normalize()
    return point, normal


def _make_detail_box(
    name: str, centre, normal, size: Tuple[float, float, float], rounding: float, tilt_deg: float
):
    """Build a beveled box sitting on the body surface, facing ``normal``."""
    from mathutils import Matrix, Vector

    width, depth, height = (float(value) for value in size)
    bevel = 0.5 * rounding * min(width, depth, height)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for vertex in bm.verts:
        vertex.co.x *= width
        vertex.co.y *= depth
        vertex.co.z *= height
    if bevel > 1e-5:
        bmesh.ops.bevel(
            bm,
            geom=list(bm.verts) + list(bm.edges),
            offset=bevel,
            segments=2,
            affect="EDGES",
            profile=0.5,
            clamp_overlap=True,
        )

    up = Vector((0.0, 0.0, 1.0))
    tangent = up.cross(normal)
    if tangent.length < 1e-6:  # pragma: no cover - vertical normal
        tangent = Vector((1.0, 0.0, 0.0))
    tangent.normalize()
    if abs(tilt_deg) > 1e-6:
        normal = (Matrix.Rotation(math.radians(tilt_deg), 3, tangent) @ normal).normalized()
    vertical = normal.cross(tangent).normalized()
    basis = Matrix((tangent, normal, vertical)).transposed()
    bmesh.ops.transform(bm, matrix=basis, verts=bm.verts[:])
    bmesh.ops.translate(bm, vec=Vector(centre), verts=bm.verts[:])

    mesh = bpy.data.meshes.new(f"{name}Mesh")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _assign_material(obj, material_name: str) -> None:
    """Give the object a single placeholder slot the material stage rebuilds."""
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    if not material.use_nodes:
        material.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.material_index = 0


def _build_details(
    body_world: List[Any],
    landmarks: Dict[str, Any],
    details: List[Dict[str, Any]],
    surface=None,
) -> List[Dict[str, Any]]:
    """Create one box object per detail and report what was placed where."""
    built: List[Dict[str, Any]] = []
    for detail in details:
        point, normal = _detail_attachment(body_world, landmarks, detail, surface)
        width, depth, height = detail["size"]
        centre = point + normal * (detail["gap"] + 0.5 * depth)
        name = f"{DETAIL_OBJECT_PREFIX}{detail['id']}"
        obj = _make_detail_box(
            name, centre, normal, (width, depth, height), detail["rounding"], detail["tilt_deg"]
        )
        _assign_material(obj, detail["material"]["name"])
        built.append(
            {
                "id": detail["id"],
                "object": obj.name,
                "anchor": detail["anchor"],
                "side": detail["side"],
                "face": detail.get("face") or "front",
                "size": [round(value, 4) for value in detail["size"]],
                "position": [round(value, 4) for value in centre],
                "material": detail["material"]["name"],
                "stats": mesh_stats(obj),
            }
        )
        logger.info(
            "detail %s (%s/%s/%s) at (%.2f, %.2f, %.2f)",
            detail["id"],
            detail["anchor"],
            detail["side"],
            detail.get("face") or "front",
            centre[0],
            centre[1],
            centre[2],
        )
    return built


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


def _store_piece_metadata(garment, built_pieces, built_details) -> None:
    """Persist the piece/detail plan on the object for the later stages.

    Stages run as separate Blender sessions, so the shell stage writes what it
    built (materials, per-piece offsets and thickness) as custom properties that
    survive the checkpoint and the join.
    """
    pieces_meta = []
    for built in built_pieces:
        piece = built["settings"]
        pieces_meta.append(
            {
                "key": built["key"],
                "label": built.get("label"),
                "object": built["object"],
                "region": built["region"],
                "coverage": built.get("coverage"),
                "material": piece.get("material"),
                "offset": piece.get("offset"),
                "thickness": built.get("thickness") or piece.get("thickness"),
                "sleeves": piece.get("sleeves"),
                "opening": piece.get("opening"),
                "fit": piece.get("fit"),
                "fit_mode": piece.get("fit_mode") or "surface",
                "fit_params": piece.get("fit_params") or {},
                "face_count": built["stats"]["face_count"],
            }
        )
    garment[PIECE_META_KEY] = json.dumps(pieces_meta)
    garment[DETAIL_META_KEY] = json.dumps(built_details)


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

    body_world_verts = [body.matrix_world @ vertex.co for vertex in body.data.vertices]
    pieces = outfit_pieces(settings)
    details = outfit_details(settings, pieces)
    supplied = params.get("garment_blend")
    patch_info: Dict[str, Any] = {}
    built_pieces: List[Dict[str, Any]] = []
    built_details: List[Dict[str, Any]] = []
    openings = 0
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
        if garment.vertex_groups.get(UN_SHRINK_GROUP) is None:
            garment.vertex_groups.new(name=UN_SHRINK_GROUP)
        built_pieces.append(
            {
                "key": "main",
                "label": str(settings.get("display_name") or "Garment"),
                "object": garment.name,
                "region": str(settings.get("region") or "UPPER_BODY"),
                "coverage": settings.get("coverage"),
                "settings": pieces[0],
                "limits": {},
                "opening_faces_removed": 0,
                "pinned_vertices": 0,
                "offset_vertices_moved": 0,
                "stats": mesh_stats(garment),
                "measurements": {},
            }
        )
    else:
        construction = "body_region_cut"
        for index, piece in enumerate(pieces):
            name = GARMENT_OBJECT_NAME if index == 0 else f"{PIECE_OBJECT_PREFIX}{piece['key']}"
            piece_obj, info = _extract_piece(body, landmarks, piece, name)
            if info["face_count"] < MIN_PIECE_FACES:
                return _fail(
                    f"piece {piece['key']!r} cut only {info['face_count']} body faces; check "
                    f"`coverage`/`sleeves` in assets/outfits/outfits.json for {outfit}",
                    {"piece": piece["key"], "patch": info, "landmarks": landmarks},
                )
            moved = _offset_piece(piece_obj, landmarks, surface, piece, info["limits"])
            pinned_piece = _tag_piece_unshrink(piece_obj, landmarks, piece, info["limits"])
            _assign_material(piece_obj, piece["material"]["name"])
            openings += info["opening_faces_removed"]
            built_pieces.append(
                {
                    "key": piece["key"],
                    "label": piece.get("label"),
                    "object": piece_obj.name,
                    "region": piece["region"],
                    "coverage": piece.get("coverage"),
                    "settings": piece,
                    "limits": info["limits"],
                    "opening_faces_removed": info["opening_faces_removed"],
                    "pinned_vertices": pinned_piece,
                    "offset_vertices_moved": moved,
                    "stats": mesh_stats(piece_obj),
                    "measurements": _measure(
                        piece_obj,
                        surface,
                        float((piece.get("offset") or {}).get("default", 0.014)),
                    ),
                }
            )
            logger.info(
                "piece %s (%s): %d faces, %d pinned, moved %d vertices",
                piece["key"],
                piece["region"],
                info["face_count"],
                pinned_piece,
                moved,
            )
        garment = bpy.data.objects[built_pieces[0]["object"]]
        patch_info = {
            "limits": built_pieces[0]["limits"],
            "opening_faces_removed": openings,
            "face_count": built_pieces[0]["stats"]["face_count"],
        }
        built_details = _build_details(body_world_verts, landmarks, details, surface)

    thickness = float((built_pieces[0]["settings"] or {}).get("thickness", 0.006))
    if thickness <= 0:
        return _fail("outfit thickness must be > 0: a zero-thickness garment cannot be exported")
    for built in built_pieces:
        piece_thickness = float((built["settings"] or {}).get("thickness", thickness))
        if piece_thickness <= 0:
            return _fail(
                f"piece {built['key']!r} thickness must be > 0: a zero-thickness garment "
                "cannot be exported"
            )
        built["thickness"] = piece_thickness

    pinned = sum(built["pinned_vertices"] for built in built_pieces)
    stats = mesh_stats(garment)
    if len(built_pieces) > 1:
        # The multi-piece object does not exist yet (the shrinkwrap stage joins
        # the pieces), so report the arithmetic sum of what will become it.
        for key in ("vertex_count", "edge_count", "face_count", "triangle_count"):
            stats[key] = sum(built["stats"][key] for built in built_pieces)
    stats["material_slots"] = sorted(
        {built["settings"]["material"]["name"] for built in built_pieces}
    )
    measurements = [built["measurements"] for built in built_pieces if built["measurements"]]
    measured = {
        "min_clearance": min(m["min_clearance"] for m in measurements) if measurements else 0.0,
        "mean_clearance": round(
            sum(m["mean_clearance"] for m in measurements) / len(measurements), 6
        )
        if measurements
        else 0.0,
        "penetrating_vertices": sum(m["penetrating_vertices"] for m in measurements),
        "surface_area_m2": round(sum(m["surface_area_m2"] for m in measurements), 6),
        "pieces_measured": len(measurements),
    }
    body_stats = mesh_stats(body)

    if stats["face_count"] < MIN_GARMENT_FACES:
        return _fail(f"garment has only {stats['face_count']} faces after construction")
    if len(built_pieces) == 1 and stats["face_count"] == body_stats["face_count"]:
        return _fail("garment is a 1:1 copy of the base body; refusing to export a body clone")

    _store_piece_metadata(garment, built_pieces, built_details)

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
        "piece_count": len(built_pieces),
        "pieces": [
            {
                "key": built["key"],
                "label": built.get("label"),
                "object": built["object"],
                "region": built["region"],
                "coverage": built.get("coverage"),
                "material": built["settings"]["material"]["name"],
                "thickness_m": built.get("thickness"),
                "pinned_vertices": built["pinned_vertices"],
                "offset_vertices_moved": built["offset_vertices_moved"],
                "stats": built["stats"],
                "measurements": built["measurements"],
                "limits": {
                    key: (round(value, 5) if isinstance(value, (int, float)) else value)
                    for key, value in (built["limits"] or {}).items()
                },
            }
            for built in built_pieces
        ],
        "detail_count": len(built_details),
        "details": built_details,
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
