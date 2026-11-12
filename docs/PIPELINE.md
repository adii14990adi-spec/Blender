# Pipeline reference

What each stage guarantees, the exact gates it enforces, and the checkpoint it
leaves behind. Anything a stage cannot guarantee is listed in
[LIMITATIONS.md](LIMITATIONS.md) instead of being silently ignored.

```
shell → shrinkwrap → weights → uv_material → export
```

`pipeline.STAGE_ORDER` is the single source of truth for that order.
`uv`/`material`/`materials` are *aliases* of `uv_material`, not extra stages:
the earlier pipeline ran that module twice, which duplicated UV layers and
re-applied the material.

## Inputs

| Input | Resolved by | Notes |
| --- | --- | --- |
| Body variant | `assets/base_body/manifest.json` | `male` → `base_body_Male.blend`, `female` → `base_body_Female.blend`. No `BASE_BODY.blend` fallback exists. |
| Outfit | `assets/outfits/outfits.json` | Region, coverage, offsets, thickness, colours, texture slots. |
| Textures | outfit `material.textures` | Reused when the file exists; otherwise generated deterministically and flagged `procedural`. |

Every input is validated (file magic, object names, axes, ranges) before a stage
runs; a bad input fails with a message naming the file, the object and the fix.

## Bring your own garment

Every stage takes a `--params` JSON object, and `shell` honours two keys that let
you skip the body-surface cut entirely:

```bash
python tools/run_headless.py --variant male --outfit jacket_urban_01 --stage shell \
  --params '{"garment_blend": "/abs/path/my_jacket.blend", "garment_object": "Jacket_Mesh"}'
```

* `garment_blend` — a `.blend` whose largest mesh becomes the garment. The object
  is renamed `CLOTH_Shell`, so the rest of the pipeline (fit, skinning, thickness,
  UVs, export) runs unchanged.
* `garment_object` — optional object name; omit it to take the largest mesh.
* `fit_supplied: false` keeps the mesh exactly where it is instead of projecting
  it onto the body surface.

A supplied mesh is rejected with the offending file and the list of available
objects when the file is missing, holds no mesh, has fewer than 8 vertices or is
still parented/linked to the body.

The supplied mesh goes through every later stage unchanged: fit, skinning,
thickness, UVs, export. It must be dense enough to absorb the fit — a 576-face
grid stretched over the body trips the p99 edge-distortion gate (0.62 > 0.50),
which is the gate doing its job. Give the stage a mesh with a density close to the
body's, or raise `p99_distortion`/`max_distortion` deliberately.

`--params` carries stage-local overrides for any stage, for example
`{"min_clearance": 0.005}` (validate), `{"weight_limit": 4}` (weights) or
`{"thickness": 0.002}` (shrinkwrap). They are intentionally *not* part of the
checkpoint signature: a parameter only changes the stage that consumes it, so an
upstream checkpoint stays valid and the affected stage simply rebuilds.

## Stage 0 — `shell`

Cuts a garment panel out of the body itself, then offsets it off the surface.

* Locates the body mesh from the manifest (`body_object`) and normalises the
  scene to +Z up / −Y forward using the declared source axes.
* Derives landmarks (height, shoulder width, arm radius, wrist, hips) from the
  actual vertex cloud, not from constants.
* Bisects the body with six planes (hem, collar, two sleeve cuts, front
  opening) => panel faces, then removes the front-opening strip and dissolves
  the sub-millimetre slivers the bisects leave behind.
* Offsets every vertex along its nearest-surface normal with a coverage ramp
  (`offset.default` + `hem_extra` / `collar_extra` / `sleeve_extra`).
* Tags hem, collar and cuff vertices in the `UN_SHRINK` helper group (a 4 mm
  band) so the fit cannot pull the edges in. The group is a helper only — it is
  never a deform influence.

**Gate:** garment face count ≥ `MIN_GARMENT_FACES` and *different* from the
body's face count (a copy of the body is not a garment).
**Checkpoint:** `outputs/<variant>/<outfit>/00_garment.blend`
**Not yet applied:** thickness. A double-walled shell cannot be shrinkwrapped —
both walls project onto the same offset surface and the edge distortion explodes
(previously 11–18×). Thickness is applied in the next stage, after the fit.

