# Sub-prompt 01 — Generate Shell

Stage: `shell` (aliases: `garment`, `generate_shell`, `subdivide`)
Region: UPPER_BODY | LOWER_BODY | HEAD

## Goal
Cut the garment panel out of the body and push it off the surface. This is the
base mesh every later stage (shrinkwrap, weights, UV, material) operates on.

The body is **never** assumed to be `BASE_BODY`: it is resolved from
`assets/base_body/manifest.json` for the requested `--variant`
(`male` → `base_body_Male.blend`, `female` → `base_body_Female.blend`) and the
manifest's `body_object` name is used to find the mesh. A blockout body is never
generated silently.

## Steps (bpy, executed by the stage script — not by the agent)
1. Open the variant's blend (or the `--blend` override) and normalise the scene
   to +Z up / −Y forward using the manifest axes.
2. Infer landmarks from the vertex cloud (height, shoulder width, arm radius,
   wrist, hips) and derive garment limits from the outfit's `coverage`.
3. Bisect the body with the hem, collar, sleeve and front-opening planes, delete
   the opening strip, dissolve the sliver faces the bisects leave behind.
4. Offset every vertex along its nearest-surface normal using the coverage ramp
   (`offset.default` + `hem_extra` / `collar_extra` / `sleeve_extra`).
5. Tag hem/collar/cuff vertices in the `UN_SHRINK` helper group (4 mm band) so
   the fit cannot pull the edges in.
6. Save the checkpoint `outputs/<variant>/<outfit>/00_garment.blend`.

Thickness is **not** applied here on purpose: a double-walled shell cannot be
fitted (both walls project onto the same offset surface). The shrinkwrap stage
adds it after the fit.

## Params
- `outfit` (string), `variant` (string: male | female).

## Success metrics
- `garment_stats.face_count` (must differ from the body's), `patch.opening_faces_removed`
- `measurements.{min,mean}_clearance`, `pinned_vertices`
- `checkpoint`

## Failure modes
- Body object from the manifest missing → `fail` with the object list.
- Garment identical to the body (face count equal) → `fail`.
- Checkpoint could not be written → `fail`.
