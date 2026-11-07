# game-assets — prompt → Blender → `.glb` pipeline (phone-only ops)

Upload a reference character image + a short prompt → a Groq vision model emits a **JSON ops DSL**
(never raw `bpy` code) → GitHub Actions runs **headless Blender** → a new `out_<job_id>.glb` lands on an
orphan `assets` branch → the web UI previews it in `<model-viewer>`.

```
[browser]  image + prompt
    │  POST /generate
    ▼
[Cloudflare Worker]  ── Groq vision ─► ops JSON ── sanitise/clamp ──► commit jobs/<id>.json
    │                                                                        │ repository_dispatch
    │  GET /status/<id>                                                      ▼
    │                                                        [GitHub Actions · Blender 4.2 LTS]
    │                                                             worker.py (120 s watchdog)
    │                                                             GLB header validated
    └──────────── ready + commit-pinned CDN url ◄────── publish job (serialised) → assets branch
```

No server to maintain, nothing to install on your phone, base assets are never modified.

---

## Deploy checklist (this order — each step is independently verifiable)

| # | Do this | Prove it worked |
| --- | --- | --- |
| 1 | Push base model + textures + `LICENSE`; run **Actions → populate-manifest** | `bases/<id>/manifest.json` shows real bones/materials, `up_axis`, `height_m` |
| 2 | Create the `assets` orphan branch (or let the first build create it) | `git ls-remote origin assets` |
| 3 | **Actions → build-asset → sample** (Groq untouched) | run is green, `out_sample.glb` opens in `<model-viewer>` |
| 4 | Run `tools/groq_preflight.py` (§5) | at least one model says `VISION OK` |
| 5 | `wrangler deploy`, then `curl /health?models=1` and `/models?probe=1` | chain listed, no model in `missing_from_account` |
| 6 | One real job from a 512 px image | `/status/<id>` → `ready:true`, GLB downloads |
| 7 | Two jobs at once | both publish, no non-fast-forward failure |
| 8 | Enable Pages, run the whole flow in mobile Chrome | preview renders on the phone |

Steps 1–3 are pure Blender/publish — **do not touch Groq before step 3 is green**, otherwise a red run
tells you nothing about which half is broken.

---

## 0. What you need

| Thing | Cost | Phone-friendly? |
| --- | --- | --- |
| GitHub account + public repo | free | yes |
| Groq API key | free tier | yes |
| Cloudflare Workers | free (10 ms CPU) or $5/mo (30 s CPU) | yes — Termux / Cloud Shell / dash |
| GitHub Pages | free | yes |
| Blender | — | runs on GitHub's runner, you never install it |

Public repo is required: jsDelivr + Pages + `raw.githubusercontent.com` all need it, and the Worker reads
`ops_spec.md` / `manifest.json` unauthenticated.

---

## 1. Base model

```
bases/female_base_v3/
├─ model.gltf          # must reference model.bin by RELATIVE path
├─ model.bin
├─ textures/*
├─ LICENSE             # required, machine readable
└─ manifest.json       # names + geometry facts (step 1b)
```

If `model.gltf` says `"uri": "file:///C:/Users/..."`, fix it to `"uri": "model.bin"`.
Export from Blender with **glTF 2.0 → Format: glTF Separate**. Keep it under ~20 MB.

### 1b. License + manifest

`bases/<id>/LICENSE`:

```
commercial: true
source: https://example.com/character-pack
author: Some Artist
license: CC-BY-4.0
attribution: "Character base by Some Artist, CC-BY-4.0"
```

`worker.py` reads the first line, echoes it in every build result and warns the UI when it is `false`.
Outputs of a non-commercial base must not ship. Reference images are logged per job as
`user-provided — user is responsible`.

Then **Actions → populate-manifest → Run**. It fills `meshes`, `bones`, `materials`, `shape_keys`,
`hair.meshes`, plus the geometry facts the executor refuses to hardcode:

| field | meaning |
| --- | --- |
| `up_axis` | `auto` → detected from the bounding box; pin to `X`/`Y`/`Z` if the model is Y-up/lying down |
| `height_m` | longest extent, used to sanity-check banding |
| `origin_height` | where the origin sits along the up axis (feet ≠ 0 for some rigs) |
| `shape_key_edit` | `false` → vertex ops skip meshes with blend shapes instead of distorting them |

No armature? Set `"skinning": false`, leave `bones: []`. `bone_resize` then no-ops with a warning and
`add_mesh` parents to the armature object instead of a bone. Everything else still works.

---

## 2. Fine-grained GitHub PAT

