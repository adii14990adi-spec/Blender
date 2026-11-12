# Sub-prompt 03 — Weights

Stage: `weights`
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Transfer vertex weights from BASE_BODY to CLOTH_Shell and build the UN_SHRINK vertex group for loose hems.

## Steps (bpy, executed by the stage script)
1. Add a Data Transfer modifier mapping weights from BASE_BODY → CLOTH_Shell (POLYINTERP_NEAREST, CHANGELOG v1.2.1).
2. Create the `UN_SHRINK` vertex group (loose hems are excluded from shrinkwrap influence).
3. Cap weights at 4 per vertex (mobile ceiling; CHANGELOG v1.3.0).
4. Save checkpoint `outputs/<outfit>/02_weighted.blend`.

## Params
- `outfit` (string)
- `weight_limit` (int, default 4)

## Success metrics
- `vertex_groups`
- `max_weights_per_vertex`
- `checkpoint`

## Validation
Run `weights_check` (max 4 weights/vertex, group coverage) before proceeding.
