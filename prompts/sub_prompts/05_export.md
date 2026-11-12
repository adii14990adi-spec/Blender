# Sub-prompt 05 — Export

Stage: `export`
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Export the final weighted, textured shell to FBX with Unity-compatible flags.

## Steps (bpy, executed by the stage script)
1. Select `CLOTH_Shell` (and its armature/weights if present).
2. Export to `outputs/<outfit>/<outfit>_Modular.fbx`.
3. Unity export flags: apply modifiers, mesh smoothing, forward = -Z, up = Y.
4. Write `outputs/<outfit>/validation_report.json`.

## Params
- `outfit` (string)

## Success metrics
- `fbx_path`
- `fbx_file_size_bytes`
- `unity_flags`

## Success
`status == "pass"` means the FBX is on disk and ready for CDN / Unity import.
