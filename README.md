# game-assets — prompt → Blender → `.glb` pipeline (phone-only ops)

Upload a reference character image + a short prompt → a Groq vision model emits a **JSON ops DSL**
(never raw `bpy` code) → GitHub Actions runs **headless Blender** → a new `out_<job_id>.glb` lands on an
orphan `assets` branch → the web UI previews it in `<model-viewer>`.

```
[browser]  image + prompt
    │  POST /generate
    ▼
[Cloudflare Worker]  ── Groq (vision) ─► ops JSON ── sanitise/clamp ──► commit jobs/<id>.json
    │                                                                        │ repository_dispatch
    │  GET /status/<id>                                                      ▼
    │                                                        [GitHub Actions · Blender 4.2 LTS]
    │                                                             worker.py (120 s self-kill)
    └──────────── ready + CDN url ◄──────────────── assets branch: out_<id>.glb (jsDelivr)
```

No server to maintain, nothing to install on your phone, base assets are never modified.

---

## Deploy checklist (in order)

1. Create a **public** repo and upload this project (`main` branch).
2. Upload `bases/female_base_v3/{model.gltf,model.bin,textures/}` and replace `bases/female_base_v3/LICENSE`.
3. **Actions → populate-manifest → Run** with `female_base_v3` → commits real mesh/bone/material names.
4. Create the fine-grained PAT: *Contents RW* + *Actions RW*, this repo only (§2).
5. **Actions → build-asset → Run** with `sample` → confirms Blender + publish work before Groq is involved.
6. Create the Groq key; verify the three model IDs against `console.groq.com/docs/models` (§5).
7. `wrangler kv namespace create RATE_KV` → paste id into `backend/wrangler.toml`.
8. `wrangler secret put GROQ_API_KEY` and `wrangler secret put GH_PAT`; edit `[vars]` (`REPO`, `CDN_BASE`, `ALLOWED_ASSETS`).
9. `wrangler deploy` → `curl https://<worker>/health` must return `ok:true`.
10. Settings → Pages → `main` / `/web` → open `https://<you>.github.io/<repo>/`.
11. In the UI: paste the Worker URL → Save → upload an image → Generate → wait for the `<model-viewer>` preview.
12. Run through §10 (security checklist) once, then set `ALLOWED_ORIGINS` to your Pages origin instead of `*`.

---

## 0. What you need

| Thing | Cost | Phone-friendly? |
| --- | --- | --- |
| GitHub account + public repo | free | yes (web / GitHub mobile) |
| Groq API key | free tier | yes (console.groq.com in Chrome) |
| Cloudflare account + Workers | free tier | yes (dash) or Termux/Cloud Shell for `wrangler` |
| GitHub Pages | free | yes |
| Blender | — | runs on GitHub's runner, you never install it |

**Time to first `.glb`: ~25 minutes**, most of it waiting for GitHub to install Blender once (then it is cached).

---

## 1. Create the repo and upload the base model

1. Create a **public** repo, e.g. `game-assets` (public = jsDelivr CDN + GitHub Pages for free).
2. Upload this project's files. On a phone the easiest path is **Add file → Upload files** in the GitHub
   web UI (it accepts drag/drop from the Files app), or `git` in Termux.
3. Put the base character in:

```
bases/female_base_v3/
├─ model.gltf
├─ model.bin
├─ textures/albedo.png        # any number of textures
├─ LICENSE                    # REQUIRED — see step 1b
└─ manifest.json              # bone/mesh/material names (step 3)
```

> The `.gltf` must reference `model.bin` by a **relative** path. If it says
> `"uri": "file:///C:/Users/..."` or an absolute URL, fix it: `"uri": "model.bin"`.
> Exporting from Blender with **File → Export → glTF 2.0 → Format: glTF Separate** does the right thing.
> Keep total base size small (< 20 MB) so `git` on a phone stays sane.

### 1b. License tracking (do not skip)

Every base asset needs `bases/<id>/LICENSE` in this exact machine-readable shape:

```
commercial: true
source: https://example.com/character-pack
author: Some Artist
license: CC-BY-4.0
attribution: "Character base by Some Artist, CC-BY-4.0"
notes: redistributed unmodified
```

`commercial: false` assets are still buildable locally, but you must not ship their outputs.
Reference images you upload are treated as **user-provided — the user is responsible** for having the
rights to them; the Worker records that fact in every job file.

