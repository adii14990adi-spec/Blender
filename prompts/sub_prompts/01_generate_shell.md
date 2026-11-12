# Sub-prompt 01 — Generate Shell

Stage: `shell` (alias: `subdivide`)
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Create the cloth shell mesh from the selected region of the base body (BASE_BODY). The shell is the base mesh that all later stages (shrinkwrap, weights, UV, material) operate on.

## Steps (bpy, executed by the stage script — not by the agent)
1. Open `--blend`.
2. Select the base body region corresponding to `--region` (vertex group or mask).
3. Duplicate it as a new mesh object named `CLOTH_Shell`.
4. Optionally apply a Subdivision Surface modifier based on `params.subdivide_levels` (default 1).
5. Save the checkpoint `outputs/<outfit>/00_shell.blend`.

## Params
- `outfit` (string): outfit directory name.
- `subdivide_levels` (int, default 1).

## Success metrics
- `shell_vertex_count`
- `shell_object_name`
- `checkpoint`

## Failure modes
- Base body or region vertex group missing → `fail`, metric `error`.
- Checkpoint could not be written → `fail`.
