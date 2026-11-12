"""Stage 01 — surface-accurate garment fitting.

The old implementation moved every shell vertex towards the nearest body
*vertex* by a fixed distance. On a 6k-vertex character that produces a garment
that follows the body's vertex density instead of its surface: uneven offsets,
distorted panels and visible gaps.

This stage projects each vertex onto the nearest point of the body *surface*
(BVH tree), pushes it out along the surface normal by the requested offset,
relaxes the result with a fixed Laplacian pass to remove triangle-level noise,
and then re-projects so that no vertex ends up inside the body. Vertices tagged
``UN_SHRINK`` (hem, collar, cuffs) keep their shape but still cannot penetrate.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, load_outfit, resolve_variant
from ..utils.bpy_helpers import (
    PipelineError,
    find_mesh_object,
    mesh_stats,
    open_blend,
    save_blend,
    set_active,
)
from ..utils.frame import height_fraction, infer_landmarks
from ..utils.geometry import Surface, count_penetrations, fit_to_surface, repair_and_solidify
from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, outfit_dir

logger = get_logger("stages.shrinkwrap")

STAGE_INDEX = 1
STAGE_NAME = "shrinkwrap"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
PIECE_OBJECT_PREFIX = "CLOTH_Shell__"
DETAIL_OBJECT_PREFIX = "CLOTH_Detail__"
PIECE_META_KEY = "garment_pieces"
DETAIL_META_KEY = "garment_details"
UN_SHRINK_GROUP = "UN_SHRINK"
DEFAULT_OFFSET = 0.014
MAX_ALLOWED_DISTORTION = 3.0  # worst single edge (relative length change)
MAX_P99_DISTORTION = 0.5  # 99th percentile of edges above 3 mm


def _fail(error: str, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "fail",
        "stage": STAGE_NAME,
        "metrics": metrics or {},
        "checkpoint": None,
        "error": error,
    }


def _edge_lengths(obj):
    matrix = obj.matrix_world
    return [
        (matrix @ obj.data.vertices[a].co - matrix @ obj.data.vertices[b].co).length
        for a, b in (edge.vertices for edge in obj.data.edges)
    ]


def _distortion(before, after, min_edge_length: float = 3e-3) -> Dict[str, float]:
    """Edge-length change statistics, ignoring sub-millimetre sliver edges."""
    ratios = [
        abs(new / old - 1.0)
        for old, new in zip(before, after, strict=False)
        if old >= min_edge_length
    ]
    if not ratios:
        return {"max": 0.0, "p99": 0.0, "edges_measured": 0}
    ratios.sort()
    return {
        "max": round(ratios[-1], 4),
        "p99": round(ratios[min(len(ratios) - 1, int(0.99 * len(ratios)))], 4),
        "edges_measured": len(ratios),
    }


def fit_garment(
    garment, body, settings: Dict[str, Any], landmarks, params: Dict[str, Any]
) -> Dict[str, Any]:
    base_offset = float(
        params.get("offset", (settings.get("offset") or {}).get("default", DEFAULT_OFFSET))
    )
    if base_offset <= 0:
        raise PipelineError(f"offset must be > 0, got {base_offset}")

    iterations = int(params.get("iterations", 2))
    relax_iterations = int(params.get("relax_iterations", 6))
    coverage = settings.get("coverage") or {}
    top = float(coverage.get("top", 0.19))
    bottom = float(coverage.get("bottom", 0.58))
    offsets = settings.get("offset") or {}
    hem_extra = float(offsets.get("hem_extra", 0.0))

    def ramp(point) -> float:
        fraction = height_fraction(point.z, landmarks)
        span = max(bottom - top, 1e-6)
        position = (fraction - top) / span
        hem = max(0.0, min(1.0, (position - 0.62) / 0.38))
        return base_offset + hem_extra * hem * hem

    surface = Surface(body)
    group = garment.vertex_groups.get(UN_SHRINK_GROUP)
    pinned = []
    if group is not None:
        for vertex in garment.data.vertices:
            for membership in vertex.groups:
                if membership.group == group.index and membership.weight > 0.5:
                    pinned.append(vertex.index)

    before = _edge_lengths(garment)
    result = fit_to_surface(
        garment,
        surface,
        offset=base_offset,
        iterations=iterations,
        relax_iterations=relax_iterations,
        relax_factor=float(params.get("relax_factor", 0.5)),
        keep_indices=pinned,
        offset_ramp=ramp,
    )
    after = _edge_lengths(garment)
    distortion = _distortion(before, after)
    result["edge_distortion"] = distortion
    result["max_edge_distortion"] = distortion["max"]
    result["offset_requested"] = base_offset
    # A vertex that is merely closer to the body than the requested standoff is
    # a tight fit (armpits, crotch). A vertex at distance 0 is actual
    # poke-through; both are reported, only the latter is a hard failure.
    result["penetrating_vertices"] = count_penetrations(garment, surface, min_clearance=1e-4)
    result["tight_vertices"] = count_penetrations(garment, surface, min_clearance=base_offset * 0.5)
    result["vertices"] = len(garment.data.vertices)
    return result


def _piece_plan(garment) -> Dict[str, Any]:
    """Read the piece/detail plan the shell stage stored on the object."""
    import json

    pieces = []
    raw_pieces = garment.get(PIECE_META_KEY)
    if raw_pieces:
        try:
            pieces = json.loads(raw_pieces)
        except (TypeError, ValueError):  # pragma: no cover - corrupt metadata
            pieces = []
    details = []
    raw_details = garment.get(DETAIL_META_KEY)
    if raw_details:
        try:
            details = json.loads(raw_details)
        except (TypeError, ValueError):  # pragma: no cover - corrupt metadata
            details = []
    return {"pieces": pieces, "details": details}


def _piece_settings(settings: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Build the fit settings for one piece from the (already resolved) plan."""
    material = meta.get("material") or {}
    material = {**material, "textures": material.get("textures") or {}}
    return {
        "coverage": meta.get("coverage") or settings.get("coverage"),
        "offset": meta.get("offset") or settings.get("offset"),
        "thickness": float(meta.get("thickness") or settings.get("thickness") or 0.006),
        "sleeves": meta.get("sleeves") or {},
        "opening": meta.get("opening"),
        "fit": meta.get("fit"),
        "material": material,
        "key": meta.get("key"),
    }