---

## 2. Create the fine-grained GitHub PAT

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**

* **Repository access:** Only select repositories → `game-assets`
* **Permissions:**
  * *Contents* → **Read and write** (commit job files)
  * *Actions* → **Read and write** (trigger the workflow)
  * *Metadata* → Read-only (auto)
* **Expiration:** 90 days (calendar reminder)

Copy the `github_pat_…` value — it goes into the Worker as `GH_PAT` in step 6. Never commit it.

---

## 3. Fill the manifest (one-shot Action)

`bases/female_base_v3/manifest.json` ships as a skeleton. It tells the LLM and the executor which
**exact** mesh/bone/material names exist. If you already know them, just type them in. If not:

1. **Actions → populate-manifest → Run workflow**
2. `asset_id`: `female_base_v3` → Run.
3. The action imports your `.gltf` in Blender, dumps real names, and commits `manifest.json` back to `main`.

Verify by opening `bases/female_base_v3/manifest.json`. You should see arrays of names like
`["Body", "Hair_Long", "Armor_Chest"]`. Optionally prune entries you don't want the AI to touch, then
re-run **nothing** — the manifest is read fresh at build time.

> If your base is a **static mesh with no armature**, leave `"armature": ""`, `"bones": []`, and set
> `"skinning": false`. `worker.py` then skips all rig work (`bone_resize` becomes a no-op warning) and
> only does vertex/material/mesh ops. Do not set `skinning: true` on a rig-less model — the export will
> still work but the GLB will have no skeleton.

---

## 4. Create the orphan `assets` branch

Outputs live on a branch that has **no shared history with `main`**, so `main` stays small and CDN
serving stays clean.

Easiest — let the pipeline do it: the first successful build auto-creates the branch (the workflow
handles "branch does not exist"). If you want it up front, from Termux / Cloud Shell:

```bash
git clone https://github.com/<you>/game-assets && cd game-assets
git checkout --orphan assets
git rm -rf .            # keep the folder empty
echo "# generated GLBs" > README.md
git add README.md && git commit -m "init assets branch"
git push origin assets
```

Never put source assets on `assets` — remember it has no `main` files.

---

## 5. Groq: key + model IDs

1. console.groq.com → **API Keys** → create → copy `gsk_…`.
2. **Confirm the model IDs before deploying.** They change; the defaults in `backend/wrangler.toml` are:

| Role | Default model ID | Notes |
| --- | --- | --- |
| vision primary | `qwen/qwen3.8-27b` | image + text → ops JSON |
| vision fallback | `qwen/qwen3.6-27b` | used when the primary errors or returns non-JSON |
| JSON validator (optional) | `openai/gpt-oss-120b` | text-only second pass, `ENABLE_VALIDATOR="true"` |
| upgrade path | `meta-models/muse-spark-open` | needs a Groq access request |

Check them (Termux or any shell):

```bash
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY" | grep -o '"id":"[^"]*"' | sort
```

If an ID is missing, change **only** `backend/wrangler.toml` `[vars]` (or `wrangler secret put` /
dash var) — model IDs are read from env everywhere, never from code.

---

## 6. Deploy the Cloudflare Worker

The Worker is the only dynamic endpoint. Three ways, pick one:

**A. Termux (phone, recommended if you already use it)**

```bash
pkg install nodejs-lts git
npm i -g wrangler
cd backend
wrangler login                       # opens a browser URL, tap "Allow"
wrangler kv namespace create RATE_KV  # copy the id=... output
# paste that id into wrangler.toml → kv_namespaces[0].id
wrangler secret put GROQ_API_KEY      # paste gsk_...
wrangler secret put GH_PAT            # paste github_pat_...
wrangler deploy
# → https://game-assets.<subdomain>.workers.dev
```

**B. Google Cloud Shell** (browser, no phone packages): same commands, `curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -` first if node is old.

**C. Cloudflare dashboard** → Workers & Pages → Create → paste `backend/src/index.js` → Settings →
Variables (add `GROQ_API_KEY`, `GH_PAT`, `REPO`, `CDN_BASE`, `ALLOWED_ASSETS` as **encrypted** vars) →
Deploy. KV: Storage & Databases → KV → create `RATE_KV` → bind it to the Worker as `RATE_KV`.

