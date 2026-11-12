"""Stage 02 — skinning: surface weight transfer, 4-influence cap, armature bind.

Fixes the three defects of the old stage:

* weights are interpolated from the nearest point on the body **surface**
  (barycentric over the triangle, equivalent to Data Transfer
  ``POLYINTERP_NEAREST``) instead of copied from the nearest body *vertex*;
* only deform-bone groups are transferred (``UN_SHRINK`` and any other helper
  group is excluded);
* the strongest ``weight_limit`` influences are kept and renormalised to sum 1,
  and every vertex is asserted to have valid normalised weights afterwards.

The garment is then bound to the character armature (existing one when the
asset ships a rig, procedural humanoid otherwise).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, load_outfit, resolve_variant
from ..utils.bpy_helpers import (
    PipelineError,
    find_mesh_object,
    mesh_stats,
    open_blend,
    save_blend,
)
from ..utils.frame import infer_landmarks
from ..utils.geometry import Surface, barycentric_surface_weights, vertex_weight_table
from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, outfit_dir
from ..utils.rig import bind_to_armature, deform_bone_names, ensure_body_rig

logger = get_logger("stages.weights")

STAGE_INDEX = 2
STAGE_NAME = "weights"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
UN_SHRINK_GROUP = "UN_SHRINK"
HELPER_GROUPS = {UN_SHRINK_GROUP, "DEFORM", "SHRINKWRAP", "BODY"}
DEFAULT_WEIGHT_LIMIT = 4


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


def transfer_weights(
    garment,
    body,
    armature,
    weight_limit: int = DEFAULT_WEIGHT_LIMIT,
    min_weight: float = 0.01,
) -> Dict[str, Any]:
    """Skin ``garment`` from ``body`` surface weights, capped and normalised."""
    if garment.type != "MESH" or body.type != "MESH":
        raise PipelineError("garment and body must be mesh objects")
    if weight_limit < 1:
        raise PipelineError(f"weight_limit must be >= 1, got {weight_limit}")

    bone_names = deform_bone_names(armature)
    if not bone_names:
        raise PipelineError(f"armature {armature.name!r} exposes no deform bones")

    # A body vertex group that is not a deform bone (e.g. UN_SHRINK) must not
    # become a skin influence.
    body_groups = {group.name for group in body.vertex_groups}
    transferable = [name for name in bone_names if name in body_groups]
    missing = sorted(set(bone_names) - set(transferable))
    if not transferable:
        raise PipelineError(
            f"body {body.name!r} has no vertex groups matching the armature deform bones "
            f"({', '.join(bone_names[:6])}...). The body is not skinned."
        )

    table = vertex_weight_table(body, allowed_group_names=transferable)
    body_index_to_name = {group.index: group.name for group in body.vertex_groups}
    # Remap table keys to garment group indices created below.
    garment_groups = {}
    for name in transferable:
        group = garment.vertex_groups.get(name)
        if group is None:
            group = garment.vertex_groups.new(name=name)
        garment_groups[name] = group

    # Reset any pre-existing skin groups so a re-run is idempotent.
    for name in list(garment.vertex_groups.keys()):
        if name in HELPER_GROUPS:
            continue
        if name not in transferable:
            garment.vertex_groups.remove(garment.vertex_groups[name])
        else:
            garment.vertex_groups[name].remove(range(len(garment.data.vertices)))

    surface = Surface(body)
    matrix = garment.matrix_world
    influence_histogram: Dict[int, int] = {}
    unweighted: List[int] = []
    per_vertex_influences = 0
    total_influences = 0
    transferred = 0

    for vertex in garment.data.vertices:
        point = matrix @ vertex.co
        weights, _info = barycentric_surface_weights(point, surface, table, limit=weight_limit)
        if not weights:
            unweighted.append(vertex.index)
            continue
        # weights keys are body group indices -> map to names -> garment groups
        resolved = {}
        for group_index, weight in weights.items():
            name = body_index_to_name.get(group_index)
            if name and name in garment_groups and weight >= min_weight:
                resolved[name] = resolved.get(name, 0.0) + weight
        total = sum(resolved.values())
        if total <= 0:
            unweighted.append(vertex.index)
            continue
        for name, weight in resolved.items():
            garment_groups[name].add([vertex.index], weight / total, "REPLACE")
        count = len(resolved)
        influence_histogram[count] = influence_histogram.get(count, 0) + 1
        per_vertex_influences = max(per_vertex_influences, count)
        total_influences += count
        transferred += 1

    for index in unweighted:
        _fallback_weight(garment, index, armature, garment_groups, matrix, weight_limit)
        influence_histogram[1] = influence_histogram.get(1, 0) + 1
        per_vertex_influences = max(per_vertex_influences, 1)

    garment.data.update()

    # UN_SHRINK is a helper group, never a skin influence.
    if UN_SHRINK_GROUP in list(bone_names):
        raise PipelineError("UN_SHRINK collides with a deform bone name; rename the helper group")
    if garment.vertex_groups.get(UN_SHRINK_GROUP) is None:
        garment.vertex_groups.new(name=UN_SHRINK_GROUP)

    verification = verify_weights(garment, transferable, weight_limit)
    bind_to_armature(garment, armature, name="GarmentArmature")

    return {
        "transfer_method": "polyinterp_nearest_surface",
        "weight_limit": weight_limit,
        "armature": armature.name,
        "deform_bones": bone_names,
        "transferable_groups": transferable,
        "excluded_body_groups": sorted(body_groups - set(transferable)),
        "armature_bones_without_body_weights": missing,
        "verts_transferred": transferred,
        "verts_with_fallback": len(unweighted),
        "influence_histogram": {str(k): v for k, v in sorted(influence_histogram.items())},
        "max_influences": per_vertex_influences,
        "mean_influences": round(total_influences / max(transferred, 1), 3),
        "verification": verification,
        "helper_group": UN_SHRINK_GROUP,
    }


def _fallback_weight(garment, vertex_index, armature, garment_groups, matrix, weight_limit):
    """Give an orphan vertex the weight of the closest deform bone."""
    point = matrix @ garment.data.vertices[vertex_index].co
    best_name, best_distance = None, float("inf")
    for name in garment_groups:
        bone = armature.data.bones.get(name)
        if bone is None:
            continue
        head = armature.matrix_world @ bone.head_local
        tail = armature.matrix_world @ bone.tail_local
        segment = tail - head
        length_sq = segment.length_squared
        if length_sq <= 1e-12:
            distance = (point - head).length
        else:
            t = max(0.0, min(1.0, (point - head).dot(segment) / length_sq))
            distance = (point - (head + segment * t)).length
        if distance < best_distance:
            best_name, best_distance = name, distance
    if best_name is None:
        raise PipelineError(f"cannot weight vertex {vertex_index}: no deform bone available")
    garment_groups[best_name].add([vertex_index], 1.0, "REPLACE")


def verify_weights(
    garment, bone_names: List[str], weight_limit: int, tolerance: float = 1e-3
) -> Dict[str, Any]:
    """Assert every vertex is skinned, normalised and within the influence cap."""
    index_to_name = {group.index: group.name for group in garment.vertex_groups}
    allowed = set(bone_names)
    max_influences = 0
    unweighted = 0
    unnormalised = 0
    exceeding = 0
    helper_memberships = 0
    for vertex in garment.data.vertices:
        weights = []
        for membership in vertex.groups:
            name = index_to_name.get(membership.group)
            if name is None or name not in allowed:
                # Helper groups (UN_SHRINK) are not bones, so the armature
                # modifier and both exporters ignore them by construction.
                if name in HELPER_GROUPS and membership.weight > 0.0:
                    helper_memberships += 1
                continue
            if membership.weight > 0.0:
                weights.append(membership.weight)
        if not weights:
            unweighted += 1
            continue
        max_influences = max(max_influences, len(weights))
        if len(weights) > weight_limit:
            exceeding += 1
        total = sum(weights)
        if abs(total - 1.0) > tolerance:
            unnormalised += 1
    helper_group_names = sorted(HELPER_GROUPS & set(index_to_name.values()))
    helper_is_bone = sorted(HELPER_GROUPS & set(bone_names))
    return {
        "max_influences": max_influences,
        "vertices": len(garment.data.vertices),
        "unweighted_vertices": unweighted,
        "unnormalised_vertices": unnormalised,
        "vertices_exceeding_limit": exceeding,
        "helper_groups": helper_group_names,
        "helper_vertices": helper_memberships,
        "helper_groups_colliding_with_bones": helper_is_bone,
        "ok": unweighted == 0 and unnormalised == 0 and exceeding == 0 and not helper_is_bone,
    }


def skin_checkpoint(
    blend_path: str, region: str, params: Dict[str, Any], outfit: str
) -> Dict[str, Any]:
    if not _HAS_BPY:
        return _fail("bpy is not available: run inside Blender")
    try:
        variant = resolve_variant(params.get("variant"), params.get("_body_manifest"))
        load_outfit(outfit, params.get("_outfit_manifest"))
    except ConfigError as exc:
        return _fail(str(exc))

    weight_limit = int(params.get("weight_limit", DEFAULT_WEIGHT_LIMIT))
    open_blend(blend_path)
    garment = find_mesh_object(GARMENT_OBJECT_NAME)
    body = find_mesh_object(params.get("body_object") or variant.body_object)
    if garment is None:
        return _fail(
            f"{GARMENT_OBJECT_NAME!r} not found in {blend_path}; run `shell` and `shrinkwrap` first"
        )
    if body is None:
        return _fail(f"body object {variant.body_object!r} not found in {blend_path}")

    landmarks = infer_landmarks([body.matrix_world @ v.co for v in body.data.vertices])
    rig_params = dict(params)
    rig_params.setdefault("_landmarks", landmarks)
    try:
        rig = ensure_body_rig(body, rig_params)
        result = transfer_weights(garment, body, rig["armature"], weight_limit=weight_limit)
    except PipelineError as exc:
        return _fail(str(exc), {"variant": variant.name})

    verification = result["verification"]
    metrics = {
        "skin": result,
        "rig": {
            "source": rig["rig_source"],
            "skinning_method": rig["skinning_method"],
            "bone_count": rig["bone_count"],
            "bones": rig["bones"],
            "detail": rig.get("skinning_detail"),
        },
        "garment_stats": mesh_stats(garment),
        "weight_limit": weight_limit,
        "variant": variant.name,
    }
    if not verification["ok"]:
        reasons = []
        if verification["unweighted_vertices"]:
            reasons.append(f"{verification['unweighted_vertices']} vertices have no deform weight")
        if verification["unnormalised_vertices"]:
            reasons.append(f"{verification['unnormalised_vertices']} vertices do not sum to 1")
        if verification["vertices_exceeding_limit"]:
            reasons.append(
                f"{verification['vertices_exceeding_limit']} vertices exceed "
                f"{weight_limit} influences"
            )
        if verification["helper_groups_colliding_with_bones"]:
            reasons.append("a helper group collides with a deform bone name")
        metrics["reasons"] = reasons
        return _fail("; ".join(reasons), metrics)
    if result["max_influences"] > weight_limit:
        return _fail(
            f"max influences {result['max_influences']} exceeds the limit {weight_limit}", metrics
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

    result = skin_checkpoint(blend_path, region, params, outfit)
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
    logger.info("skin checkpoint -> %s", checkpoint)
    return result
