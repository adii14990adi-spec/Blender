# Sub-prompt 03 — Weights

Stage: `weights` (aliases: `skin`, `skin_weights`)
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Bind the garment to the armature and give every vertex valid, normalised skin
weights with at most 4 influences.

## Steps (bpy, executed by the stage script)
1. Load the upstream checkpoint (`01_fitted.blend`).
2. Find the variant's armature (`armature_object` in
   `assets/base_body/manifest.json`). The shipped marketplace bodies have none,
   so the stage builds a 21-bone procedural rig (Hips → Spine → Chest → Neck →
   Head, arms and legs) and bone-heat weights it. An authored rig is used as-is
   when the manifest names one.
3. Bind `CLOTH_Shell` with an Armature modifier, then transfer weights from the
   body: nearest-surface / Data Transfer interpolation, limit to
   `weight_limit` (4) influences, keep the strongest, renormalise.
4. Vertices the transfer misses get nearest-bone weights and are counted in
   `verts_with_fallback` — never left unweighted.
5. Helper groups (`UN_SHRINK`) are excluded from the transfer and never become
   deform influences.
6. Save `outputs/<variant>/<outfit>/02_skinned.blend`.

## Gates
- `max_influences ≤ 4`, `unweighted_vertices == 0`, `unnormalised_vertices == 0`
- no helper group whose name collides with a bone
- `armature` set, and an Armature modifier pointing at it

## Params
- `outfit` (string), `variant` (string), `weight_limit` (int, default 4)

## Success metrics
- `skin.armature`, `skin.deform_bones`, `skin.verts_transferred`
- `skin.influence_histogram`, `skin.verification`
