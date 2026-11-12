# Sub-prompt 04 — UV & Material

Stage: `uv` (material is an alias)
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Unwrap CLOTH_Shell UVs and assign a material with the basecolor texture.

## Steps (bpy, executed by the stage script)
1. Smart UV Project `CLOTH_Shell`.
2. Create/material slot: `MAT_Cloth_<outfit>`.
3. Load `assets/textures/<outfit>_basecolor.png` as the basecolor texture (if present).
4. Assign material to CLOTH_Shell.
5. Save checkpoint `outputs/<outfit>/03_uv_material.blend`.

## Params
- `outfit` (string)
- `texture_path` (string, optional)

## Success metrics
- `uv_islands`
- `material_name`
- `texture_loaded`
- `checkpoint`

## Validation
Run `uv_check` (UV overlap / island bounds) before proceeding.