Settings → Developer settings → Personal access tokens → **Fine-grained** → Generate:

* Repository access: **Only select repositories** → this repo
* Permissions: **Contents: Read and write**, **Actions: Read and write** (Metadata is automatic)
* Expiry: 90 days

---

## 3. Orphan `assets` branch

First build creates it automatically. Manually:

```bash
git clone https://github.com/<you>/game-assets && cd game-assets
git checkout --orphan assets && git rm -rf . && echo "# generated GLBs" > README.md
git add . && git commit -m "init assets" && git push origin assets
```

Outputs are `out_<job_id>.glb`, never overwritten, never on `main`.

---

## 4. Test the Blender pipeline (no Groq)

**Actions → build-asset → Run workflow → `sample`**. `jobs/sample.json` applies three safe ops.

Green run ⇒ `applied: 3`, `glb ok · N bytes`, and `assets` gains `out_sample.glb`. If this is red, the
problem is Blender/publish, not the LLM.

---

## 5. Groq: verify the models before trusting them

This is the step that silently kills deployments. Docs listings and reality drift apart, and a model
that exists can still refuse images.

**Current state, checked against `console.groq.com/docs/models` on 2026-11-07:**

| model id | tier | input | verdict |
| --- | --- | --- | --- |
| `qwen/qwen3.8-27b` | Preview | **Text, images** (vision + JSON object mode, ≤3 images, 2048 tok/image) | primary ✅ |
| `openai/gpt-oss-120b` | Production | text only | validator only — never in `VISION_MODELS` |
| `qwen/qwen3.6-27b` | — | not in the lineup | ❌ don't configure it |
| `meta-llama/llama-4-scout-17b-16e-instruct` | — | retired | ❌ |

Re-check before every deploy:

```bash
export GROQ_API_KEY=gsk_...
python3 tools/groq_preflight.py            # both checks, stdlib only, works in Termux
```

It does exactly this per candidate:

```bash
# 1. does the id exist on YOUR account?
curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY" \
  | python3 -c "import json,sys;print('\n'.join(m['id'] for m in json.load(sys.stdin)['data']))"

# 2. does it actually take image_url? (a 400 "does not support" means it is dead for this pipeline)
curl -s https://api.groq.com/openai/v1/chat/completions -H "Authorization: Bearer $GROQ_API_KEY" \
  -H "Content-Type: application/json" -d '{"model":"qwen/qwen3.8-27b","max_tokens":8,
  "messages":[{"role":"user","content":[{"type":"text","text":"ok"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}}]}]}'
```

Put the winners in `backend/wrangler.toml` → `VISION_MODELS` (ordered CSV). After deploying you can also
ask the Worker itself:

```bash
curl https://<worker>/health?models=1     # chain vs. what your account actually has
curl https://<worker>/models?probe=1      # live image probe per model
```

**The Worker defends itself either way:** it filters `VISION_MODELS` against `/openai/v1/models`, and any
model that answers an image request with `400 … image … not supported` is demoted to `text` for 24 h and
never retried as primary. If every configured model is dead you get a clean `502 no_vision_model` instead
of a mystery 404.

---

## 6. Deploy the Worker

```bash
pkg install nodejs-lts git          # Termux; on Cloud Shell just install node 20+
npm i -g wrangler
cd backend
wrangler login
wrangler kv namespace create RATE_KV     # paste id into wrangler.toml
wrangler secret put GROQ_API_KEY
wrangler secret put GH_PAT
wrangler deploy
curl https://<worker>/health?models=1
```

Dashboard alternative: Workers & Pages → Create → paste `backend/src/index.js` → Settings → Variables
(add secrets **encrypted**, bind KV as `RATE_KV`).

| var | kind | example |
| --- | --- | --- |
| `GROQ_API_KEY`, `GH_PAT` | secret | — |
| `REPO` | var | `you/game-assets` |
| `CDN_BASE` | var | `https://cdn.jsdelivr.net/gh/you/game-assets` |
| `ALLOWED_ASSETS` | var | `female_base_v3` |
| `BASE_BRANCH` / `ASSETS_BRANCH` | var | `main` / `assets` |
| `VISION_MODELS` | var | `qwen/qwen3.8-27b` |
| `VALIDATOR_MODEL`, `ENABLE_VALIDATOR` | var | `openai/gpt-oss-120b`, `false` |
| `GROQ_REASONING_EFFORT` | var | `none` (thinking mode off — fewer tokens, cleaner JSON) |
| `ALLOWED_ORIGINS` | var | `*` while testing, then your Pages origin |
| `RATE_KV` | KV binding | created above |

