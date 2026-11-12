# Modular Clothing Pipeline

Headless Blender 5.0 pipeline that turns a humanoid base body into a game-ready
garment asset:

```
shell → shrinkwrap → weights → uv_material → export
  │         │           │            │           └─ FBX + GLB (native exporters, validated)
  │         │           │            └─ smart UV unwrap + PBR maps (packed)
  │         │           └─ armature binding, ≤ 4 influences/vertex, normalised
  │         └─ nearest-surface fit + real thickness (watertight shell)
  └─ garment panel cut from the body, opened at the front
```

Both body variants — `base_body_Male.blend` and `base_body_Female.blend` — are
first-class. There is **no** `BASE_BODY.blend` assumption and no blockout-body
path in production code.

## Current status

Latest full run (both variants, real Blender 5.0, Khronos-validated):
see [`docs/BUILD_REPORT.md`](docs/BUILD_REPORT.md).

| Check | male | female |
| --- | --- | --- |
| All 5 stages | PASS | PASS |
| Garment (panel → final) | 3 318 → 6 930 faces | 3 482 → 7 302 faces |
| Watertight / degenerate faces | yes / 0 | yes / 0 |
| Min clearance / penetrations | 6.3 mm / 0 | 6.8 mm / 0 |
| Max influences per vertex | 4 | 4 |
| UV layers / coverage | 1 / 0.96 | 1 / 0.96 |
| GLB GLB skin + joints | 1 / 21 | 1 / 21 |
| Khronos errors / warnings | 0 / 0 | 0 / 0 |
| FBX skin clusters | 21 | 21 |

What the pipeline does *not* do is documented in
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) — read it before promising a
tailored coat.

## Repository layout

```
.github/workflows/     ci.yml (PR gate + matrix build) · blender-build.yml (deploy)
.github/actions/       setup-blender composite action (Blender 5.0 + checksum)
blender/               pipeline package (bpy_scripts), entry point, stages, validators
  bpy_scripts/stages/  shell · shrinkwrap · weights · uv_material · export_fbx
  bpy_scripts/utils/   geometry, rig, textures, checkpoints, paths, glb/fbx inspectors
  bpy_scripts/validators/  preflight, weights, topology, uv, export
assets/base_body/      base_body_Male.blend, base_body_Female.blend, manifest.json
assets/outfits/        outfits.json (region, coverage, offsets, thickness, materials)
assets/textures/       per-outfit PBR slots (generated set is gitignored)
tests/unit/            pure-Python tests (no Blender)
tests/integration/     real pipeline runs inside Blender 5.0
tools/                 run_headless.py, validate_exports.py, build_report.py, gltf_validate.js
web_viewer/            model-viewer review page (male/female switch)
docs/                  pipeline reference, limitations, build report
```

## Quick start

```bash
# 0) Blender 5.0 - either the binary on PATH, or the importable module
pip install bpy==5.0.0            # Python 3.11, used by the integration tests

# 1) what can I build?
python tools/run_headless.py --list-assets

# 2) full build for one variant
python tools/run_headless.py --variant male --outfit jacket_urban_01 --stage full --out /tmp/build.json

#    ...or with the real Blender binary (what CI uses)
blender -b --python-exit-code 1 --python blender/run_pipeline.py -- \
  --stage full --variant female --out /tmp/build_female.json

#    ...or preview the review page exactly as Pages serves it
python tools/serve_viewer.py --port 8099

# 3) validate the artifacts (structure + Khronos validator)
python tools/validate_exports.py --outfit jacket_urban_01 --variant male --khronos

# 4) build report
python tools/build_report.py --run male=/tmp/build.json --out docs/BUILD_REPORT.md

# 5) tests
pytest tests/unit -q          # no Blender needed
pytest tests -q               # includes the real pipeline (needs bpy)
```

Outputs land in `outputs/<variant>/<outfit>/`:

```
00_garment.blend   01_fitted.blend   02_skinned.blend   03_material.blend   04_final.blend
run_index.json     validation_report.json     export_report.json
jacket_urban_01_Modular.fbx               jacket_urban_01_Modular.glb
```

Male and female build into **separate directories** — the previous pipeline let
the second variant overwrite the first one's exports.

## How the stages chain

Each stage opens the upstream checkpoint and writes its own. The orchestrator
records every checkpoint in `run_index.json` (path, size, mtime, signature), and
a checkpoint only counts as fresh if the file still matches. Running a stage
invalidates everything downstream, and `--stage full` clears the index first, so
a stale `.blend` can never be exported as if it were new.

`--stage shrinkwrap` on a clean tree resolves the body from the manifest and
fails with "run the `shell` stage first" instead of pretending it worked.

See [`docs/PIPELINE.md`](docs/PIPELINE.md) for the exact gates of every stage.

## Exit codes and failure reporting

`blender/run_pipeline.py` exits **0 only when every requested stage passed and
the exports validated**. On failure it writes the JSON report to `--out` and
exits 1, so CI goes red at the step that broke. Blender additionally runs with
`--python-exit-code 1` so an uncaught Python error cannot exit 0.

There is no placeholder/fallback GLB any more: `--allow-fallback-glb` is
rejected explicitly. A build that cannot produce a valid asset fails.

## CI

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | PR, push to `main`, manual | `unit` (pure Python) · `assets` (both bodies are real `.blend` files) · `pipeline` (matrix: male **and** female, real Blender 5.0, exports validated + Khronos) · `bpy-tests` (`pytest tests`, real pipeline) · `gate` (fails if any job did not succeed) |
| `blender-build.yml` | push to `main`, manual | Builds both variants, validates every FBX/GLB, generates `BUILD_REPORT.md`, and deploys **only validated export artifacts** to GitHub Pages |

The PR gate fails when a base body is missing or a Git-LFS pointer — it never
skips itself green.

### GitHub Pages

Enable **Settings → Pages → Source: GitHub Actions**. The review page
(`web_viewer/index.html`) lets you switch between the male and female builds and
shows the stage status from the build's `validation_report.json`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `… is not a Blender file (starts with b'version https://git-lfs…')` | Git LFS pointer instead of the asset | `git lfs install && git lfs pull` |
| `'CLOTH_Shell' not found in base_body_Male.blend` | ran a downstream stage without its upstream checkpoint | run `--stage full`, or `--stage shell` first |
| `min_clearance -0.000x` / penetrations > 0 after the fit | offset too small for the anatomy | raise `offset.default` in `assets/outfits/outfits.json` |
| `edge distortion 4.03 exceeds 3.00` | fit dragged fabric over a concave region | raise `relax_iterations`, or reduce `hem_extra`/`sleeve_extra` |
| `base colour texture is blank` | generated PNG never got its pixels | regenerate `assets/textures/<outfit>/` (delete and rebuild) |
| `Khronos: ACCESSOR_VECTOR3_NON_UNIT … TANGENT` | zero-length tangent on a UV-degenerate sliver | handled automatically; > 1 % means the UV layout is broken |

## Asset storage

`*.blend`, `*.fbx` and `*.png` are tracked with Git LFS (`.gitattributes`).
`outputs/`, `site/` and the generated texture sets are build products and stay
out of version control.
