# Build report

_Generated 2026-11-12 16:33 UTC by `tools/build_report.py`._

**Overall: PASS**

## female · jacket_urban_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12812 |
| Panel faces (cut from body) | 3482 |
| Front-opening faces removed | 986 |
| Final vertices / faces | 4150 / 7302 |
| Thickness | 0.007 m |
| Volume | 0.006573 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0068 / 0.0132 / 0.0186 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 1.99 / 0.352 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 4150 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.957 |
| Degenerate UV faces | 0 |
| Material slots | 1 |
| Texture set valid | yes |
| base_color map | 512x512, packed=yes, mean=0.136 |
| normal map | 512x512, packed=yes, mean=0.501 |
| roughness map | 512x512, packed=yes, mean=0.779 |
| metallic map | 512x512, packed=yes, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 7257 / 8301 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 3 |
| Khronos errors / warnings | — / — |
| Size | 1034172 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 840604 bytes |

Artifacts:
- `fbx`: `outputs/female/jacket_urban_01/jacket_urban_01_Modular.fbx`
- `glb`: `outputs/female/jacket_urban_01/jacket_urban_01_Modular.glb`
- `final_blend`: `outputs/female/jacket_urban_01/04_final.blend`

## female · parka_urban_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12812 |
| Panel faces (cut from body) | 4040 |
| Front-opening faces removed | 688 |
| Final vertices / faces | 4812 / 8514 |
| Thickness | 0.007 m |
| Volume | 0.007775 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0068 / 0.0141 / 0.0201 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 2.77 / 0.347 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 4812 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.956 |
| Degenerate UV faces | 0 |
| Material slots | 1 |
| Texture set valid | yes |
| base_color map | 512x512, packed=yes, mean=0.112 |
| normal map | 512x512, packed=yes, mean=0.501 |
| roughness map | 512x512, packed=yes, mean=0.859 |
| metallic map | 512x512, packed=yes, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 8549 / 9628 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 3 |
| Khronos errors / warnings | — / — |
| Size | 1036676 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 786812 bytes |

Artifacts:
- `fbx`: `outputs/female/parka_urban_01/parka_urban_01_Modular.fbx`
- `glb`: `outputs/female/parka_urban_01/parka_urban_01_Modular.glb`
- `final_blend`: `outputs/female/parka_urban_01/04_final.blend`

## female · tactical_loadout_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12812 |
| Panel faces (cut from body) | 6376 |
| Front-opening faces removed | 0 |
| Final vertices / faces | 2870 / 5336 |
| Thickness | 0.004 m |
| Volume | 0.010772 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

Multi-piece outfit — 4 pieces, 11 hard details, 6 materials

| Piece | Region | Fit mode | Faces | Min clearance | Penetrating | Watertight |
| --- | --- | --- | --- | --- | --- | --- |
| `tee` | UPPER_BODY | surface | 5336 | 0.0089 m | 0 | yes |
| `cargo` | LOWER_BODY | shell | 3870 | 0.0004 m | 0 | yes |
| `belt` | ACCESSORY | shell | 794 | 0.0015 m | 0 | yes |
| `boots` | FOOTWEAR | shell | 3402 | 0.0026 m | 0 | yes |

Materials: `MAT_Cloth_tactical_tee`, `MAT_Cloth_tactical_cargo`, `MAT_Cloth_tactical_webbing`, `MAT_Cloth_tactical_boot`, `MAT_Cloth_tactical_gear`, `MAT_Cloth_tactical_patch`

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0004 / 0.0100 / 0.0152 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 0.96 / 0.174 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 8012 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.953 |
| Degenerate UV faces | 0 |
| Material slots | 6 |
| Texture set valid | yes |
| base_color map | 512x512, packed=**no**, mean=0.096 |
| normal map | 512x512, packed=**no**, mean=0.500 |
| roughness map | 512x512, packed=**no**, mean=0.919 |
| metallic map | 512x512, packed=**no**, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 16708 / 16008 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 18 |
| Khronos errors / warnings | — / — |
| Size | 3097276 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 2522124 bytes |

Artifacts:
- `fbx`: `outputs/female/tactical_loadout_01/tactical_loadout_01_Modular.fbx`
- `glb`: `outputs/female/tactical_loadout_01/tactical_loadout_01_Modular.glb`
- `final_blend`: `outputs/female/tactical_loadout_01/04_final.blend`

## male · jacket_urban_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12566 |
| Panel faces (cut from body) | 3318 |
| Front-opening faces removed | 682 |
| Final vertices / faces | 3914 / 6930 |
| Thickness | 0.007 m |
| Volume | 0.008095 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0063 / 0.0134 / 0.0180 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 1.08 / 0.291 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 3914 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.959 |
| Degenerate UV faces | 0 |
| Material slots | 1 |
| Texture set valid | yes |
| base_color map | 512x512, packed=yes, mean=0.136 |
| normal map | 512x512, packed=yes, mean=0.501 |
| roughness map | 512x512, packed=yes, mean=0.779 |
| metallic map | 512x512, packed=yes, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 6545 / 7830 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 3 |
| Khronos errors / warnings | — / — |
| Size | 974564 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 821372 bytes |

