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

# Full build + FBX export
make build OUTFIT=jacket_urban_01
```

## CI

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/blender-validate.yml` | `pull_request` | PR gate — runs validators only, blocks bad exports |
| `.github/workflows/blender-build.yml` | `push` to `main` | Full pipeline + FBX export, uploads `outputs/` artifact |
| `.github/workflows/nightly-regression.yml` | nightly | Rebuild every outfit vs. the base rig, diff against `main` |

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
