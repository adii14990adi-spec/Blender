# Sub-prompt 04 — UV + PBR material

Stage: `uv_material` — the only stage that touches UVs or materials.
`uv` / `material` / `materials` are aliases of it; they must never run twice
(that used to duplicate UV layers and re-apply the material).

## Goal
A real unwrap and a complete PBR material whose maps are packed into the
`.blend` and embedded in the exports.

## Steps
1. Load the upstream checkpoint (`02_skinned.blend`).
2. Remove every existing UV layer, then Smart UV Project (angle-based unwrap
   with seams as fallback). Never an XY planar projection.
3. Validate: one layer only, no NaN, nothing outside 0–1, no UV-degenerate face
   with real (> 1 mm²) area, coverage ≥ 0.05.
4. Build a Principled BSDF with base colour, normal, roughness and metallic
   maps; pack all four images.
5. Re-open each image and verify it is not blank.
6. Save `outputs/<variant>/<outfit>/03_material.blend`.

## Gates
- UV: `uv_layer_count == 1`, `degenerate_faces == 0`, `loops_outside_0_1 == 0`
- textures: every slot present, ≥ 64², packed, non-blank
  (base colour mean > 0.01 and std > 0.002, normal map ≈ (0.5, 0.5, 1.0))

## Params
- `outfit`, `variant`, `angle_limit` (default 66°), `island_margin` (default 0.02)
