# Prompt Changelog

The master prompt is a code artifact. Every change goes through this changelog (semver) and a validation run before it can be pinned by Arena Agent.

## v2.0.0 — 2026-11-12

Pipeline rewrite (breaking, prompt-visible):

- **Body variants replace BASE_BODY.** `assets/base_body/manifest.json` declares
  `male` (`base_body_Male.blend`) and `female` (`base_body_Female.blend`); the
  body object name comes from the manifest. No blockout fallback in production.
- **Shell no longer duplicates the body.** It cuts a garment panel with hem /
  collar / sleeve / front-opening planes and offsets it along the surface
  normals. Thickness moved to the shrinkwrap stage.
- **Shrinkwrap is a real surface fit**: push-out-only nearest-surface projection
  with a smoothed displacement field, a clearance pass and Solidify afterwards
  (a double-walled shell cannot be fitted). Gates: watertight, positive volume,
  `min_clearance > 0`, 0 penetrations, edge distortion max ≤ 3.0 / p99 ≤ 0.5.
- **Weights**: Data Transfer from the body (or a procedural 21-bone rig when the
  body has none), ≤ 4 influences, strongest kept, normalised, helper groups
  excluded, every vertex validated.
- **UV + material are one stage.** Smart UV Project (no XY projection), one UV
  layer, non-blank packed PBR maps. Running `uv` and `material` separately is no
  longer possible.
- **Export uses the native FBX/glTF exporters** and validates the results (skin,
  joints, UVs, tangents, embedded textures, ≤ 4 influences) plus the Khronos
  glTF Validator. The custom GLB writer and the fallback GLB are removed.
- **Checkpoints are variant-scoped** (`outputs/<variant>/<outfit>/`) and tracked
  in `run_index.json` with size/mtime, so stale files cannot be exported.

## v1.3.0 — 2026-11-12

- Added UN_SHRINK vertex group logic for loose hems
- Shrinkwrap offset increased 0.003 → 0.004 (fixed kneepad poke-through)
- Weight limit reduced 4 → 4 (unchanged; tested, mobile ceiling)

## v1.2.1 — 2026-11-08

- Data Transfer mapping: NEAREST_VERTEX → POLYINTERP_NEAREST (fixed elbow seams on jackets)

## v1.2.0 — 2026-11-05

- Initial Unity export flags locked