def _join_into_main(main, others) -> Dict[str, Any]:
    """Join every piece and detail object into ``CLOTH_Shell``."""
    import bpy

    joined = [obj for obj in others if obj is not None and obj.name != main.name]
    if not joined:  # pragma: no cover - single-piece outfits
        return {"joined_objects": 0}
    names = [obj.name for obj in joined]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in joined:
        obj.select_set(True)
    main.select_set(True)
    set_active(main)
    bpy.ops.object.join()
    main.name = GARMENT_OBJECT_NAME
    main.data.name = f"{GARMENT_OBJECT_NAME}Mesh"
    return {"joined_objects": len(joined), "joined_from": names}


def _fit_pieces(garment, body, settings, landmarks, params, plan) -> Dict[str, Any]:
    """Fit, thicken and validate every piece, then join them into one object."""
    import bpy

    pieces = plan["pieces"]
    details = plan["details"]
    main = bpy.data.objects.get(pieces[0].get("object") or "") or garment
    results: List[Dict[str, Any]] = []
    failures: List[str] = []
    for meta in pieces:
        obj = bpy.data.objects.get(meta.get("object") or "")
        if obj is None:
            failures.append(f"piece object {meta.get('object')!r} is missing from the checkpoint")
            continue
        piece_settings = _piece_settings(settings, meta)
        piece_params = {**params, **(meta.get("fit_params") or {})}
        try:
            if str(meta.get("fit_mode") or "surface").lower() == "shell":
                fit = _shell_only_fit(obj, body, piece_settings)
            else:
                fit = fit_garment(obj, body, piece_settings, landmarks, piece_params)
            thickness = piece_settings["thickness"]
            if thickness <= 0:
                raise PipelineError("outfit thickness must be > 0")
            shell = repair_and_solidify(obj, thickness)
        except PipelineError as exc:
            failures.append(f"{meta.get('key')}: {exc}")
            continue
        problems = _piece_problems(shell, fit, params, thickness)
        failures.extend(f"{meta.get('key')}: {problem}" for problem in problems)
        results.append(
            {
                "key": meta.get("key"),
                "object": obj.name,
                "region": meta.get("region"),
                "material": (meta.get("material") or {}).get("name"),
                "thickness": thickness,
                "fit": fit,
                "shell": shell,
            }
        )

    detail_objects = []
    for meta in details:
        obj = bpy.data.objects.get(meta.get("object") or "")
        if obj is not None:
            detail_objects.append(obj)

    if failures:
        return {"pieces": results, "failures": failures, "joined": None}

    to_join = [bpy.data.objects[result["object"]] for result in results]
    joined = _join_into_main(main, to_join + detail_objects)
    joined["stats"] = mesh_stats(main)
    joined["material_slots"] = [
        slot.material.name if slot.material else None for slot in main.material_slots
    ]
    return {"pieces": results, "failures": [], "joined": joined}