Artifacts:
- `fbx`: `outputs/male/jacket_urban_01/jacket_urban_01_Modular.fbx`
- `glb`: `outputs/male/jacket_urban_01/jacket_urban_01_Modular.glb`
- `final_blend`: `outputs/male/jacket_urban_01/04_final.blend`

## male · parka_urban_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12566 |
| Panel faces (cut from body) | 3694 |
| Front-opening faces removed | 500 |
| Final vertices / faces | 4364 / 7758 |
| Thickness | 0.007 m |
| Volume | 0.009236 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0064 / 0.0143 / 0.0190 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 1.15 / 0.308 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 4364 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.959 |
| Degenerate UV faces | 0 |
| Material slots | 1 |
| Texture set valid | yes |
| base_color map | 512x512, packed=yes, mean=0.112 |
| normal map | 512x512, packed=yes, mean=0.501 |
| roughness map | 512x512, packed=yes, mean=0.859 |
| metallic map | 512x512, packed=yes, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 7391 / 8732 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 3 |
| Khronos errors / warnings | — / — |
| Size | 938840 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 749932 bytes |

Artifacts:
- `fbx`: `outputs/male/parka_urban_01/parka_urban_01_Modular.fbx`
- `glb`: `outputs/male/parka_urban_01/parka_urban_01_Modular.glb`
- `final_blend`: `outputs/male/parka_urban_01/04_final.blend`

## male · tactical_loadout_01 — PASS

| Stage | Result |
| --- | --- |
| Garment shell | PASS |
| Surface fit + thickness | PASS |
| Skin weights | PASS |
| UV unwrap + PBR material | PASS |
| FBX / GLB export | PASS |

| Garment | Value |
| --- | --- |
| Body faces (source) | 12566 |
| Panel faces (cut from body) | 6086 |
| Front-opening faces removed | 0 |
| Final vertices / faces | 2484 / 4578 |
| Thickness | 0.004 m |
| Volume | 0.011298 m³ |
| Watertight (0 boundary/non-manifold) | yes |
| Degenerate faces | 0 |

Multi-piece outfit — 4 pieces, 11 hard details, 6 materials

| Piece | Region | Fit mode | Faces | Min clearance | Penetrating | Watertight |
| --- | --- | --- | --- | --- | --- | --- |
| `tee` | UPPER_BODY | surface | 4578 | 0.0090 m | 0 | yes |
| `cargo` | LOWER_BODY | shell | 4172 | 0.0002 m | 0 | yes |
| `belt` | ACCESSORY | shell | 726 | 0.0022 m | 0 | yes |
| `boots` | FOOTWEAR | shell | 3472 | 0.0004 m | 0 | yes |

Materials: `MAT_Cloth_tactical_tee`, `MAT_Cloth_tactical_cargo`, `MAT_Cloth_tactical_webbing`, `MAT_Cloth_tactical_boot`, `MAT_Cloth_tactical_gear`, `MAT_Cloth_tactical_patch`

| Fit | Value |
| --- | --- |
| Min / mean / max clearance | 0.0002 / 0.0100 / 0.0138 m |
| Penetrating vertices | 0 |
| Edge distortion max / p99 | 0.64 / 0.197 |

| Skinning | Value |
| --- | --- |
| Armature | RIG_Character |
| Deform bones | 21 |
| Transferred vertices | 7896 |
| Max influences per vertex | 4 |
| Unweighted / unnormalised | 0 / 0 |
| Transfer method | polyinterp_nearest_surface |

| UV / Material | Value |
| --- | --- |
| UV layers | 1 |
| Unwrap method | smart_project |
| UV coverage | 0.941 |
| Degenerate UV faces | 0 |
| Material slots | 6 |
| Texture set valid | yes |
| base_color map | 512x512, packed=**no**, mean=0.096 |
| normal map | 512x512, packed=**no**, mean=0.500 |
| roughness map | 512x512, packed=**no**, mean=0.919 |
| metallic map | 512x512, packed=**no**, mean=0.000 |

| GLB | Value |
| --- | --- |
| Valid | yes |
| Vertices / triangles | 16510 / 15792 |
| Skin / joints | 1 / [21] |
| UVs / tangents / images | yes / yes / 18 |
| Khronos errors / warnings | — / — |
| Size | 3080320 bytes |

| FBX | Value |
| --- | --- |
| Valid | yes |
| Skin clusters / bone models | 21 / 21 |
| UV layers | 1 |
| Size | 2521980 bytes |

Artifacts:
- `fbx`: `outputs/male/tactical_loadout_01/tactical_loadout_01_Modular.fbx`
- `glb`: `outputs/male/tactical_loadout_01/tactical_loadout_01_Modular.glb`
- `final_blend`: `outputs/male/tactical_loadout_01/04_final.blend`