### CPU: 10 ms free / 30 s paid

Wall-clock time waiting on Groq/GitHub does not count; CPU does. Base64 of a 512 px JPEG plus JSON
validation stays well under 10 ms, so the free plan works for this design. If you hit `[1102]` /
"Worker exceeded CPU": move to Workers Paid ($5, 30 s), or switch to the **thin mode** described in §11 —
that one also removes the PAT from the Worker.

---

## 7. Web UI (Pages)

Settings → Pages → Source: **Deploy from a branch** → `main` / `/web`.
Open `https://<you>.github.io/game-assets/`, paste the Worker URL, **Save** (it is remembered in
`localStorage`; nothing is hardcoded in `app.js`).

Also: Settings → Actions → General → Workflow permissions → **Read and write**.

---

## 8. Use

1. Tap the image box, pick a reference (downscaled to 512 px on-device).
2. Optional prompt: `taller, wider hips, red armor, long dark hair`.
3. **Generate** → ops list appears (this is exactly what will run).
4. Polling backs off 2 → 4 → 8 → 10 s (jsDelivr 404s new commits for up to ~2 min; hammering wastes requests).
5. On ready: `<model-viewer>` with `camera-controls` + `auto-rotate`, download button, mirror fallbacks.

Cold build ≈ 2–4 min (Blender download is cached after the first run), warm ≈ 60–90 s.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `502 no_vision_model` | Nothing in `VISION_MODELS` accepts images. Run `tools/groq_preflight.py`, fix the CSV. |
| `502 groq_failed` + `status: 404` | Model id gone. The Worker marks it `missing` for 24 h; replace the id. |
| `502 groq_failed` + `status: 400 … image` | Model is text-only for your account. Auto-demoted; pick another. |
| `422 no_valid_ops` | Model returned only junk ops. Shorten the prompt, or enable `ENABLE_VALIDATOR`. |
| Action green, `applied: 0` | Manifest names don't match the mesh. Read the run Summary `errors[]`. |
| `GLB validation failed` / `glb header truncated` | Export died mid-write — the file is **not** published. Look for `watchdog` or exporter errors above it. |
| Worker exit `3` / `__timeout__` | Hit the 120 s watchdog. Fewer ops, or a lighter base model. |
| `nothing to publish — identical …` | Same job re-run with byte-identical output. Correct behaviour, not an error. |
| `could not publish after 4 attempts` | Publish is serialised (`concurrency: assets-publish`); this means 4 consecutive rejects. Re-run the job. |
| `add_mesh` ignored | `loc` outside `[-2,2]³` or `scale` outside `[0.01,1]`. |
| Props float when the character poses | Base has no rig — `attach_bone` needs an armature; check `manifest.bones`. |
| `proportion` changes the wrong region | Model isn't Z-up or is offset. `populate-manifest` writes `up_axis`/`height_m`; pin `up_axis`. |
| Morph targets distort after a build | Shape keys + vertex edits. Set `shape_key_edit: false` in the manifest. |
| Blender `libGL` / exit `-6` | Missing `libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 xz-utils`. |
| Blender download 404 | Mirror dropped the version. Bump `BLENDER_VERSION` **and** `BLENDER_DIR` together. |
| `ready:false` forever | Actions tab: the run failed. If green, jsDelivr is cold — the UI retries via `statically.io`/raw. |
| `<model-viewer>` blank, download works | CDN cache miss. Download button fetches a blob and works regardless. |
| CORS error | `ALLOWED_ORIGINS` doesn't include the Pages origin. |
| `429 rate_limited_*` | 20/h per IP, 200/day global — raise in `backend/src/index.js` deliberately. |

---

## 10. Security checklist

- [ ] PAT is fine-grained, one repo, `Contents: RW` + `Actions: RW`, with an expiry.
- [ ] `GH_PAT` / `GROQ_API_KEY` only as Worker secrets — never in `wrangler.toml`, never in git.
- [ ] Workflows use the ephemeral `GITHUB_TOKEN`, never your PAT.
- [ ] `ALLOWED_ASSETS` whitelist set; anything else is rejected before Groq is called.
- [ ] ≤ 12 ops; every number clamped twice (Worker + `worker.py`).
- [ ] `add_mesh` `loc` ∈ `[-2,2]³`, `scale` ∈ `[0.01,1]`; `attach_bone` must exist in the manifest.
- [ ] Only the 6 op names survive validation — the model cannot smuggle code in.
- [ ] `/status/:job_id` only accepts `^[a-f0-9]{8,32}$` (no traversal), returns `Cache-Control: no-store`.
- [ ] Job ids are validated against `^[a-z0-9][a-z0-9_-]{0,40}$` before touching the filesystem.
- [ ] Publish is serialised **and** retried with rebase — no lost outputs under concurrency.
- [ ] Exported GLB is header-validated (magic/version/length) before it can reach `assets`.
- [ ] Watchdog kills the process instead of SIGALRM; a killed run leaves only `out.glb.part`.
- [ ] Rate limits KV-backed, per IP + global; degraded mode is reported, never silent.
- [ ] `bases/*/LICENSE` present with `commercial:`; surfaced in every result and in the UI.
- [ ] Non-commercial outputs are never shipped.
- [ ] Public repo ⇒ base assets are public. Don't upload paid assets.