def _shell_only_fit(obj, body, piece_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Report a piece the shell stage already fitted, without re-projecting.

    A second nearest-surface projection is a fix-up for big panels; on a narrow
    belt band or a boot wrapped around a foot it does more damage than good
    (the surface curves away faster than the standoff), so those pieces keep the
    offset the shell stage produced and are only measured here.
    """
    surface = Surface(body)
    matrix = obj.matrix_world
    distances = [surface.nearest(matrix @ vertex.co)[3] for vertex in obj.data.vertices]
    distances = [d for d in distances if d is not None]
    if not distances:
        raise PipelineError("could not measure the piece against the body surface")
    return {
        "min_clearance": round(min(distances), 6),
        "mean_clearance": round(sum(distances) / len(distances), 6),
        "max_clearance": round(max(distances), 6),
        "max_edge_distortion": 0.0,
        "edge_distortion": {"max": 0.0, "p99": 0.0, "edges_measured": 0},
        "penetrating_vertices": sum(1 for d in distances if d <= 1e-4),
        "tight_vertices": sum(
            1
            for d in distances
            if d < 0.5 * float((piece_settings.get("offset") or {}).get("default", 0.014))
        ),
        "vertices": len(distances),
        "offset_requested": float((piece_settings.get("offset") or {}).get("default", 0.014)),
        "fit_mode": "shell",
        "skipped_projection": True,
    }


def _piece_problems(shell: Dict[str, Any], fit: Dict[str, Any], params, thickness: float):
    """Per-piece acceptance checks (same bars the single-garment path uses)."""
    problems = []
    if shell["non_manifold_edges"] or shell["boundary_edges"]:
        problems.append(
            "not watertight after applying thickness "
            f"({shell['non_manifold_edges']} non-manifold edges, "
            f"{shell['boundary_edges']} boundary edges)"
        )
    if shell["volume_m3"] <= 0:
        problems.append(f"volume {shell['volume_m3']} is not positive (inverted normals)")
    if fit["min_clearance"] <= float(params.get("min_clearance", 0.0)):
        problems.append(
            f"penetrates the body (min clearance {fit['min_clearance']:.4f} m); raise `offset`"
        )
    max_distortion = float(params.get("max_distortion", MAX_ALLOWED_DISTORTION))
    p99_limit = float(params.get("p99_distortion", MAX_P99_DISTORTION))
    if fit["max_edge_distortion"] > max_distortion:
        problems.append(
            f"edge distortion {fit['max_edge_distortion']:.2f} exceeds {max_distortion:.2f}"
        )
    if fit["edge_distortion"]["p99"] > p99_limit:
        problems.append(
            f"99th percentile edge distortion {fit['edge_distortion']['p99']:.2f} "
            f"exceeds {p99_limit:.2f}"
        )
    if thickness <= 0:  # pragma: no cover - guarded by the caller
        problems.append("thickness must be > 0")
    return problems


def _fit_multi_piece(
    garment, body, settings, landmarks, params, plan, outfit: str
) -> Dict[str, Any]:
    """Fit every piece on its own and report the result as one garment."""
    pieces = _fit_pieces(garment, body, settings, landmarks, params, plan)
    if pieces["failures"]:
        return _fail(
            "; ".join(pieces["failures"]),
            {
                "pieces": [
                    {"key": item["key"], "fit": item["fit"], "shell": item["shell"]}
                    for item in pieces["pieces"]
                ],
                "variant": params.get("variant"),
                "outfit": outfit,
            },
        )
    results = pieces["pieces"]
    main = results[0]
    joined = pieces["joined"] or {}
    aggregate_fit = dict(main["fit"])
    aggregate_fit["min_clearance"] = min(item["fit"]["min_clearance"] for item in results)
    aggregate_fit["pieces"] = [
        {
            "key": item["key"],
            "object": item["object"],
            "material": item["material"],
            "thickness": item["thickness"],
            "fit_mode": item["fit"].get("fit_mode", "surface"),
            "min_clearance": item["fit"]["min_clearance"],
            "max_edge_distortion": item["fit"]["max_edge_distortion"],
            "p99_edge_distortion": item["fit"]["edge_distortion"]["p99"],
            "volume_m3": item["shell"]["volume_m3"],
        }
        for item in results
    ]
    aggregate_shell = dict(main["shell"])
    aggregate_shell["volume_m3"] = round(sum(item["shell"]["volume_m3"] for item in results), 6)
    metrics = {
        "fit": aggregate_fit,
        "shell": aggregate_shell,
        "garment_stats": joined.get("stats") or mesh_stats(garment),
        "material_slots": joined.get("material_slots") or [],
        "joined": joined,
        "pieces": [
            {
                "key": item["key"],
                "object": item["object"],
                "region": item["region"],
                "thickness": item["thickness"],
                "fit_mode": item["fit"].get("fit_mode", "surface"),
                "fit": item["fit"],
                "shell": item["shell"],
            }
            for item in results
        ],
        "detail_objects": [detail.get("object") for detail in plan["details"]],
        "body_object": body.name,
        "variant": params.get("variant"),
        "outfit": outfit,
        "criteria": {
            "min_clearance": float(params.get("min_clearance", 0.0)),
            "max_allowed_edge_distortion": float(
                params.get("max_distortion", MAX_ALLOWED_DISTORTION)
            ),
            "max_allowed_p99_edge_distortion": float(
                params.get("p99_distortion", MAX_P99_DISTORTION)
            ),
        },
    }
    logger.info(
        "fitted %d pieces + %d details into %s (%d material slots)",
        len(results),
        len(plan["details"]),
        joined.get("stats", {}).get("face_count", "?"),
        len(metrics["material_slots"]),
    )
    return {"status": "pass", "stage": STAGE_NAME, "metrics": metrics, "checkpoint": None}


def fit_checkpoint(
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
    body = find_mesh_object(params.get("body_object") or variant.body_object)
    if garment is None:
        return _fail(
            f"{GARMENT_OBJECT_NAME!r} not found in {blend_path}; run the `shell` stage first",
            {"objects": [obj.name for obj in bpy.data.objects]},
        )
    if body is None:
        return _fail(f"body object {variant.body_object!r} not found in {blend_path}")

    landmarks = infer_landmarks([body.matrix_world @ v.co for v in body.data.vertices])
    plan = _piece_plan(garment)
    if plan["pieces"] and (len(plan["pieces"]) > 1 or plan["details"]):
        return _fit_multi_piece(garment, body, settings, landmarks, params, plan, outfit)

    try:
        result = fit_garment(garment, body, settings, landmarks, params)
        thickness = float(params.get("thickness", (settings.get("thickness") or 0.006)))
        if thickness <= 0:
            raise PipelineError("outfit thickness must be > 0")
        shell = repair_and_solidify(garment, thickness)
    except PipelineError as exc:
        return _fail(str(exc))

    if shell["non_manifold_edges"] or shell["boundary_edges"]:
        return _fail(
            "garment is not watertight after applying thickness "
            f"({shell['non_manifold_edges']} non-manifold edges, "
            f"{shell['boundary_edges']} boundary edges)",
            {"fit": result, "shell": shell},
        )
    if shell["volume_m3"] <= 0:
        return _fail(
            f"garment volume {shell['volume_m3']} is not positive (inverted normals)",
            {"shell": shell},
        )

    min_clearance = float(params.get("min_clearance", 0.0))
    max_distortion = float(params.get("max_distortion", MAX_ALLOWED_DISTORTION))
    p99_limit = float(params.get("p99_distortion", 0.5))
    ok = (
        result["min_clearance"] > min_clearance
        and result["max_edge_distortion"] <= max_distortion
        and result["edge_distortion"]["p99"] <= p99_limit
    )

    metrics = {
        "fit": result,
        "shell": shell,
        "garment_stats": mesh_stats(garment),
        "body_object": body.name,
        "variant": params.get("variant"),
        "criteria": {
            "min_clearance": min_clearance,
            "max_allowed_edge_distortion": max_distortion,
            "max_allowed_p99_edge_distortion": p99_limit,
        },
    }
    if not ok:
        reasons = []
        if result["min_clearance"] <= min_clearance:
            reasons.append(
                f"garment penetrates the body (min clearance {result['min_clearance']:.4f} m); "
                "raise `offset`"
            )
        if result["max_edge_distortion"] > max_distortion:
            reasons.append(
                f"edge distortion {result['max_edge_distortion']:.2f} exceeds {max_distortion:.2f} "
                "(garment is stretched; raise `relax_iterations`)"
            )
        if result["edge_distortion"]["p99"] > p99_limit:
            reasons.append(
                f"99th percentile edge distortion {result['edge_distortion']['p99']:.2f} "
                f"exceeds {p99_limit:.2f}"
            )
        metrics["reasons"] = reasons
        return _fail("; ".join(reasons), metrics)
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

    result = fit_checkpoint(blend_path, region, params, outfit)
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
    logger.info("fit checkpoint -> %s", checkpoint)
    return result
