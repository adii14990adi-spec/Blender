# Sub-prompt 02 — Shrinkwrap

Stage: `shrinkwrap`
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Shrinkwrap `CLOTH_Shell` onto `BASE_BODY` so the cloth rests on the body with a small offset (prevents z-fighting / poke-through).

## Steps (bpy, executed by the stage script)
1. Load the previous checkpoint (`00_shell.blend`) or `--blend`.
2. Add a Shrinkwrap modifier to `CLOTH_Shell` targeting `BASE_BODY`.
3. Set `offset` from `params.offset` (default 0.004, CHANGELOG v1.3.0).
4. Apply the modifier.
5. Save checkpoint `outputs/<outfit>/01_shrinkwrapped.blend`.

## Params
- `outfit` (string)
- `offset` (float, default 0.004)

## Success metrics
- `shrinkwrap_offset`
- `shell_vertex_count` (post-apply)
- `checkpoint`

## Failure recovery
If validation reports poke-through / intersection, retry with a larger offset (e.g. 0.006) rather than rebuilding from shell.
