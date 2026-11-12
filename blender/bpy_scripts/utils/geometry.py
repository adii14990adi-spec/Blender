"""Geometry kernels shared by the garment, fit and skinning stages.

The two functions that used to make the pipeline produce unusable clothes were:

* shrinkwrap: "move each shell vertex toward the nearest body *vertex*";
* weights: "copy the weights of the nearest body *vertex*".

Both are replaced here with nearest-*surface* queries on a BVH tree, with
barycentric interpolation of per-corner data, plus explicit offset enforcement
and Laplacian relaxation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

try:  # pragma: no cover - inside Blender
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    HAS_MATHUTILS = True
except ImportError:  # pragma: no cover
    Vector = None  # type: ignore[assignment]
    BVHTree = None  # type: ignore[assignment]
    HAS_MATHUTILS = False

# Faces below this area are slivers left behind by bisection/solidify.
DEGENERATE_FACE_AREA = 1e-7


class Surface:
    """Triangulated world-space surface of a mesh, queryable with a BVH tree.

    Stores the world-space vertex positions once so every query is in world
    space regardless of the object's ``matrix_world``.
    """

    def __init__(self, obj, depsgraph=None):
        if not HAS_MATHUTILS:
            raise RuntimeError("mathutils is unavailable: run inside Blender")
        mesh = obj.data
        matrix = obj.matrix_world
        self.vertices: List[Any] = [matrix @ v.co for v in mesh.vertices]
        triangles: List[Tuple[int, int, int]] = []
        for poly in mesh.polygons:
            verts = list(poly.vertices)
            if len(verts) < 3:
                continue
            for i in range(1, len(verts) - 1):
                triangles.append((verts[0], verts[i], verts[i + 1]))
        if not triangles:
            raise RuntimeError(f"{obj.name} has no faces; cannot build a surface")
        self.triangles = triangles
        self.tree = BVHTree.FromPolygons(self.vertices, triangles, all_triangles=True, epsilon=0.0)

    def nearest(self, point) -> "tuple[Any, Any, int, float]":
        location, normal, index, distance = self.tree.find_nearest(point)
        return location, normal, index, distance

    def triangle_at(self, index: int) -> Tuple[int, int, int]:
        return self.triangles[index]

    def signed_clearance(self, point) -> float:
        """Distance to the surface, positive when ``point`` is outside the body."""
        location, normal, _index, distance = self.tree.find_nearest(point)
        if location is None:
            return float("inf")
        return distance if (point - location).dot(normal) >= 0.0 else -distance


def barycentric(point, a, b, c) -> Tuple[float, float, float]:
    """Barycentric coordinates of ``point`` projected onto triangle abc."""
    v0 = b - a
    v1 = c - a
    v2 = point - a
    d00 = v0.dot(v0)
    d01 = v0.dot(v1)
    d11 = v1.dot(v1)
    d20 = v2.dot(v0)
    d21 = v2.dot(v1)
    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-20:
        return (1.0, 0.0, 0.0)
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    return (u, v, w)


def _clamp_weights(
    weights: List[float], limit: int, epsilon: float = 1e-5
) -> List[Tuple[int, float]]:
    """Keep the strongest ``limit`` influences and renormalise them to sum 1."""
    indexed = [(i, w) for i, w in enumerate(weights) if w > epsilon]
    indexed.sort(key=lambda item: item[1], reverse=True)
    kept = indexed[:limit]
    total = sum(w for _, w in kept)
    if total <= 0.0 or not kept:
        return []
    return [(i, w / total) for i, w in kept]


def barycentric_surface_weights(
    point,
    surface: Surface,
    vertex_weights: Sequence[Dict[int, float]],
    limit: int = 4,
) -> Tuple[Dict[int, float], Dict[str, float]]:
    """Transfer weights from the nearest *surface point* of ``surface``.

    This is the manual equivalent of Blender's Data Transfer modifier with the
    ``POLYINTERP_NEAREST`` mapping: the nearest triangle is located on the body,
    and the three corner vertices' weights are interpolated with barycentric
    coordinates. Unlike nearest-vertex transfer this is continuous across the
    garment, so no seams or popping appear when the character deforms.
    """
    location, _normal, index, _distance = surface.nearest(point)
    if location is None or index is None:
        return {}, {"distance": float("inf"), "triangle": -1}
    tri = surface.triangle_at(index)
    a, b, c = (surface.vertices[i] for i in tri)
    u, v, w = barycentric(location, a, b, c)
    bary = (max(0.0, u), max(0.0, v), max(0.0, w))
    total = sum(bary)
    bary = tuple(x / total for x in bary) if total > 0 else (1.0, 0.0, 0.0)

    blended: Dict[int, float] = {}
    for weight, vertex_index in zip(bary, tri, strict=False):
        if weight <= 0.0:
            continue
        for group_index, group_weight in vertex_weights[vertex_index].items():
            blended[group_index] = blended.get(group_index, 0.0) + weight * group_weight

    order = sorted(blended)
    clamped = _clamp_weights([blended[i] for i in order], limit)
    result = {order[i]: w for i, w in clamped}
    return result, {
        "distance": float(_distance),
        "triangle": int(index),
        "barycentric": [round(x, 4) for x in bary],
    }


def vertex_weight_table(
    obj, allowed_group_names: Iterable[str] | None = None
) -> List[Dict[int, float]]:
    """Per-vertex ``{vertex_group_index: weight}`` table.

    ``allowed_group_names`` restricts the table to deform-bone groups, which is
    how helper groups such as ``UN_SHRINK`` are kept out of the skin weights.
    """
    index_of = {group.index: group.name for group in obj.vertex_groups}
    allowed = set(allowed_group_names) if allowed_group_names is not None else None
    table: List[Dict[int, float]] = []
    for vertex in obj.data.vertices:
        entry: Dict[int, float] = {}
        for membership in vertex.groups:
            name = index_of.get(membership.group)
            if name is None:
                continue
            if allowed is not None and name not in allowed:
                continue
            if membership.weight <= 0.0:
                continue
            entry[membership.group] = membership.weight
        table.append(entry)
    return table


def offset_along_normals(obj, amount: float, ramp=None) -> int:
    """Push every vertex along its smooth normal by ``amount`` (or ``ramp(co)``)."""
    mesh = obj.data
    mesh.calc_normals_split() if hasattr(mesh, "calc_normals_split") else None
    moved = 0
    normals = [v.normal.copy() for v in mesh.vertices]
    for vertex, normal in zip(mesh.vertices, normals, strict=False):
        distance = ramp(vertex.co, strict=False) if ramp is not None else amount
        vertex.co = vertex.co + normal * distance
        moved += 1
    mesh.update()
    return moved


def edge_list(mesh) -> List[Tuple[int, int]]:
    return [(edge.vertices[0], edge.vertices[1]) for edge in mesh.edges]


def laplacian_relax(
    positions: List[Any],
    edges: Sequence[Tuple[int, int]],
    iterations: int = 3,
    factor: float = 0.5,
    fixed: Iterable[int] | None = None,
) -> None:
    """In-place uniform Laplacian smoothing of ``positions`` (world space)."""
    fixed_set = set(fixed or ())
    if iterations <= 0 or not edges:
        return
    neighbours: List[List[int]] = [[] for _ in positions]
    for a, b in edges:
        neighbours[a].append(b)
        neighbours[b].append(a)
    for _ in range(iterations):
        updated = list(positions)
        for index, position in enumerate(positions):
            if index in fixed_set:
                continue
            ring = neighbours[index]
            if not ring:
                continue
            average = Vector((0.0, 0.0, 0.0))
            for other in ring:
                average += positions[other]
            average /= len(ring)
            updated[index] = position.lerp(average, factor)
        positions[:] = updated


def smooth_displacements(
    displacements: List[Any],
    edges: Sequence[Tuple[int, int]],
    iterations: int = 4,
    factor: float = 0.5,
    fixed: Iterable[int] | None = None,
) -> None:
    """Laplacian-smooth a displacement field (in place).

    Smoothing the *displacement* instead of the vertex positions keeps the
    garment's own shape: every vertex moves by a smooth approximation of its
    required correction, so neighbouring vertices move together and edge
    lengths barely change.
    """
    fixed_set = set(fixed or ())
    if iterations <= 0 or not edges:
        return
    neighbours: List[List[int]] = [[] for _ in displacements]
    for a, b in edges:
        neighbours[a].append(b)
        neighbours[b].append(a)
    for _ in range(iterations):
        updated = list(displacements)
        for index, delta in enumerate(displacements):
            if index in fixed_set:
                continue
            ring = neighbours[index]
            if not ring:
                continue
            average = Vector((0.0, 0.0, 0.0))
            for other in ring:
                average += displacements[other]
            average /= len(ring)
            updated[index] = delta.lerp(average, factor)
        displacements[:] = updated


def fit_to_surface(
    obj,
    surface: Surface,
    offset: float,
    iterations: int = 2,
    relax_iterations: int = 4,
    relax_factor: float = 0.5,
    keep_indices: Iterable[int] | None = None,
    offset_ramp=None,
) -> Dict[str, Any]:
    """Fit ``obj`` onto ``surface`` with a smooth, shape-preserving offset.

    1. every vertex is projected to the nearest point of the body surface plus
       the requested standoff (a true shrinkwrap projection, not nearest-vertex);
    2. the resulting *displacement field* is Laplacian-smoothed so adjacent
       vertices move together — this is what keeps edge lengths and therefore
       the garment's shape intact;
    3. a final clearance pass pushes any remaining violator out of the body.
    """
    mesh = obj.data
    matrix = obj.matrix_world
    inverse = matrix.inverted()
    keep = set(keep_indices or ())
    world = [matrix @ v.co for v in mesh.vertices]
    before = [p.copy() for p in world]

    def wanted_at(point) -> float:
        return offset_ramp(point) if offset_ramp is not None else offset

    targets: List[Any] = []
    for index, point in enumerate(world):
        location, normal, _tri, distance = surface.nearest(point)
        wanted = wanted_at(point)
        if location is None or normal is None:
            targets.append(point.copy())
            continue
        if index in keep and distance >= wanted:
            targets.append(point.copy())  # pinned hem/collar/cuff vertices
        else:
            targets.append(location + normal * wanted)

    displacements = [target - point for target, point in zip(targets, world, strict=False)]
    smooth_displacements(displacements, edge_list(mesh), relax_iterations, relax_factor, keep)
    if iterations > 1:
        # Second projection pass on the smoothed result, then re-smooth lightly.
        moved = [point + delta for point, delta in zip(world, displacements, strict=False)]
        for index, point in enumerate(moved):
            location, normal, _tri, distance = surface.nearest(point)
            if location is None or normal is None or index in keep:
                continue
            wanted = wanted_at(point)
            if distance < wanted:
                displacements[index] += (location + normal * wanted) - point
        smooth_displacements(
            displacements, edge_list(mesh), max(1, relax_iterations // 2), relax_factor * 0.5, keep
        )
    world = [point + delta for point, delta in zip(world, displacements, strict=False)]

    # Final clearance guarantee.
    enforcement = 0
    for _ in range(4):
        violations = 0
        for index, point in enumerate(world):
            location, normal, _tri, distance = surface.nearest(point)
            if location is None or normal is None:
                continue
            wanted = wanted_at(point)
            if distance < wanted:
                world[index] = location + normal * wanted
                violations += 1
        enforcement += violations
        if violations == 0:
            break

    clearances: List[float] = []
    penetrating = 0
    for point in world:
        location, normal, _tri, distance = surface.nearest(point)
        if location is None:
            continue
        sign = 1.0 if (point - location).dot(normal) >= 0 else -1.0
        clearances.append(sign * distance)
        if distance < wanted_at(point) * 0.999:
            penetrating += 1

    for vertex, point in zip(mesh.vertices, world, strict=False):
        vertex.co = inverse @ point
    mesh.update()

    displacement = [(a - b).length for a, b in zip(world, before, strict=False)]
    return {
        "vertices_moved": sum(1 for d in displacement if d > 1e-6),
        "max_displacement": round(max(displacement), 6) if displacement else 0.0,
        "mean_displacement": round(sum(displacement) / len(displacement), 6)
        if displacement
        else 0.0,
        "min_clearance": round(min(clearances), 6) if clearances else 0.0,
        "mean_clearance": round(sum(clearances) / len(clearances), 6) if clearances else 0.0,
        "max_clearance": round(max(clearances), 6) if clearances else 0.0,
        "verts_below_offset": penetrating,
        "pinned_vertices": len(keep),
        "clearance_corrections": enforcement,
    }


def count_penetrations(obj, surface: Surface, min_clearance: float = 0.0) -> int:
    """Vertices whose distance to the body surface is below ``min_clearance``.

    Uses the unsigned distance: after the fit every vertex sits exactly
    ``offset`` away from the surface, and the sign of the dot product with the
    surface normal is unreliable on concave anatomy (armpits, neck, crotch).
    """
    matrix = obj.matrix_world
    count = 0
    for vertex in obj.data.vertices:
        _location, _normal, _index, distance = surface.nearest(matrix @ vertex.co)
        if distance is None or distance < min_clearance:
            count += 1
    return count


def solidify_mesh(obj, thickness: float, offset: float = 1.0) -> Dict[str, Any]:
    """Add real garment thickness with a Solidify modifier and apply it.

    ``offset=1.0`` keeps the original surface as the outside and grows the
    shell inwards, so the standoff computed against the body is preserved.
    """
    import bpy

    modifier = obj.modifiers.new(name="GarmentThickness", type="SOLIDIFY")
    modifier.thickness = thickness
    modifier.offset = offset
    # Even thickness divides by the corner angle and explodes into long spikes
    # on the sliver triangles a game body mesh is full of; plain normals are
    # stable and visually identical at garment thicknesses.
    modifier.use_even_offset = False
    modifier.use_quality_normals = True
    modifier.use_rim = True
    modifier.use_rim_only = False
    from .bpy_helpers import apply_modifier, set_active

    set_active(obj)
    applied = apply_modifier(obj, modifier.name)
    if not applied:  # pragma: no cover - context failure fallback
        modifier.show_viewport = True

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated)
        obj.modifiers.clear()
        obj.data = mesh
    return {"thickness": thickness, "solidify_applied": applied}


def repair_and_solidify(obj, thickness: float) -> Dict[str, Any]:
    """Turn a fitted single-surface panel into a watertight shell with thickness.

    Done *after* the surface fit on purpose: projecting a double-walled shell
    would collapse its two walls onto the same offset surface, which is what
    produced the enormous edge distortion (and intersecting cloth) before.

    Pinch ("bowtie") vertices — where two boundary loops of the panel touch —
    are split first, otherwise Solidify emits four rim faces on one edge and the
    result is non-manifold.
    """
    import bmesh
    import bpy

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.verts.ensure_lookup_table()
    loose = [vertex for vertex in bm.verts if not vertex.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    pinch_vertices = 0
    pinch_edges = set()
    for vertex in bm.verts:
        if not vertex.is_manifold:
            pinch_vertices += 1
            pinch_edges.update(vertex.link_edges)
    if pinch_edges:
        bmesh.ops.split_edges(bm, edges=list(pinch_edges))
    bm.verts.ensure_lookup_table()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    modifier = obj.modifiers.new(name="GarmentThickness", type="SOLIDIFY")
    modifier.thickness = thickness
    modifier.offset = 1.0  # grow inwards, keep the fitted outer surface
    modifier.use_even_offset = False
    modifier.use_quality_normals = True
    modifier.use_rim = True
    modifier.use_rim_only = False

    from .bpy_helpers import apply_modifier, set_active

    set_active(obj)
    applied = apply_modifier(obj, modifier.name)
    if not applied:  # pragma: no cover - context-dependent fallback
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated)
        obj.modifiers.clear()
        obj.data = mesh

    # Solidify leaves a handful of sub-millimetre sliver faces along the rim.
    # They are harmless visually but make the topology validator (and some
    # engines' lightmap UV packing) reject the mesh, so collapse them - and
    # only keep the result if the shell stays closed.
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    slivers = sum(1 for face in bm.faces if face.calc_area() < DEGENERATE_FACE_AREA)
    if slivers:
        keeper = bmesh.new()
        keeper.from_mesh(obj.data)
        bmesh.ops.dissolve_degenerate(bm, dist=1.0e-3, edges=bm.edges[:])
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        broken = any(len(edge.link_faces) > 2 for edge in bm.edges) or any(
            len(edge.link_faces) == 1 for edge in bm.edges
        )
        if broken or bm.calc_volume(signed=True) <= 0:
            bm.free()
            bm = keeper
            slivers_removed = 0
        else:
            keeper.free()
            slivers_removed = slivers
            bm.to_mesh(obj.data)
            obj.data.update()
    else:
        slivers_removed = 0

    bm2 = bmesh.new()
    bm2.from_mesh(obj.data)
    non_manifold = sum(1 for edge in bm2.edges if len(edge.link_faces) > 2)
    boundary = sum(1 for edge in bm2.edges if len(edge.link_faces) == 1)
    volume = bm2.calc_volume(signed=True)
    bmesh.ops.recalc_face_normals(bm2, faces=bm2.faces[:])
    bm2.to_mesh(obj.data)
    bm2.free()
    bm.free()
    obj.data.update()

    return {
        "thickness": thickness,
        "solidify_applied": applied,
        "sliver_faces_removed": slivers_removed,
        "degenerate_faces": sum(
            1 for polygon in obj.data.polygons if polygon.area < DEGENERATE_FACE_AREA
        ),
        "pinch_vertices_split": pinch_vertices,
        "split_edges": len(pinch_edges),
        "non_manifold_edges": non_manifold,
        "boundary_edges": boundary,
        "volume_m3": round(volume, 8),
        "vertex_count": len(obj.data.vertices),
        "face_count": len(obj.data.polygons),
    }