## Stage 1 — `shrinkwrap`

Projects the panel onto the body with a *push-out-only* rule, then applies
thickness.

* Nearest-surface projection to `nearest_point + normal * offset`, where the
  offset follows the same ramp as the shell stage.
* The displacement **field** is Laplacian-smoothed (6 iterations, factor 0.5)
  and re-projected, which removes the local spikes a per-vertex projection
  leaves on concave regions.
* A clearance pass pushes any vertex that is still closer than the target
  distance back out.
* `repair_and_solidify`: weld, drop loose verts, split pinch vertices, recalc
  normals, then Solidify (`offset=1.0`, `use_even_offset=False`, `use_rim`) so
  the fitted surface stays the *outer* one. Sliver faces are dissolved and the
  result is kept only if the shell stays closed.

**Gates:** watertight (`boundary_edges == 0`, `non_manifold_edges == 0`),
positive volume, `min_clearance > 0`, no penetrating vertices, max edge
distortion ≤ 3.0 and p99 ≤ 0.5 (edges ≥ 3 mm; smaller edges are cut slivers and
their ratios are meaningless). Every gate reports *why* it failed and which
parameter to change.
**Checkpoint:** `01_fitted.blend`

## Stage 2 — `weights`

Binds the garment to the armature and transfers skin weights.

* Finds the armature (or builds a 21-bone procedural rig with bone-heat
  weighting when the body ships without one — the marketplace bodies do).
* Data Transfer from the body, nearest-face interpolation, limited to 4
  influences per vertex, strongest kept, then normalised.
* Falls back to nearest-bone weights per vertex (counted and reported in
  `verts_with_fallback`) rather than leaving a vertex unweighted.
* `UN_SHRINK` and any other helper group are excluded from the transfer.

**Gates:** at most 4 influences, no unweighted vertex, every vertex normalised
to 1.0, no helper group colliding with a bone name, an Armature modifier
pointing at the bound armature.
**Checkpoint:** `02_skinned.blend`

## Stage 3 — `uv_material`

Real unwrap + PBR material.

* Removes every existing UV layer, then Smart UV Project (angle-based unwrap
  with seams as a fallback) — never the old XY planar projection.
* Validates the layout: one layer only, no NaNs, nothing outside 0–1, no
  UV-degenerate face with real (> 1 mm²) 3D area, coverage ≥ 0.05.
* Builds a Principled BSDF with base colour, normal, roughness and metallic
  maps; the four images are packed into the `.blend`.
* Re-opens each image and checks it is not blank (a black base-colour or a flat
  normal map fails the stage — this is how the "textures exist but the asset is
  black" bug was caught).

**Checkpoint:** `03_material.blend`

## Stage 4 — `export`

Native exporters only (the hand-written GLB writer is gone).

* Triangulates the garment once (the glTF exporter cannot build tangents from
  n-gons) while preserving UVs and vertex groups.
* `bpy.ops.export_scene.fbx` with the garment **and** the armature selected,
  skins, embedded textures, Unity axes.
* `bpy.ops.export_scene.gltf` (`GLB`, skins, tangents, materials, images,
  animations, Y-up).
* Repairs zero-length tangents (reported, and a large number fails the export).
* Parses both files back: GLB must contain a skin, joints, UVs, normals,
  tangents, embedded images and ≤ 4 influences with normalised weights; FBX
  must contain a Skin deformer, one cluster per bone and UV/normal layers.
* Runs the Khronos glTF Validator when node + `gltf-validator` are installed;
  any error fails the stage.

**Checkpoint:** `04_final.blend`, plus `<outfit>_Modular.fbx` and
`<outfit>_Modular.glb`.

## Checkpoints and staleness

`outputs/<variant>/<outfit>/run_index.json` records, per stage, the checkpoint
path, its size and mtime, the signature and the parameters. A checkpoint only
counts as *fresh* if the file still exists with the recorded size and mtime.
`--stage X` resumes from the newest fresh upstream checkpoint; running a stage
invalidates everything downstream, so a stale `03_material.blend` can never be
exported as if it were new. `--stage full` clears the index first.

## Validation stage

`--stage validate` runs `preflight`, `weights_check`, `topology_check`,
`uv_check` and `export_check` against the newest checkpoint without writing a
new one. It is the PR gate.
