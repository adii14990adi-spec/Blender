# Sub-prompt 02 — Shrinkwrap + thickness

Stage: `shrinkwrap` (alias: `fit`)
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Fit the panel onto the body with a real, measurable standoff and give it
thickness — without collapsing the offset, tearing the mesh or intersecting the
body.

## Steps (bpy, executed by the stage script)
1. Load the upstream checkpoint (`00_garment.blend`), or fail with "run the
   `shell` stage first".
2. Build a BVH surface of the body and push every vertex out to
   `nearest_point + normal * offset(vertex)` (offset follows the same ramp as the
   shell stage). Push-out only: vertices that are already far enough are left
   alone, so the hem's standoff is not dragged onto the skin.
3. Smooth the displacement **field** (Laplacian, `relax_iterations` ×
   `relax_factor`), re-project, then run a clearance pass for any remaining
   violators. Pinned (`UN_SHRINK`) vertices never move inward.
4. `repair_and_solidify`: weld, split pinch vertices, recalc normals, then apply
   Solidify (`offset=1.0` so the fitted surface stays the outer one,
   `use_even_offset=False`, `use_rim=True`). Slivers are dissolved and the result
   is kept only if the shell stays closed.
5. Save `outputs/<variant>/<outfit>/01_fitted.blend`.

## Gates (all fail the stage, none are warnings)
- watertight: `boundary_edges == 0` and `non_manifold_edges == 0`
- `volume_m3 > 0`, `degenerate_faces == 0`
- `min_clearance > 0`, `penetrating_vertices == 0`
- edge distortion (edges ≥ 3 mm): `max ≤ 3.0`, `p99 ≤ 0.5`

## Params
- `outfit`, `variant`
- `relax_iterations` (default 6), `relax_factor` (default 0.5)
- offsets come from the outfit, not from stage params

## Failure recovery
- `min_clearance < 0` / penetrations → raise `offset.default` in the outfit.
- distortion too high → raise `relax_iterations`, or lower `hem_extra` /
  `sleeve_extra` (a very flared hem stretches the edges next to it).
