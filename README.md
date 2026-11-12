# Modular Clothing Pipeline

Production-grade headless Blender pipeline for generating modular clothing (shell → shrinkwrap → weights → UV/material → FBX export), orchestrated by Arena Agent and gated by GitHub Actions CI.

## Repository layout

```
.github/            # CI workflows + issue templates
prompts/            # Versioned master prompt + stage sub-prompts (source of truth)
blender/            # bpy pipeline stages, validators, utils, entry point
assets/             # Base body, reference sheets, textures (Git LFS)
outputs/            # CI artifacts — gitignored
agent/              # Arena Agent config, tool schemas, system prompt
tests/              # Unit + integration tests (run inside Blender)
docker/             # Blender 5.x headless image
```

## Quick start

```bash
# Lint
make lint

# Run the PR-gate validation stage (requires headless Blender on PATH)
make validate

# Run unit tests inside Blender
make test

# Full pipeline + FBX/GLB export (stage chaining, see below)
make build OUTFIT=jacket_urban_01          # = --stage full

# Lint + non-Blender tests (import wiring, CLI contract)
ruff check blender tests agent
pytest -q
```

`run_pipeline.py` exits **0 only when the requested stage passes**. On failure it
writes the failure JSON to `--out` and exits **1**, so CI goes red at the step
that actually broke. Blender also needs `--python-exit-code 1` so an uncaught
Python error fails the step instead of exiting 0.

A placeholder GLB is only written when `--allow-fallback-glb` is passed, and it
is always flagged with `glb_is_fallback: true`; the build workflow asserts that
flag is not set.

## How the stages chain

Each stage opens the newest checkpoint written before it
(`outputs/<outfit>/00_shell.blend` → `01_shrinkwrapped.blend` →
`02_weighted.blend` → `03_uv_material.blend`) and saves its own checkpoint, so a
failed run is resumable and `--stage shrinkwrap` re-uses stage 1's output. Metrics
record `input.input_source` (`checkpoint` or `base_blend`) and
`input_blend` (the exact file the stage was handed) so the run log shows which
artifact was consumed. `--stage full` (alias `--stage all`) runs the whole chain
and stops at the first failure.

## CI

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/blender-validate.yml` | `pull_request` | PR gate — runs validators only, blocks bad exports (skipped with a warning while the base body is still a placeholder) |
| `.github/workflows/blender-build.yml` | `push` to `main` | Full pipeline + FBX/GLB export, uploads `outputs/` artifact and deploys the web viewer to Pages |

### GitHub Pages (one-time setup)

Enable Pages in **Settings → Pages → Build and deployment → Source: GitHub Actions**. Once enabled, every successful `main` build publishes the browser review page at `https://adii14990adi-spec.github.io/Blender/`.

Without Pages enabled, the deploy step will fail at `deploy-pages` (no publish target).

## Known placeholders

Two files are intentionally stubs and must be replaced before the build can go
green:

| Path | State | Needed |
|---|---|---|
| `assets/base_body/BASE_BODY.blend` | 385-byte text stub | A real Blender file with a `BASE_BODY` mesh and `UPPER_BODY`/`LOWER_BODY`/`HEAD` vertex groups, committed with Git LFS |
| `assets/base_body/BASE_BODY.fbx` | stub | The same body exported from Unity, for scale checks |

`run_pipeline.py` detects the stub by its file header and fails with an
actionable message instead of letting Blender fail obscurely. For an explicitly
allowed smoke test without a real asset, pass
`--params '{"allow_blockout_body": true}'`: the shell stage then builds a blockout
cube and records `base_body_source: "blockout"`.

The FBX/GLB exporters no longer write placeholder files on failure — a failed
export fails the stage. Previously a stub FBX and a cube GLB were produced with
`status: pass`, which is how the CI run looked green while shipping nothing.

## Prompt versioning

`prompts/master_prompt.md` is the authoritative system instruction for Arena Agent. Every change goes through `prompts/CHANGELOG.md` (semver) and a validation run. The agent pins the version in `agent/arena_agent_config.yaml`.

## Asset storage

`*.blend`, `*.fbx`, `*.png` are tracked with Git LFS (`.gitattributes`). `outputs/` is CI-only and never committed. Reference sheets >50 MB should live in S3 with an in-repo `reference_manifest.json`.

## Docker

Pin Blender 5.x:

```bash
docker build -t ghcr.io/<org>/blender5-headless:5.0.0 -f docker/Dockerfile.blender .
```

Never use `:latest` in production workflows — bpy API drift will silently break the pipeline.

## Blender 5.0 headless risk audit

| # | Risk | State here |
|---|---|---|
| R1 | `except Exception: pass` around `bpy.ops.wm.open_mainfile` hides a missing/corrupt file | Fixed — every stage fails with `could not open <path>: <error>` |
| R2 | A failed `bpy.ops.export_scene.fbx` wrote a fake 12-byte FBX and still returned `pass` | Fixed — a failed FBX/GLB export fails the stage; no stub files are written |
| R3 | Git LFS pointer instead of a real `.blend` | Detected in `run_pipeline.py` (magic header check) and printed by the "Inspect base body asset" CI step |
| R4 | Operators needing a UI context / active object | Export stage links the shell into the active view layer, falls back to direct deselection, and `mode_set` is never required; only `blender -b` is used, no window ops |
| R5 | Object names (`CLOTH_Shell`, `BASE_BODY`) assumed after load | Export stage logs `[o.name for o in bpy.data.objects]` and returns the list in failure metrics; shell stage fails if `BASE_BODY` is absent (blockout is opt-in) |
| R6 | Relative `outputs/` vs `REPO_ROOT/outputs` | Fixed — every path goes through `bpy_scripts/utils/paths.py`; `run_pipeline.py` checks the GLB under `REPO_ROOT/outputs/<outfit>/` |
| R7 | A fallback GLB reaching GitHub Pages | Fixed — fallback requires `--allow-fallback-glb` and is flagged; the build asserts `glb_is_fallback` is false and size > 1 KB before Pages setup |
| R8 | Node 20 deprecation on `actions/checkout@v4` | Cosmetic, no action needed |

Everything bpy-specific above is guarded and lint-checked, but it is **not
executed in this repo's test suite** — the pipeline code paths only run inside
real Blender (CI). `pytest` covers the import wiring, CLI exit codes and stage
chaining with mocked stages.