Smoke test:

```bash
curl -s https://game-assets.<subdomain>.workers.dev/health
# {"ok":true,"asset":"female_base_v3",...}
```

### Worker env reference

| Var | Where | Example |
| --- | --- | --- |
| `GROQ_API_KEY` | secret | `gsk_…` |
| `GH_PAT` | secret | `github_pat_…` |
| `REPO` | var | `yourname/game-assets` |
| `CDN_BASE` | var | `https://cdn.jsdelivr.net/gh/yourname/game-assets` |
| `ALLOWED_ASSETS` | var | `female_base_v3` (comma separated) |
| `BASE_BRANCH` | var | `main` |
| `ASSETS_BRANCH` | var | `assets` |
| `VISION_MODEL` / `VISION_FALLBACK_MODEL` / `VALIDATOR_MODEL` | var | see step 5 |
| `ENABLE_VALIDATOR` | var | `false` |
| `ALLOWED_ORIGINS` | var | `*` or `https://you.github.io` |
| `RATE_KV` | KV binding | created in step 6 |

---

## 7. Turn on GitHub Pages for the web UI

**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` / folder: `/web`** → Save.
Wait ~1 min, open `https://<you>.github.io/game-assets/`.

First run: the UI asks for the **Worker URL** (paste `https://game-assets.<subdomain>.workers.dev`) and
stores it in `localStorage`. Nothing is hardcoded, so you can redeploy the Worker without editing `app.js`.

Also enable the Actions side: **Settings → Actions → General → Workflow permissions →
Read and write permissions** (the workflow already declares `permissions: contents: write`, but the repo
toggle must allow it).

---

## 8. Test the Blender pipeline before touching Groq

```bash
# locally? no. Do this from the GitHub UI:
Actions → build-asset → Run workflow → job_id: sample → Run
```

`jobs/sample.json` applies three harmless ops (a `material_param`, a `proportion`, an `add_mesh`).
Expected: green run, `Summary` shows `applied: 3, errors: 0`, and
`assets` branch gains `out_<job_id>.glb`. Open the job log and look for the `RESULT` block.

Then test end-to-end from the web UI: upload any character image, prompt
`taller, wider hips, red armor, long dark hair`, Generate. Polling stops when
`out_<job_id>.glb` appears (typically 2–4 min cold, ~90 s warm).

---