---

## 11. Known limits & when to change the architecture

* **No sculpting / retopology.** The face, topology and surface detail stay as the base. `proportion` is a
  smooth regional vertex scale, not a sculpt. The UI says so up front — keep it that way, or users will
  read "different face" as a bug.
* **Shape keys.** Vertex edits move the basis, so morph targets distort. `shape_key_edit: false` skips
  those meshes (and `worker.py` warns either way).
* **Hair is a stylised proxy** unless your base ships real hair meshes per style (`manifest.hair.styles`).
* **Rig consistency is approximate**: bones are mirrored with the region, weight paint is untouched.
* **jsDelivr caps files at 20 MB.** The build warns above that, `/status` returns `cdn_limited` plus an
  `alt_cdn_url`, and the UI falls back automatically. For anything serious, move outputs to **Cloudflare
  R2** (free egress via Workers) or GitHub Releases and point `CDN_BASE` at it.
* **Actions minutes**: a private repo gets 2,000 min/month; a build is ~2–3 min cold, ~1 min warm.
  Watch **Settings → Billing** after ~5 builds. A public repo is free.
* **Single reference image.** Multi-angle fusion, texture baking, UV regeneration, rig auto-generation =
  v2.
* **Thin mode (v1.1)** — if Worker CPU becomes the constraint, move the Groq call into the workflow:
  the Worker commits the 512 px reference to a `refs/<job_id>` branch (or R2), dispatches with just the
  path, and a `tools/plan_ops.py` step calls Groq and fills `ops` before `worker.py` runs. Costs one
  extra Action step; buys a Worker that does no base64/JSON work and a PAT that only needs
  `contents:write` + `actions:write` on a scratch branch.

---

## 12. Post-deploy test checklist (~20 min)

| test | expected |
| --- | --- |
| `jobs` with `ops: []` | clean copy of the base exported, `ok:true`, warning "no ops", run green |
| 12 ops | all applied |
| 13 ops | Worker truncates with a warning; worker caps at 12 anyway |
| `material_param` on a bogus material | dropped with a warning, build continues |
| `add_mesh` with `loc:[9,9,9]` | clamped to `[2,2,2]`, no error |
| 8 MB reference PNG | client resizes to 512 px, Worker never returns 413 |
| base without an armature | `bone_resize` warns + skips, GLB exports without skins |
| same `job_id` twice | second publish reports "nothing to publish", run green |
| two jobs at once | both `out_*.glb` present on `assets` |
| 21st request in an hour | `429 rate_limited_ip` |
| 25 MB GLB | published, `cdn_limited:true`, UI uses the mirror CDN |

---

## 13. Local checks (no Blender, no cloud)

```bash
python3 -c "import ast;ast.parse(open('worker.py').read())"
node --check web/app.js
cd backend && npm run check                      # node --check src/index.js
python3 -m json.tool bases/female_base_v3/manifest.json >/dev/null
```

The three suites used while building this (stub `bpy`, mocked Worker fetch, jsdom UI) are not committed:
they need a fake Blender and add noise to a repo you deploy from. Re-create them if you change op
semantics.

---

## Assumptions

1. `asset_id` is `female_base_v3` with an armature; otherwise set `"skinning": false`.
2. Metres, 1.6–1.9 m characters, Z-up after import (auto-detected, override with `up_axis`).
3. Base is public and commercially usable; the repo is public.
4. Branches: `main` (source) + `assets` (outputs, created on first publish).
5. Models face **-Y in Blender** (standard glTF import). Hair is placed behind the head on that basis —
   override `manifest.hair.forward_axis` if yours faces +Y.
6. Groq free tier (30 RPM / 1k RPD); the Worker's 20/h + 200/day sit below it on purpose.
7. `qwen/qwen3.8-27b` remains available and multimodal on your account — verified by
   `tools/groq_preflight.py` and `/models?probe=1`, not by hope.
