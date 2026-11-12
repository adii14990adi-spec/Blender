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
from typing import Any, Dict

try:  # pragma: no cover
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..config import ConfigError, load_outfit, resolve_variant
from ..utils.bpy_helpers import PipelineError, find_mesh_object, mesh_stats, open_blend, save_blend
from ..utils.frame import height_fraction, infer_landmarks
from ..utils.geometry import Surface, count_penetrations, fit_to_surface, repair_and_solidify
from ..utils.logging import get_logger
from ..utils.paths import checkpoint_path, ensure_dir, outfit_dir

logger = get_logger("stages.shrinkwrap")

STAGE_INDEX = 1
STAGE_NAME = "shrinkwrap"
GARMENT_OBJECT_NAME = "CLOTH_Shell"
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