## 9. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Action green but `applied: 0` | Manifest names don't match the model. Check `manifest.json` and read `errors[]` in the run Summary. |
| `add_mesh` ignored | `loc` outside `[-2,2]³` or `scale` outside `[0.01,1]` — clamped by design. Model units matter: a 1.7 m tall character expects loc ~`[0.3,0,1.4]`. |
| `material_param` rejected | Material not in `manifest.materials`. Populate the manifest (step 3). |
| Blender exits `-6`/`libGL` error | Missing system libs. The workflow installs `libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 xz-utils`; if you edited the step, restore it. |
| Download 404 during "Download Blender" | Version pin removed from the mirror. Bump `BLENDER_VERSION`/`BLENDER_DIR` in `build.yml` (both must match, e.g. `4.2.9`). The step tries 3 mirrors. |
| `fatal: could not read Username` on publish | Repo toggle in step 7 (*Read and write permissions*) is off, or your PAT was used in the workflow (it isn't — the workflow uses `GITHUB_TOKEN`). |
| Worker `502 groq_failed` | Bad/expired key, or the model ID is gone. Re-check step 5. |
| Worker `422 job_exists` / push races | Harmless — the Worker retries with a new `job_id` (up to 3). |
| `ready:false` forever | Check the run: **Actions** tab. If it failed at export, the model likely has shape keys + `export_apply`. See "Known limits". |
| jsDelivr serves a stale 404 | The UI falls back to a `raw.githubusercontent.com` URL automatically; jsDelivr's 404 cache clears in a few minutes. |
| `<model-viewer>` blank but download works | The GLB references external textures — re-export embedded. `worker.py` always exports self-contained GLB. |
| CORS error in console | `ALLOWED_ORIGINS` doesn't include your Pages origin. Use `*` while testing. |
| Rate limited (`429`) | 20 requests/hour/IP, 200/day global. Wait, or bump the constants in `backend/src/index.js` consciously. |

---

## 10. Security checklist

- [ ] PAT is **fine-grained**, scoped to this one repo, `Contents: RW` + `Actions: RW` only, with an expiry.
- [ ] `GH_PAT` and `GROQ_API_KEY` exist **only** as Worker secrets, never in `wrangler.toml`, never in git.
- [ ] No secrets in the workflow — it uses the ephemeral `GITHUB_TOKEN`.
- [ ] `ALLOWED_ASSETS` whitelist is set; the Worker rejects any other `asset_id` before calling Groq.
- [ ] Ops are capped at 12 and every number is clamped **twice** (Worker + `worker.py`).
- [ ] `add_mesh` `loc` is clamped to `[-2,2]³`, `scale` to `[0.01,1]`.
- [ ] The LLM cannot emit code: only the 6 op names in `ops_spec.md` survive validation; anything else is
      stripped (Worker) or hard-fails the op (`worker.py`).
- [ ] Action `timeout-minutes: 15`, `worker.py` self-kills at 120 s (`signal.alarm`).
- [ ] Rate limits: 20/hour per IP, 200/day global (KV-backed).
- [ ] Base assets are read-only inputs; every build writes a **new** `out_<job_id>.glb`, old ones are kept.
- [ ] `bases/*/LICENSE` exists with `commercial:` set; reference images are logged as user-provided.
- [ ] `/status` only accepts `^[a-f0-9]{8,32}$` — no path traversal, no arbitrary file probing.
- [ ] Public repo ⇒ base assets are public. Don't upload paid assets.

---

## Layout

```
bases/female_base_v3/{model.gltf,model.bin,textures/,LICENSE,manifest.json}
jobs/                      incoming job JSON (committed by the Worker)
jobs/sample.json           manual test job
assets/                    placeholder on main; real outputs live on the `assets` orphan branch
ops_spec.md                the LLM contract (system prompt)
worker.py                  Blender executor (ops dispatch + clamps + 120 s watchdog)
tools/populate_manifest.py manifest dumper used by the one-shot Action
.github/workflows/build.yml             Blender build + publish
.github/workflows/populate-manifest.yml one-shot manifest dumper
backend/src/index.js       Cloudflare Worker (Groq + GitHub + rate limit + CORS)
backend/wrangler.toml      Worker config
backend/package.json       wrangler devDependency
web/{index.html,app.js}    vanilla JS UI, Pages-deployable, no build step
```

---

## Known limits (non-goals for v1)

* **No sculpting / retopology.** Headless Blender can't do organic remodelling; `proportion` is a smooth
  regional vertex scale, not a sculpt. Silhouette changes are soft and local.
* **Stylised hair proxy only.** `hair_swap` prefers pre-modelled hair meshes declared in
  `manifest.hair.styles`; otherwise it builds a simple primitive-based hair cap/mass. Not hair cards.
* **Rig consistency is approximate.** When vertices are scaled, matching bones are scaled too, but weight
  paint is untouched — expect small deformation drift on extreme poses.
* **Single reference image.** Multi-angle fusion, texture baking, UV regeneration and rig auto-generation
  are v2.
* **Output is a `.glb` only.** Unity/Godot/Unreal import it as-is; there are no engine plugins.
* Shape keys + `export_apply=True` can make Blender warn; if you need morphs, drop `export_apply` in
  `worker.py` (one line) and apply modifiers explicitly instead.

---

## Assumptions

1. `asset_id` is `female_base_v3` and it ships an armature. If not, set `"skinning": false` in the manifest
   (see step 3) — rig ops then no-op with warnings instead of failing.
2. Units are metres, ~1.6–1.9 m tall characters; `add_mesh` clamps assume that scale.
3. The base is public-domain/commercially usable and **public** (CDN + Pages need public).
4. Repo is `main` (base) + `assets` (outputs); the workflow creates `assets` on first publish if missing.
5. Models face **-Y in Blender** (standard glTF import, +Z forward in glTF). Hair is placed behind the head
   on that basis; override with `manifest.hair.forward_axis` if your model faces +Y.
6. Groq free tier is used: 30 RPM / 1k RPD — the Worker's 20/h + 200/day limits are deliberately below it.
7. `qwen/qwen3.8-27b` accepts image input on your account; the Worker falls back to
   `qwen/qwen3.6-27b` and then to a JSON-repair pass. Verify in step 5.
