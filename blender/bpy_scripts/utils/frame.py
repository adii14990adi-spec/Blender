"""Orientation normalisation and body landmark inference.

The shipped base bodies are authored **Y-down**: the head sits at ``y = -1.81``
and the soles at ``y = 0.01``, with the face pointing towards ``-Z``. Exporting
that unchanged produces an upside-down character in Unity/Godot, and every
coverage/landmark computation would be inverted too.

This module rotates the working scene into the Blender-canonical frame
(+Z up, -Y forward, +X right) and derives the anatomical landmarks the garment
and rig stages need from the actual mesh — no hard-coded magic numbers.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:  # pragma: no cover - inside Blender
    from mathutils import Matrix, Vector

    HAS_MATHUTILS = True
except ImportError:  # pragma: no cover
    Matrix = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    HAS_MATHUTILS = False

from ..config import axis_vector

CANONICAL_UP = "+Z"
CANONICAL_FORWARD = "-Y"


def canonical_rotation(up_axis: str, forward_axis: str):
    """Rotation matrix that maps the asset frame onto canonical Blender space."""
    if not HAS_MATHUTILS:
        raise RuntimeError("mathutils is unavailable: run inside Blender")
    up = Vector(axis_vector(up_axis))
    forward = Vector(axis_vector(forward_axis))
    z_axis = up.normalized()
    y_axis = (-forward).normalized()
    if abs(z_axis.dot(y_axis)) > 0.999:
        raise ValueError("up_axis and forward_axis must be perpendicular")
    x_axis = y_axis.cross(z_axis).normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    # Matrix built from columns maps canonical -> asset; we need asset -> canonical.
    canonical_to_asset = Matrix(
        (
            (x_axis.x, y_axis.x, z_axis.x),
            (x_axis.y, y_axis.y, z_axis.y),
            (x_axis.z, y_axis.z, z_axis.z),
        )
    )
    return canonical_to_asset.transposed()


def apply_orientation(objects: List[Any], rotation) -> None:
    """Rotate every object (and its mesh data) into the canonical frame."""
    matrix = rotation.to_4x4()
    for obj in objects:
        if obj is None:
            continue
        obj.matrix_world = matrix @ obj.matrix_world
    if objects:
        import bpy

        bpy.context.view_layer.update()


def _clusters(values: List[float], gap: float) -> List[List[float]]:
    if not values:
        return []
    ordered = sorted(values)
    clusters: List[List[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] > gap:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def _band(verts, z0: float, z1: float):
    return [v for v in verts if z0 <= v.z <= z1]


def infer_landmarks(world_verts: List[Any]) -> Dict[str, Any]:
    """Derive anatomical landmarks from canonical-space body vertices.

    All returned values are in canonical world units (metres) and are computed
    from the mesh, so male/female variants of different proportions both work.
    """
    if not world_verts:
        raise ValueError("cannot infer landmarks from an empty mesh")
    xs = [v.x for v in world_verts]
    ys = [v.y for v in world_verts]
    zs = [v.z for v in world_verts]
    z_min, z_max = min(zs), max(zs)
    height = z_max - z_min
    if height <= 0:
        raise ValueError("body has zero height")
    max_abs_x = max(abs(x) for x in xs)

    # --- arms: the horizontal limb band, found by the widest x extent --------
    arm_verts = [v for v in world_verts if abs(v.x) > 0.30 * max_abs_x]
    arm_z = (
        sorted(v.z for v in arm_verts)[len(arm_verts) // 2] if arm_verts else z_min + 0.78 * height
    )
    arm_z = min(max(arm_z, z_min + 0.55 * height), z_min + 0.92 * height)
    arm_tip_x = max(abs(v.x) for v in arm_verts) if arm_verts else 0.5 * max_abs_x

    # Arm centre line and radius profile: needed for elbow/wrist placement.
    shoulder_guess = 0.11 * height
    span = max(arm_tip_x - shoulder_guess, 1e-6)
    bins = 24
    profile = []
    for i in range(bins):
        x0 = shoulder_guess + span * i / bins
        x1 = shoulder_guess + span * (i + 1) / bins
        cell = [v for v in arm_verts if x0 <= abs(v.x) < x1]
        if len(cell) < 4:
            continue
        centre_y = sorted(v.y for v in cell)[len(cell) // 2]
        centre_z = sorted(v.z for v in cell)[len(cell) // 2]
        radius = max(((v.y - centre_y) ** 2 + (v.z - centre_z) ** 2) ** 0.5 for v in cell)
        profile.append((abs(cell[0].x), centre_y, centre_z, radius))

    def _extreme(lo: float, hi: float, want_min: bool) -> float:
        window = [p for p in profile if lo <= (p[0] - shoulder_guess) / span <= hi]
        if not window:
            return shoulder_guess + span * (lo + hi) / 2.0
        chosen = min(window, key=lambda p: p[3]) if want_min else max(window, key=lambda p: p[3])
        return chosen[0]

    arm_y = (
        sorted(p[1] for p in profile)[len(profile) // 2] if profile else sorted(ys)[len(ys) // 2]
    )
    arm_centre_z = sorted(p[2] for p in profile)[len(profile) // 2] if profile else arm_z
    arm_radius = sorted(p[3] for p in profile)[len(profile) // 2] if profile else 0.03 * height
    arm_radius = max(arm_radius, 0.012 * height)

    def _on_arm_axis(co, factor: float = 2.2) -> bool:
        """True for vertices that belong to the horizontal arm cylinder."""
        return (
            abs(co.x) > 0.08 * height
            and abs(co.z - arm_centre_z) < factor * arm_radius
            and abs(co.y - arm_y) < factor * arm_radius
        )

    # Torso width at shoulder height, measured with the arms excluded.
    torso_band = _band(world_verts, arm_z - 0.02 * height, arm_z + 0.02 * height)
    torso_half = max(
        (abs(v.x) for v in torso_band if not _on_arm_axis(v)),
        default=0.11 * height,
    )
    shoulder_x = torso_half * 0.95

    elbow_x = max(_extreme(0.32, 0.62, True), shoulder_x + 0.02)
    wrist_x = max(_extreme(0.68, 0.94, True), elbow_x + 0.02)

    # --- legs: walk DOWN from the shoulders until the legs split -------------
    gap = 0.035 * height
    band_height = 0.015 * height
    crotch_z = hip_z_guess = z_min + 0.52 * height
    z = arm_z - 0.10 * height
    while z > z_min + band_height:
        band = _band(world_verts, z - band_height, z)
        if len(band) >= 8 and len(_clusters([v.x for v in band], gap)) >= 2:
            crotch_z = z
            break
        z -= band_height

    crotch_z = max(crotch_z, hip_z_guess - 0.12 * height)
    hip_z = min(crotch_z + 0.035 * height, arm_z - 0.02 * height)
    hip_band = _band(world_verts, hip_z, hip_z + 0.03 * height)
    hip_half = max((abs(v.x) for v in hip_band), default=0.11 * height)
    hip_x = 0.55 * hip_half

    knee_z = crotch_z - 0.47 * (crotch_z - z_min)
    ankle_z = z_min + 0.045 * height
    knee_band = _band(world_verts, knee_z - 0.02 * height, knee_z + 0.02 * height)
    leg_clusters = _clusters([v.x for v in knee_band], gap)
    if len(leg_clusters) >= 2:
        leg_x = (
            sum(leg_clusters[0]) / len(leg_clusters[0])
            + sum(leg_clusters[-1]) / len(leg_clusters[-1])
        ) / 2.0
        leg_x = abs(leg_x)
    else:
        leg_x = hip_x * 0.85

    # --- neck / head ---------------------------------------------------------
    neck_z = arm_z
    narrowest = None
    zz = arm_z + 0.02 * height
    while zz < z_min + 0.95 * height:
        band = _band(world_verts, zz, zz + 0.02 * height)
        if len(band) >= 6:
            width = max(abs(v.x) for v in band)
            if narrowest is None or width < narrowest[1]:
                narrowest = (zz, width)
        zz += 0.01 * height
    if narrowest is not None:
        neck_z = narrowest[0]
    head_z = min(neck_z + 0.09 * height, z_max - 0.05 * height)

    head_band = _band(world_verts, z_min + 0.92 * height, z_max)
    head_centre_y = (
        sorted(v.y for v in head_band)[len(head_band) // 2] if head_band else -0.02 * height
    )
    toe_y = min(ys)

    return {
        "height": height,
        "ground_z": z_min,
        "top_z": z_max,
        "shoulder_z": arm_z,
        "shoulder_x": shoulder_x,
        "torso_half_width": torso_half,
        "arm_tip_x": arm_tip_x,
        "elbow_x": elbow_x,
        "wrist_x": wrist_x,
        "arm_y": arm_y,
        "arm_z": arm_centre_z,
        "arm_radius": arm_radius,
        "crotch_z": crotch_z,
        "hip_z": hip_z,
        "hip_x": hip_x,
        "knee_z": knee_z,
        "ankle_z": ankle_z,
        "leg_x": leg_x,
        "neck_z": neck_z,
        "head_z": head_z,
        "head_centre_y": head_centre_y,
        "centre_y": sorted(ys)[len(ys) // 2],
        "toe_y": toe_y,
        "span_x": max_abs_x,
    }


def height_fraction(z: float, landmarks: Dict[str, Any]) -> float:
    """Convert a canonical z into a 0 (head top) .. 1 (soles) fraction."""
    height = landmarks["height"]
    if height <= 0:
        return 0.0
    return (landmarks["top_z"] - z) / height


def z_from_fraction(fraction: float, landmarks: Dict[str, Any]) -> float:
    return landmarks["top_z"] - fraction * landmarks["height"]
