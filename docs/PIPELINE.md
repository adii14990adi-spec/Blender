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

## Multi-piece outfits

An outfit is either one garment (`jacket_urban_01`, `parka_urban_01`) or a
**loadout** built from several pieces in a single pass
(`tactical_loadout_01`: tee + cargo trousers + duty belt + boots, plus eleven
hard details). The schema lives in `assets/outfits/outfits.json` and is
versioned (`schema_version: 3`); an outfit without a `pieces` key keeps working
exactly as before — it resolves to one piece called `main` with the old
material names and texture paths, which is what keeps the historical builds
byte-comparable.

```json
{
  "display_name": "Tactical Loadout 01",
  "region": "MULTI",
  "garment_type": "loadout",
  "defaults": { "offset": { "default": 0.011, "hem_extra": 0.006 }, "thickness": 0.005 },
  "pieces": {
    "tee":   { "region": "UPPER_BODY", "coverage": { "top": 0.183, "bottom": 0.462 },
               "sleeves": { "length": 0.28, "cuff_inset": 0.92 },
               "opening": "closed", "thickness": 0.0035,
               "material": { "name": "MAT_Cloth_tactical_tee", "…": "…" } },
    "cargo": { "region": "LOWER_BODY", "coverage": { "top": 0.462, "bottom": 0.925 },
               "fit_mode": "shell", "offset": { "default": 0.012 } },
    "boots": { "region": "FOOTWEAR", "coverage": { "top": 0.885, "bottom": 0.997 },
               "fit_mode": "shell" }
  },
  "materials": { "gear": { "name": "MAT_Cloth_tactical_gear", "…": "…" } },
  "details": [
    { "id": "pouch_left", "anchor": "waist", "side": "left", "face": "outer",
      "size": [0.075, 0.052, 0.115], "gap": 0.018, "z_offset": -0.018,
      "rounding": 0.22, "material": "gear" }
  ]
}
```

Piece fields (all optional except `region`):

| Field | Meaning |
| --- | --- |
| `region` | `UPPER_BODY`, `LOWER_BODY`, `ACCESSORY` or `FOOTWEAR` — decides which body bands and cuts apply. |
| `coverage` | `{top, bottom}` as a fraction of body height from the head down (0 = head, 1 = feet). |
| `sleeves` | `{length, cuff_inset}` for the sleeve bisect (omit for a piece with no sleeves, e.g. trousers or boots). |
| `opening` | `front` cuts a vertical strip out of the chest (the jackets); `closed` (default) leaves the panel whole, which is what the tee uses. |
| `offset` | Standoff ramp (`default` + `hem_extra` / `collar_extra` / `sleeve_extra`). |
| `thickness` | Solidify thickness applied after the fit. |
| `fit_mode` | `surface` (re-project onto the body) or `shell` (measure only — used where a leg/arm tube cannot be re-projected without collapsing). |
| `fit_params` | `iterations` / `relax_iterations` overrides for `surface` pieces. |
| `material` | One material per piece: `name`, `base_color`, `roughness`, `metallic`, `normal_strength`, `textures{base_color,normal,roughness,metallic}`, optional `procedural` settings. Texture paths default to `<texture_dir>/<piece>_<slot>.png`. |

Detail fields: `id`, `anchor` (`waist`, `hip`, `knee`, `chest`, `ankle`, …),
`side` (`left`/`right`/`center`/`both`), `face` (`front`/`back`/`outer`),
`size` (w, d, h in metres), `gap` (standoff off the surface),
`z_offset`, `x_range` (fraction of the half-span, mirrored on the right side),
`rounding` (bevel), `tilt_deg` and `material` (a piece key or a standalone
top-level `materials` entry). Placement uses the **real surface normal** from a
nearest-point query with the vertical component removed, so an off-centre anchor
(a pouch on one hip) never tilts sideways.

What the stages do with a loadout:

* **shell** cuts every piece in one pass (`CLOTH_Shell`, then
  `CLOTH_Shell__<piece>`), builds each detail as a beveled box
  (`CLOTH_Detail__<id>`), and records the plan in the object's custom properties.
* **shrinkwrap** fits each piece with its own parameters, `shell`-mode pieces
  are measured only (and report `skipped_projection`), then everything is joined
  into one `CLOTH_Shell` with one material slot per piece/detail material — the
  rest of the pipeline (weights, UVs, export) is unchanged.
* **uv_material** keeps the slot order from the join and builds one textured
  material per slot, generating a per-piece texture set only when the files are
  missing. Near-black webbing fails the texture-variance gate, so those palettes
  carry a deliberately raised base colour (brighten it in
  `assets/outfits/outfits.json` and delete `assets/textures/<outfit>/` to
  regenerate).
* **export** writes the same single skinned GLB/FBX: six materials, 18 packed
  maps, 15 792 triangles (male) / 16 008 (female) and a 2.9 MB GLB.

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

For a loadout the same bisects run once per piece against that piece's region
and coverage; details are appended afterwards as separate objects. Nothing is
merged here — merging is the shrinkwrap stage's job, so each piece can be fitted
with its own parameters first.

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

With several pieces the stage dispatches per piece: `surface` pieces are
projected as above, `shell` pieces are only measured (min/mean/max clearance
against the body) and keep the offset the shell stage gave them, because
projecting a closed leg tube onto the legs collapses it. Every piece is then
joined into `CLOTH_Shell` with its material slots preserved.

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
new one. It is the PR gate. `uv_check` walks **every** material slot (a
loadout has six), so a piece whose texture graph is broken fails even when the
first slot is fine.
