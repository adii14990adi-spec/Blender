# Sub-prompt 05 — Export

Stage: `export` (aliases: `export_fbx`, `fbx`, `glb`)

## Goal
Produce game-ready FBX and GLB files with mesh, UVs, normals, tangents, PBR
textures, skin weights, armature and animations — using Blender's native
exporters only. The hand-written GLB writer is gone; there is no placeholder
export path.

## Steps
1. Load the upstream checkpoint (`03_material.blend`), find `CLOTH_Shell` and
   the armature; refuse to export without an Armature modifier when
   `require_skin` is set.
2. Triangulate the garment (the glTF exporter cannot build tangents from
   n-gons), preserving UVs and vertex groups.
3. `export_scene.fbx`: garment **and** armature selected, skins, embedded
   textures, Unity axes.
4. `export_scene.gltf` (`GLB`): skins, tangents, materials, embedded images,
   animations, Y-up.
5. Repair zero-length tangents (reported in `tangents`; > 1 % fails).
6. Parse both files back with the project inspectors; run the Khronos glTF
   Validator when available.
7. Save `04_final.blend` **after** a successful export.

## Gates
- GLB: skin + joints, UVs, normals, tangents, embedded images, ≤ 4 influences,
  normalised weights, triangles only
- FBX: Skin deformer, one cluster per bone, UV/normal layers
- Khronos: 0 errors (warnings are also required to be 0 in CI)

## Params
- `outfit`, `variant`, `require_skin` (default true),
  `export_animations` (default true)
