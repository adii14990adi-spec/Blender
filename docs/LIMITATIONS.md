# Known limitations

Honest list of what this pipeline does **not** do, with the reason and the
workaround. Everything here was observed while running the real pipeline on both
body variants; nothing is speculation.

## Garment shape

* **The garment is a cut of the body surface, not a drafted pattern.** The
  panel is bisected out of the body mesh and offset off it. It therefore follows
  the body's anatomy very closely: sleeves are tubes that follow the arm, the
  hem is a horizontal cut, and there are no darts, pleats, gathers, lapels or
  collars. A tailored look needs a real pattern (sewn panels with seam lines)
  or a sculpted garment — both outside what this pipeline generates.
* **Three outfits, all cut from the body.** `jacket_urban_01` (hip length) and
  `parka_urban_01` (longer, roomier, heavier sleeve offset) are single-piece
  garments; `tactical_loadout_01` is a four-piece loadout (tee, cargo trousers,
  duty belt, boots) cut in one pass. Every outfit uses the same panel logic and
  differs only in `pieces`/`coverage`, `offsets`, `thickness` and `materials` in
  `assets/outfits/outfits.json`. Any further outfit needs its own tuning **and**
  its own clearance/distortion validation — the offsets do not generalise for
  free.
* **Hard details are beveled boxes, not modelled props.** Pouches, knee pads, a
  belt buckle and a holster are rounded cuboids auto-placed on the body surface
  (anchor + normal + standoff, optional `x_range` band). They read correctly at
  asset distance and skin with the garment, but they have no straps, buckles,
  stitching, zippers or openings, and they do not deform like leather or webbing.
  Anything more specific belongs in an authored prop mesh.
* **The lower body follows the legs exactly.** Trousers are a leg-surface cut
  with a standoff, so there are no pleats, cargo folds, boot tucks or hem breaks;
  the boots are a foot cut, which keeps the foot silhouette (a bare foot shape
  painted black rather than a last-shaped boot). A real boot needs an imported
  mesh, which the `garment_blend` path already supports.
* **Details cannot be nested.** A pouch cannot sit on another pouch, and a detail
  cannot be assigned to a piece other than by its anchor height — the anchor
  (`waist`, `knee`, `chest`, `ankle`, …) selects the body band it attaches to.
* **The GLBs contain the garment only.** No body mesh is exported, so the web
  studio shows the garment as a free-standing asset (the thumbnails render it on
  the mannequin because they are rendered from the `.blend`). That is deliberate:
  a body in every GLB would roughly double the download and is not what a
  garment asset is.
* **Shoulders read as slightly puffy** in the male build: the sleeve cut is a
  plane, so the shoulder corner keeps body-like volume. Adjacent edges are
  smooth (max distortion ≈ 1–2×), but a tailor would cut the shoulder seam.
* **The front opening is a straight vertical strip** of `opening_width`
  (0.10 m). It does not taper, and it is always cut — no buttoned or zipped
  variants.

## Fitting

* **Push-out only.** The fit never pulls fabric tight against the body; it
  enforces a minimum standoff (12 mm base, up to 18 mm at the hem). When the body
  is concave (armpit, crotch, under the bust) the nearest-surface normal can
  point sideways, so clearance there is smaller than the nominal offset. The
  gate only requires `min_clearance > 0` (measured: 6.3–6.8 mm).
* **Distortion is bounded, not eliminated.** Limits are max ≤ 3.0 and p99 ≤ 0.5
  on edges ≥ 3 mm. Measured today: 1.08 / 0.29 (male), 1.99 / 0.35 (female). A
  few uncut slivers under 3 mm still stretch several-fold; they are invisible at
  render scale but they are why the metric ignores them instead of pretending
  they do not exist.
* **No cloth simulation.** No gravity, no wrinkles, no self-collision. The
  shell is a static offset surface.

## Rig and skinning

* **The bodies ship without a rig.** `base_body_Male/Female.blend` contain no
  armature and no vertex groups, so the weights stage *builds* a 21-bone
  procedural rig (Hips → Spine → Chest → Neck → Head, arms, legs) with bone-heat
  weighting. Weights are transferred from that rig, not from an authored rig.
  A production skeleton should replace it — `manifest.json` already accepts an
  `armature_object` per variant, and the weights stage uses it when present.
* **No finger or face bones.** Hands are a single bone per side.
* **Animation is exported but not authored.** The glTF/FBX exporters are
  configured for animations (`export_animations`, NLA strips); there are no
  actions in the source files, so the exported files currently contain none.
* **Weight transfer quality depends on the body.** Data Transfer with
  nearest-face interpolation is good on the torso and arms; at the armpit the
  garment can pick up weights from the opposite side of the gap. The 4-influence
  limit and normalisation keep it valid, not necessarily ideal.

## UVs and textures

* **Smart UV Project quality.** Coverage is ~0.96 with 2 % island margin — fine
  for a 512² set, not for 4K UHD texturing. Seam placement is automatic, so
  islands are not aligned to garment seams.
* **Procedural fabric only.** The maps are a deterministic basket weave
  (512²). Real cloth scans would drop straight into
  `assets/textures/<outfit>/` and be used as-is (provenance becomes
  `existing`), but none are shipped.
* **One material slot.** Multi-material garments (lining, trim, hardware) are
  not supported yet.
* **Metallic map is constant 0** — correct for cloth, but the file is therefore
  featureless by design.

## Export

* **No lightmap UVs** (UV1). The Khronos validator passes without them; Unity's
  lightmapper would need a second channel.
* **Tangents are repaired, not perfect.** Blender emits zero-length tangents for
  triangles whose UVs collapse (sub-millimetre slivers). Those are rewritten as
  `(1, 0, 0, 1)` and counted in `tangents.repaired`; more than 1 % would fail the
  export.
* **The garment is triangulated at export** (~6.5–7.3k triangles). Quad topology
  is only available in the pre-export checkpoints.
* **Scale is metres, Y-up for glTF, Unity axes for FBX.** Nothing else is
  offered; engines expecting centimetres need an import scale.

## Tooling and CI

* **The Khronos validator is optional at runtime.** It needs node and the
  `gltf-validator` npm package; CI installs both and treats errors as failures.
  Without it the export still runs the project's own GLB parser, and the report
  records `khronos_validator.status = "skipped"` rather than pretending it ran.
* **Integration tests need Blender 5.0** (`pip install bpy==5.0.0` on Python
  3.11). They are skipped — not passed — when `bpy` is missing.
* **Rendering previews is not part of CI.** The renders used during development
  came from Cycles CPU renders of the checkpoints; the pipeline itself never
  renders, so visual regressions are only caught by eye (or by the numeric gates
  above).
