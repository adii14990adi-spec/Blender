/**
 * game-assets Worker — the only dynamic endpoint.
 *
 *   POST /generate        { image_b64 | images[], prompt, asset_id } -> { job_id, ops, warnings }
 *   GET  /status/:job_id  -> { ready, url, fallback_url, ... }
 *   GET  /assets          -> { assets: [...] }
 *   GET  /health          -> { ok, ... }
 *
 * Responsibilities
 *   - hold the Groq key and the fine-grained GitHub PAT (never exposed to the browser)
 *   - ask a vision model for a JSON ops document (system prompt = ops_spec.md from the repo)
 *   - validate + clamp every op (the Blender worker clamps again — defence in depth)
 *   - commit jobs/<job_id>.json and trigger the build workflow
 *   - report when the output GLB exists, with immutable CDN URLs
 *   - rate-limit per IP and globally, KV-backed
 *
 * Secrets / vars: see wrangler.toml and README.md.
 */

const MAX_OPS = 12;
const MAX_IMAGES = 3;
const MAX_BODY_BYTES = 8 * 1024 * 1024; // 8 MB of base64
const MAX_IMAGE_CHARS = 2_600_000; // ~1.9 MB decoded per image
const PER_IP_LIMIT = 20; // per rolling hour
const GLOBAL_LIMIT = 200; // per day
const GROQ_TIMEOUT_MS = 45_000;
const MODELS_TTL = 60 * 60; // Groq /models cache
// 1x1 PNG, used by /models?probe=1 to prove a model really accepts image_url
const PROBE_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const FACTOR_MIN = 0.7;
const FACTOR_MAX = 1.3;
const LOC_ABS_MAX = 2;
const SCALE_MIN = 0.01;
const SCALE_MAX = 1;

const OPS = new Set(["set_scale", "proportion", "bone_resize", "material_param", "add_mesh", "hair_swap"]);
const PRIMITIVES = new Set(["cube", "sphere", "cylinder", "cone", "plane", "torus"]);
const HAIR_STYLES = new Set([
  "long_straight", "long_wavy", "bob", "ponytail", "twin_tails", "short", "buzz", "bald", "as_is",
]);
const PARTS = new Set([
  "head", "neck", "shoulders", "chest", "waist", "hips", "pelvis", "thighs",
  "legs", "calves", "arms", "hands", "feet", "height",
]);
const AXES = new Set(["x", "y", "z", "all"]);

const PARAM_ALIASES = {
  "base color": "Base Color", basecolor: "Base Color", diffuse: "Base Color", albedo: "Base Color",
  color: "Base Color", colour: "Base Color",
  metallic: "Metallic", metalness: "Metallic",
  roughness: "Roughness",
  alpha: "Alpha", opacity: "Alpha",
  emission: "Emission Color", emissive: "Emission Color", "emission color": "Emission Color",
  "emission strength": "Emission Strength",
  specular: "Specular IOR Level", "specular ior level": "Specular IOR Level",
  transmission: "Transmission Weight", "transmission weight": "Transmission Weight",
  coat: "Coat Weight", clearcoat: "Coat Weight", "coat weight": "Coat Weight",
  sheen: "Sheen Weight", "sheen weight": "Sheen Weight",
  ior: "IOR",
  subsurface: "Subsurface Weight", "subsurface weight": "Subsurface Weight",
  "normal strength": "Normal Strength",
};
const COLOR_PARAMS = new Set(["Base Color", "Emission Color"]);
const PARAM_RANGES = {
  Metallic: [0, 1], Roughness: [0, 1], Alpha: [0, 1], "Emission Strength": [0, 5],
  "Specular IOR Level": [0, 1], "Transmission Weight": [0, 1], "Coat Weight": [0, 1],
  "Sheen Weight": [0, 1], "Subsurface Weight": [0, 1], IOR: [1, 2.5], "Normal Strength": [0, 2],
};

class HttpError extends Error {
  constructor(status, code, extra) {
    super(code);
    this.status = status;
    this.code = code;
    this.extra = extra || {};
  }
}

// --------------------------------------------------------------------------- //
// tiny helpers
// --------------------------------------------------------------------------- //

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });

function corsHeaders(env, request) {
  const allowed = (env.ALLOWED_ORIGINS || "*").split(",").map((s) => s.trim()).filter(Boolean);
  const origin = request.headers.get("Origin") || "";
  const allow = allowed.includes("*") ? "*" : allowed.includes(origin) ? origin : allowed[0] || "*";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

const clampNum = (value, lo, hi) => {
  const n = typeof value === "string" ? Number(value) : value;
  if (typeof n !== "number" || !Number.isFinite(n)) return null;
  return Math.min(hi, Math.max(lo, n));
};

const round4 = (n) => Math.round(n * 10000) / 10000;

function normalizeColor(value) {
  if (typeof value !== "string") return null;
  let s = value.trim().toLowerCase().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/.test(s)) s = s.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/.test(s)) return null;
  return "#" + s;
}

function b64encode(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function b64decode(text) {
  const binary = atob(text.replace(/\s/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder("utf-8").decode(bytes);
}

function stripJsonFences(text) {
  let s = String(text || "").trim();
  s = s.replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  const first = s.indexOf("{");
  const last = s.lastIndexOf("}");
  if (first >= 0 && last > first) s = s.slice(first, last + 1);
  return s;
}

function tryParseJson(text) {
  const candidate = stripJsonFences(text);
  try {
    return JSON.parse(candidate);
  } catch {
    // last-ditch repair: trailing commas
    try {
      return JSON.parse(candidate.replace(/,\s*([}\]])/g, "$1"));
    } catch {
      return null;
    }
  }
}

async function withTimeout(promiseFactory, ms, label) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(label + " timeout")), ms);
  try {
    return await promiseFactory(controller.signal);
  } finally {
    clearTimeout(timer);
  }
}

// --------------------------------------------------------------------------- //
// GitHub
// --------------------------------------------------------------------------- //

async function gh(env, path, init = {}) {
  const url = path.startsWith("http") ? path : `https://api.github.com/repos/${env.REPO}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "game-assets-worker",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
  });
  return res;
}

async function fetchRepoFile(env, path) {
  // raw.githubusercontent first (cheap, no auth, no API quota), then the contents API.
  // Edge cache is 60s, NOT an hour: editing ops_spec.md or the manifest must show up on the
  // next job, not after lunch. KV only ever holds the last known-good copy as a fallback.
  const raw = `https://raw.githubusercontent.com/${env.REPO}/${env.BASE_BRANCH || "main"}/${path}`;
  try {
    const res = await withTimeout(
      (signal) => fetch(raw, { signal, cf: { cacheTtl: 60, cacheEverything: true } }),
      15000,
      "raw",
    );
    if (res.ok) return await res.text();
  } catch {
    /* fall through */
  }
  const res = await gh(env, `/contents/${path}?ref=${env.BASE_BRANCH || "main"}`);
  if (!res.ok) throw new HttpError(500, "repo_file_unavailable", { path, status: res.status });
  const body = await res.json();
  return body.encoding === "base64" ? b64decode(body.content || "") : String(body.content || "");
}

async function kvGet(env, key) {
  if (!env.RATE_KV) return null;
  try {
    return await env.RATE_KV.get(key);
  } catch {
    return null;
  }
}

async function kvPut(env, key, value, ttl) {
  if (!env.RATE_KV) return;
  try {
    await env.RATE_KV.put(key, value, { expirationTtl: ttl });
  } catch {
    /* cache is best effort */
  }
}

async function loadSpec(env) {
  const cacheKey = "spec:v3";
  try {
    const text = await fetchRepoFile(env, "ops_spec.md");
    if (text && text.length > 80) {
      await kvPut(env, cacheKey, text, 86400);
      return text;
    }
  } catch {
    /* fall back to the cached copy */
  }
  const cached = await kvGet(env, cacheKey);
  if (cached) return cached;
  throw new HttpError(500, "repo_file_unavailable", { path: "ops_spec.md" });
}

async function loadManifest(env, assetId) {
  const cacheKey = `manifest:${assetId}:v3`;
  try {
    const text = await fetchRepoFile(env, `bases/${assetId}/manifest.json`);
    const data = JSON.parse(text);
    // remember the version alongside the copy so a stale fallback is obvious in logs
    await kvPut(env, cacheKey, JSON.stringify({ version: data.version ?? null, text }), 86400);
    return data;
  } catch {
    const hit = await kvGet(env, cacheKey);
    if (!hit) return {};
    try {
      const parsed = JSON.parse(hit);
      console.log("manifest fallback used, version", parsed.version);
      return JSON.parse(parsed.text);
    } catch {
      return {};
    }
  }
}

// --------------------------------------------------------------------------- //
// rate limiting
// --------------------------------------------------------------------------- //

async function rateLimit(env, ip) {
  if (!env.RATE_KV) return { ok: true, degraded: true };
  const now = new Date();
  const hourKey = `rl:ip:${ip}:${now.toISOString().slice(0, 13)}`;
  const dayKey = `rl:global:${now.toISOString().slice(0, 10)}`;
  let ipCount = 0;
  let globalCount = 0;
  try {
    const [a, b] = await Promise.all([env.RATE_KV.get(hourKey), env.RATE_KV.get(dayKey)]);
    ipCount = Number(a || 0);
    globalCount = Number(b || 0);
  } catch {
    return { ok: true, degraded: true };
  }
  if (ipCount >= PER_IP_LIMIT) {
    return { ok: false, code: "rate_limited_ip", detail: `${PER_IP_LIMIT}/hour`, retryAfter: 3600 };
  }
  if (globalCount >= GLOBAL_LIMIT) {
    return { ok: false, code: "rate_limited_global", detail: `${GLOBAL_LIMIT}/day`, retryAfter: 3600 };
  }
  try {
    await Promise.all([
      env.RATE_KV.put(hourKey, String(ipCount + 1), { expirationTtl: 3600 }),
      env.RATE_KV.put(dayKey, String(globalCount + 1), { expirationTtl: 86400 }),
    ]);
  } catch {
    /* counting is best effort */
  }
  return { ok: true, remaining: PER_IP_LIMIT - ipCount - 1 };
}

// --------------------------------------------------------------------------- //
// Groq
// --------------------------------------------------------------------------- //

function visionModels(env) {
  const raw = env.VISION_MODELS || env.VISION_MODEL || "qwen/qwen3.8-27b";
  return raw.split(",").map((m) => m.trim()).filter(Boolean);
}

async function groqModelIds(env, force) {
  const cacheKey = "groq:models:v1";
  if (!force) {
    const hit = await kvGet(env, cacheKey);
    if (hit) return JSON.parse(hit);
  }
  try {
    const res = await withTimeout(
      (signal) =>
        fetch("https://api.groq.com/openai/v1/models", {
          signal,
          headers: { Authorization: `Bearer ${env.GROQ_API_KEY}` },
        }),
      15000,
      "groq_models",
    );
    if (!res.ok) return null;
    const body = await res.json();
    const ids = (body.data || []).map((m) => m.id).filter(Boolean);
    await kvPut(env, cacheKey, JSON.stringify(ids), MODELS_TTL);
    return ids;
  } catch {
    return null;
  }
}

const capability = async (env, id) => (await kvGet(env, `groq:cap:${id}`)) || "unknown";
const setCapability = (env, id, value) => kvPut(env, `groq:cap:${id}`, value, 86400);

async function groqChat(env, { model, messages, jsonMode = true, temperature = 0.2, maxTokens = 1400 }) {
  if (!env.GROQ_API_KEY) throw new HttpError(500, "groq_key_missing");
  const body = { model, messages, temperature, max_completion_tokens: maxTokens };
  if (jsonMode) body.response_format = { type: "json_object" };
  // Qwen 3.8 runs in thinking mode by default; ops planning does not need the tokens.
  if (env.GROQ_REASONING_EFFORT) body.reasoning_effort = env.GROQ_REASONING_EFFORT;

  const send = (payload) =>
    withTimeout(
      (signal) =>
        fetch("https://api.groq.com/openai/v1/chat/completions", {
          method: "POST",
          signal,
          headers: {
            Authorization: `Bearer ${env.GROQ_API_KEY}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        }),
      GROQ_TIMEOUT_MS,
      "groq",
    );

  let res = await send(body);
  if (res.status === 400 && body.reasoning_effort) {
    const peek = await res.clone().text();
    if (/reasoning_effort|unsupported parameter/i.test(peek)) {
      // this model does not take the parameter — retry once without it
      delete body.reasoning_effort;
      res = await send(body);
    }
  }

  const text = await res.text();
  if (!res.ok) {
    const err = new HttpError(502, "groq_failed", {
      model,
      status: res.status,
      detail: text.slice(0, 600),
    });
    err.upstream = { status: res.status, body: text };
    throw err;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new HttpError(502, "groq_bad_response", { model });
  }
  const content = parsed?.choices?.[0]?.message?.content;
  if (!content) throw new HttpError(502, "groq_empty", { model });
  return { content, model: parsed.model || model, usage: parsed.usage || null };
}

function buildMessages(spec, manifest, prompt, images) {
  const manifestBlock = JSON.stringify(
    {
      asset_id: manifest.asset_id,
      meshes: manifest.meshes || [],
      materials: manifest.materials || [],
      bones: manifest.bones || [],
      shape_keys: manifest.shape_keys || [],
      hair: manifest.hair || {},
      skinning: manifest.skinning !== false,
    },
    null,
    0,
  );
  const system = `${spec}\n\nMANIFEST\n${manifestBlock}`;
  const userContent = [
    {
      type: "text",
      text:
        `asset_id: ${manifest.asset_id}\n` +
        `user prompt: ${prompt || "(none — match the reference image)"}\n` +
        `Return ONLY the JSON object described by the contract.`,
    },
    ...images.map((url) => ({ type: "image_url", image_url: { url } })),
  ];
  return [
    { role: "system", content: system },
    { role: "user", content: userContent },
  ];
}

// --------------------------------------------------------------------------- //
// ops validation (mirror of worker.py)
// --------------------------------------------------------------------------- //

function cleanOp(raw, manifest, warnings) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    warnings.push("dropped: op is not an object");
    return null;
  }
  const name = String(raw.op || "").toLowerCase();
  if (!OPS.has(name)) {
    warnings.push(`dropped: unknown op "${raw.op}"`);
    return null;
  }
  const mats = (manifest.materials || []).filter(Boolean);
  const meshes = (manifest.meshes || []).filter(Boolean);
  const bones = (manifest.bones || []).filter(Boolean);
  const caseIn = (list, value) => list.some((x) => String(x).toLowerCase() === String(value).toLowerCase());

  if (name === "set_scale") {
    const axis = String(raw.axis || "all").toLowerCase();
    const factor = clampNum(raw.factor, FACTOR_MIN, FACTOR_MAX);
    if (factor === null) {
      warnings.push("dropped set_scale: bad factor");
      return null;
    }
    if (!AXES.has(axis)) {
      warnings.push("dropped set_scale: bad axis");
      return null;
    }
    const target = String(raw.target || "Body");
    if (meshes.length && target.toLowerCase() !== "body" && !caseIn(meshes, target)) {
      warnings.push(`dropped set_scale: mesh "${target}" is not in the manifest`);
      return null;
    }
    return { op: name, target, axis, factor: round4(factor) };
  }

  if (name === "proportion") {
    const part = String(raw.part || "").toLowerCase();
    if (!PARTS.has(part)) {
      warnings.push(`dropped proportion: bad part "${raw.part}"`);
      return null;
    }
    const width = clampNum(raw.width ?? 1, FACTOR_MIN, FACTOR_MAX);
    const height = clampNum(raw.height ?? raw.width ?? 1, FACTOR_MIN, FACTOR_MAX);
    const depth = clampNum(raw.depth ?? raw.width ?? 1, FACTOR_MIN, FACTOR_MAX);
    if (width === null || height === null || depth === null) {
      warnings.push("dropped proportion: bad numbers");
      return null;
    }
    return { op: name, part, width: round4(width), height: round4(height), depth: round4(depth) };
  }

  if (name === "bone_resize") {
    const prefix = String(raw.bone_prefix || raw.bone || "").trim();
    const factor = clampNum(raw.factor, FACTOR_MIN, FACTOR_MAX);
    if (!prefix || factor === null) {
      warnings.push("dropped bone_resize: missing bone_prefix/factor");
      return null;
    }
    if (bones.length && !bones.some((b) => String(b).toLowerCase().startsWith(prefix.toLowerCase()))) {
      warnings.push(`dropped bone_resize: no bone starts with "${prefix}"`);
      return null;
    }
    return { op: name, bone_prefix: prefix, factor: round4(factor) };
  }

  if (name === "material_param") {
    const mat = String(raw.mat || raw.material || "").trim();
    const param = PARAM_ALIASES[String(raw.param || "").trim().toLowerCase()];
    if (!mat || !param) {
      warnings.push("dropped material_param: missing mat/param");
      return null;
    }
    if (mats.length && !caseIn(mats, mat)) {
      warnings.push(`dropped material_param: "${mat}" is not in the manifest`);
      return null;
    }
    if (COLOR_PARAMS.has(param)) {
      const color = normalizeColor(raw.value);
      if (!color) {
        warnings.push(`dropped material_param: bad colour for ${mat}.${param}`);
        return null;
      }
      return { op: name, mat, param, value: color };
    }
    const [lo, hi] = PARAM_RANGES[param] || [0, 1];
    const num = clampNum(raw.value, lo, hi);
    if (num === null) {
      warnings.push(`dropped material_param: bad number for ${mat}.${param}`);
      return null;
    }
    return { op: name, mat, param, value: round4(num) };
  }

  if (name === "add_mesh") {
    const primitive = String(raw.primitive || "cube").toLowerCase();
    if (!PRIMITIVES.has(primitive)) {
      warnings.push(`dropped add_mesh: bad primitive "${raw.primitive}"`);
      return null;
    }
    const rawName = String(raw.name || `Add_${primitive}`).replace(/[^A-Za-z0-9_]/g, "_").slice(0, 40);
    const meshName = /^[A-Za-z]/.test(rawName) ? rawName : `M_${rawName}`;
    const locIn = Array.isArray(raw.loc) ? raw.loc : [0, 0, 1];
    const scaleIn = Array.isArray(raw.scale) ? raw.scale : [0.1, 0.1, 0.1];
    const loc = [0, 1, 2].map((i) => clampNum(locIn[i], -LOC_ABS_MAX, LOC_ABS_MAX));
    const scale = [0, 1, 2].map((i) => clampNum(scaleIn[i], SCALE_MIN, SCALE_MAX));
    if (loc.includes(null) || scale.includes(null)) {
      warnings.push("dropped add_mesh: bad loc/scale");
      return null;
    }
    const op = {
      op: name,
      primitive,
      name: meshName,
      loc: loc.map(round4),
      scale: scale.map(round4),
    };
    if (raw.rot && Array.isArray(raw.rot)) {
      const rot = [0, 1, 2].map((i) => clampNum(raw.rot[i], -180, 180));
      if (!rot.includes(null)) op.rot = rot.map(round4);
    }
    const attach = String(raw.attach_bone || "auto").trim();
    if (attach && attach.toLowerCase() !== "auto") {
      if (/^(none|off|no|false)$/i.test(attach)) {
        op.attach_bone = "none";
      } else if (bones.length && !bones.some((b) => b.toLowerCase() === attach.toLowerCase())) {
        warnings.push(`add_mesh ${meshName}: bone "${attach}" not in manifest — using the nearest bone`);
        op.attach_bone = "auto";
      } else {
        op.attach_bone = attach;
      }
    }
    if (raw.mat) {
      const mat = String(raw.mat);
      if (mats.length && !caseIn(mats, mat)) warnings.push(`add_mesh ${meshName}: material "${mat}" ignored (not in manifest)`);
      else op.mat = mat;
    }
    return op;
  }

  // hair_swap
  const style = String(raw.style || "as_is").toLowerCase();
  if (!HAIR_STYLES.has(style)) {
    warnings.push(`dropped hair_swap: bad style "${raw.style}"`);
    return null;
  }
  const color = normalizeColor(raw.color) || "#2b1d16";
  return { op: name, style, color };
}

function sanitize(unsigned, manifest, assetId, warnings) {
  if (!unsigned || typeof unsigned !== "object") return { ops: [], notes: "" };
  if (unsigned.asset_id && String(unsigned.asset_id) !== String(assetId)) {
    warnings.push(`model returned asset_id "${unsigned.asset_id}", forced to "${assetId}"`);
  }
  let list = unsigned.ops;
  if (!Array.isArray(list)) {
    warnings.push("ops field is not an array");
    list = [];
  }
  if (list.length > MAX_OPS * 4) {
    warnings.push(`model returned ${list.length} raw entries — only the first ${MAX_OPS * 4} are inspected`);
    list = list.slice(0, MAX_OPS * 4);
  }
  const ops = [];
  for (const raw of list) {
    const op = cleanOp(raw, manifest, warnings);
    if (!op) continue;
    const dup = ops.findIndex((o) => JSON.stringify(o) === JSON.stringify(op));
    if (dup >= 0) {
      warnings.push(`dropped duplicate ${op.op}`);
      continue;
    }
    ops.push(op);
  }
  // cap AFTER filtering so a sloppy model cannot burn the budget on junk
  if (ops.length > MAX_OPS) {
    warnings.push(`${ops.length} valid ops — truncated to ${MAX_OPS}`);
    ops.length = MAX_OPS;
  }
  return { ops, notes: String(unsigned.notes || "").slice(0, 200) };
}

// --------------------------------------------------------------------------- //
// pipeline
// --------------------------------------------------------------------------- //

async function generateOps(env, spec, manifest, prompt, images, warnings) {
  const messages = buildMessages(spec, manifest, prompt, images);
  const declared = visionModels(env);
  const available = await groqModelIds(env, false);

  // never let a model that is not on this account, or one that rejected image_url, be primary
  const chain = [];
  let knownBad = 0;
  for (const id of declared) {
    if (available && !available.includes(id)) {
      warnings.push(`vision model ${id} is not available on this account — skipped`);
      knownBad += 1;
      continue;
    }
    const cap = await capability(env, id);
    if (cap === "text") {
      warnings.push(`vision model ${id} rejected images earlier — skipped (text-only)`);
      knownBad += 1;
      continue;
    }
    if (cap === "missing") {
      warnings.push(`vision model ${id} returned 404 earlier — skipped`);
      knownBad += 1;
      continue;
    }
    chain.push(id);
  }
  if (!chain.length && knownBad >= declared.length && available !== null) {
    // every configured model is known-dead: fail loudly instead of re-probing a text-only model
    throw new HttpError(502, "no_vision_model", {
      configured: declared,
      detail: "none of VISION_MODELS accepts images on this account — check /models?probe=1",
      warnings: [...new Set(warnings)],
    });
  }
  if (!chain.length) {
    // availability unknown (Groq /models unreachable): try the first id so the error is actionable
    chain.push(declared[0] || "qwen/qwen3.8-27b");
    warnings.push("no verified vision model — falling back to the first configured id");
  }

  const attempts = [];
  for (const model of chain) {
    attempts.push({ model, jsonMode: true });
    attempts.push({ model, jsonMode: false });
  }

  let lastError = null;
  for (const attempt of attempts) {
    try {
      const { content, model } = await groqChat(env, { ...attempt, messages });
      const parsed = tryParseJson(content);
      if (parsed) return { parsed, model, raw: content };
      warnings.push(`${model}: response was not valid JSON`);
      lastError = new HttpError(502, "invalid_json", { model, sample: String(content).slice(0, 300) });
    } catch (err) {
      lastError = err;
      warnings.push(`${attempt.model}: ${err.code || err.message}`);
      if (err.status === 500) throw err; // missing key etc. — retrying cannot help
      const upstream = err.upstream || {};
      const detail = String(upstream.body || err.extra?.detail || "");
      if (upstream.status === 404) {
        await setCapability(env, attempt.model, "missing");
      } else if (upstream.status === 400 && /image|multi-?modal|content.?type|vision/i.test(detail)) {
        // documented as multimodal, but this account/model cannot take image_url -> never primary again
        await setCapability(env, attempt.model, "text");
        warnings.push(`${attempt.model} does not accept image input — marked text-only for 24h`);
      }
    }
  }
  throw lastError || new HttpError(502, "groq_failed");
}

async function runValidator(env, spec, manifest, prompt, draft, warnings) {
  const model = env.VALIDATOR_MODEL || "openai/gpt-oss-120b";
  const messages = [
    {
      role: "system",
      content:
        `${spec}\n\nYou are a validator. The MANIFEST is:\n` +
        `${JSON.stringify({ meshes: manifest.meshes || [], materials: manifest.materials || [], bones: manifest.bones || [] })}\n` +
        `Remove any op that references a name not in the manifest, any op outside the allowed set, ` +
        `and any number outside its range. Return ONLY the corrected JSON object, same shape.`,
    },
    {
      role: "user",
      content: `user prompt: ${prompt}\n\ndraft ops:\n${JSON.stringify(draft)}`,
    },
  ];
  try {
    const { content } = await groqChat(env, { model, messages, jsonMode: true, temperature: 0, maxTokens: 900 });
    const parsed = tryParseJson(content);
    if (!parsed) {
      warnings.push("validator: non-JSON, keeping first-pass ops");
      return draft;
    }
    return parsed;
  } catch (err) {
    warnings.push(`validator: ${err.code || err.message} — keeping first-pass ops`);
    return draft;
  }
}

async function commitJob(env, jobId, payload) {
  const path = `jobs/${jobId}.json`;
  const body = JSON.stringify(payload, null, 2);
  const res = await gh(env, `/contents/${path}`, {
    method: "PUT",
    body: JSON.stringify({
      message: `job ${jobId} (${payload.asset_id})`,
      content: b64encode(body),
      branch: env.BASE_BRANCH || "main",
      committer: { name: "game-assets-worker", email: "worker@users.noreply.github.com" },
    }),
  });
  if (res.status === 201 || res.status === 200) return path;
  const text = (await res.text()).slice(0, 400);
  throw new HttpError(res.status === 422 ? 409 : 502, "job_commit_failed", { status: res.status, detail: text });
}

async function dispatchBuild(env, jobId, assetId) {
  const res = await gh(env, `/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      event_type: "build-asset",
      client_payload: { job_id: jobId, asset_id: assetId, requested_at: new Date().toISOString() },
    }),
  });
  if (res.status !== 204) {
    throw new HttpError(502, "dispatch_failed", { status: res.status, detail: (await res.text()).slice(0, 300) });
  }
}

function newJobId() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function assetsBranchInfo(env) {
  const branch = env.ASSETS_BRANCH || "assets";
  const cacheKey = `assets:head:${branch}`;
  if (env.RATE_KV) {
    try {
      const hit = await env.RATE_KV.get(cacheKey, "json");
      if (hit && hit.sha) return hit;
    } catch {
      /* ignore */
    }
  }
  const res = await gh(env, `/git/ref/heads/${branch}`);
  if (res.status === 404) return { sha: null, branch };
  if (!res.ok) throw new HttpError(502, "gh_ref_failed", { status: res.status });
  const body = await res.json();
  const info = { sha: body?.object?.sha || null, branch };
  if (env.RATE_KV && info.sha) {
    try {
      await env.RATE_KV.put(cacheKey, JSON.stringify(info), { expirationTtl: 20 });
    } catch {
      /* ignore */
    }
  }
  return info;
}

async function assetInfo(env, sha, fileName) {
  if (!sha) return { exists: false, size: 0 };
  const res = await gh(env, `/git/trees/${sha}`);
  if (!res.ok) return { exists: false, size: 0 };
  const body = await res.json();
  const entry = (body.tree || []).find((e) => e.path === fileName);
  return entry ? { exists: true, size: entry.size || 0 } : { exists: false, size: 0 };
}

const JSDELIVR_MAX_BYTES = 20 * 1024 * 1024;

function cdnUrl(env, ref, fileName) {
  const base = (env.CDN_BASE || "").replace(/\/+$/, "");
  return base ? `${base}@${ref}/${fileName}` : null;
}

// alternate CDN: pinning the commit avoids jsDelivr's branch cache entirely
function staticallyUrl(env, ref, fileName) {
  return env.REPO && ref ? `https://cdn.statically.io/gh/${env.REPO}/${ref}/${fileName}` : null;
}

function rawUrl(env, fileName) {
  return env.REPO
    ? `https://raw.githubusercontent.com/${env.REPO}/${env.ASSETS_BRANCH || "assets"}/${fileName}`
    : null;
}

// --------------------------------------------------------------------------- //
// handlers
// --------------------------------------------------------------------------- //

async function handleGenerate(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";

  const limit = await rateLimit(env, ip);
  if (!limit.ok) {
    return json(
      { error: limit.code, detail: `rate limit ${limit.detail}`, retry_after: limit.retryAfter },
      429,
      { "Retry-After": String(limit.retryAfter || 3600) },
    );
  }

  const length = Number(request.headers.get("Content-Length") || 0);
  if (length > MAX_BODY_BYTES) throw new HttpError(413, "image_too_large", { limit: MAX_BODY_BYTES });

  let body;
  try {
    body = await request.json();
  } catch {
    throw new HttpError(400, "bad_json");
  }

  const assetId = String(body.asset_id || "").trim();
  const allowed = (env.ALLOWED_ASSETS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!assetId) throw new HttpError(400, "asset_id_required", { allowed });
  if (!allowed.includes(assetId)) throw new HttpError(400, "asset_not_allowed", { allowed, asset_id: assetId });

  const prompt = String(body.prompt || "").slice(0, 800);
  const rawImages = [];
  if (body.image_b64) rawImages.push(body.image_b64);
  if (Array.isArray(body.images)) rawImages.push(...body.images);
  const images = rawImages.slice(0, MAX_IMAGES).map((img) => {
    const value = String(img || "");
    if (value.length > MAX_IMAGE_CHARS) throw new HttpError(413, "image_too_large", { limit: MAX_IMAGE_CHARS });
    if (value.startsWith("data:")) return value;
    return `data:image/jpeg;base64,${value}`;
  });
  if (!images.length && !prompt) throw new HttpError(400, "nothing_to_do", { hint: "send image_b64 and/or prompt" });

  const warnings = [];
  if (limit.degraded) warnings.push("rate limiting degraded (KV unavailable)");

  const [spec, manifest] = await Promise.all([loadSpec(env), loadManifest(env, assetId)]);

  const first = await generateOps(env, spec, manifest, prompt, images, warnings);

  let draft = first.parsed;
  if (String(env.ENABLE_VALIDATOR).toLowerCase() === "true") {
    draft = await runValidator(env, spec, manifest, prompt, draft, warnings);
  }

  const { ops, notes } = sanitize(draft, { ...manifest, asset_id: assetId }, assetId, warnings);
  const uniqueWarnings = [...new Set(warnings)];
  if (!ops.length) {
    return json(
      {
        error: "no_valid_ops",
        detail: "the model produced nothing usable — try a clearer prompt",
        warnings: uniqueWarnings,
        model: first.model,
      },
      422,
    );
  }

  const payloadBase = {
    asset_id: assetId,
    prompt,
    notes,
    ops,
    model: first.model,
    created_at: new Date().toISOString(),
    source: "worker",
    reference: images.length
      ? { type: "user-provided", count: images.length, note: "user-provided — user is responsible for image rights" }
      : { type: "none" },
  };

  let jobId = null;
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    jobId = newJobId();
    try {
      await commitJob(env, jobId, { job_id: jobId, ...payloadBase });
      lastErr = null;
      break;
    } catch (err) {
      lastErr = err;
      jobId = null;
    }
  }
  if (!jobId) throw lastErr || new HttpError(502, "job_commit_failed");

  await dispatchBuild(env, jobId, assetId);

  const origin = new URL(request.url).origin;
  return json(
    {
      job_id: jobId,
      asset_id: assetId,
      ops,
      notes,
      warnings: uniqueWarnings,
      model: first.model,
      usage: first.usage,
      status_url: `${origin}/status/${jobId}`,
      poll_after_ms: 5000,
      eta_seconds: 180,
    },
    200,
  );
}

async function handleStatus(env, jobId) {
  if (!/^[a-f0-9]{8,32}$/.test(jobId)) throw new HttpError(400, "bad_job_id");
  const fileName = `out_${jobId}.glb`;

  // a finished job is immutable — serve it from KV and save GitHub API quota
  if (env.RATE_KV) {
    try {
      const cached = await env.RATE_KV.get(`done:${jobId}`, "json");
      if (cached && cached.ready) return json(cached, 200);
    } catch {
      /* ignore */
    }
  }

  const { sha, branch } = await assetsBranchInfo(env);
  const info = await assetInfo(env, sha, fileName);
  const ready = info.exists;
  const oversized = ready && info.size > JSDELIVR_MAX_BYTES;

  const responds = {
    ready,
    job_id: jobId,
    file: fileName,
    bytes: info.size,
    url: ready && !oversized ? cdnUrl(env, sha, fileName) : null,
    fallback_url: ready ? cdnUrl(env, branch, fileName) : null,
    raw_url: ready ? rawUrl(env, fileName) : null,
    alt_cdn_url: ready ? staticallyUrl(env, sha, fileName) : null,
    commit: sha,
    retry_after_ms: ready ? 0 : 5000,
  };

  if (!ready) {
    responds.hint = "build usually takes 1-4 minutes; poll with backoff";
  } else {
    if (oversized) {
      responds.hint =
        `this GLB is ${(info.size / 1048576).toFixed(1)} MB — above jsDelivr's 20 MB limit, ` +
        "use alt_cdn_url or move outputs to R2";
      responds.cdn_limited = true;
    }
    await kvPut(env, `done:${jobId}`, JSON.stringify(responds), 86400);
  }
  return json(responds, 200);
}

async function handleModels(env, url) {
  const probe = url.searchParams.get("probe") === "1";
  const configured = visionModels(env);
  const listed = await groqModelIds(env, probe);
  const results = [];

  for (const id of configured) {
    const cap = await capability(env, id);
    const entry = { id, listed: listed ? listed.includes(id) : null, cached_capability: cap };
    if (probe && (!listed || listed.includes(id))) {
      const started = Date.now();
      try {
        const { content } = await groqChat(env, {
          model: id,
          messages: [
            { role: "user", content: [
              { type: "text", text: "Reply with the single word ok." },
              { type: "image_url", image_url: { url: PROBE_PNG } },
            ] },
          ],
          jsonMode: false,
          temperature: 0,
          maxTokens: 8,
        });
        entry.image_probe = "ok";
        entry.sample = String(content).slice(0, 40);
        await setCapability(env, id, "vision");
      } catch (err) {
        entry.image_probe = "failed";
        entry.error = err.code || err.message;
        entry.upstream_status = err.upstream?.status ?? null;
        const body = String(err.upstream?.body || "");
        if (err.upstream?.status === 404) await setCapability(env, id, "missing");
        else if (err.upstream?.status === 400 && /image|multi-?modal|content.?type|vision/i.test(body))
          await setCapability(env, id, "text");
        else if (/invalid_json|groq_empty/.test(entry.error)) {
          // a 200 with unusable text still proves the image was accepted
          entry.image_probe = "ok";
          await setCapability(env, id, "vision");
        }
      }
      entry.latency_ms = Date.now() - started;
    }
    results.push(entry);
  }

  return json({
    configured,
    probe_ran: probe,
    available_count: listed ? listed.length : null,
    available: listed ? listed.filter((id) => /qwen|llama-4|vision|gpt-oss|muse/.test(id)) : null,
    models: results,
    note: "run /models?probe=1 after deploying to prove image_url works for each configured model",
  });
}

async function handleAssets(env) {
  const allowed = (env.ALLOWED_ASSETS || "").split(",").map((s) => s.trim()).filter(Boolean);
  return json({ assets: allowed, cdn_base: env.CDN_BASE || null });
}

async function handleHealth(env, url) {
  const configured = visionModels(env);
  const listed = url && url.searchParams.get("models") === "1" ? await groqModelIds(env, true) : await groqModelIds(env, false);
  const caps = {};
  for (const id of configured) caps[id] = await capability(env, id);

  return json({
    ok: true,
    assets: (env.ALLOWED_ASSETS || "").split(",").map((s) => s.trim()).filter(Boolean),
    models: {
      vision_chain: configured,
      available_on_account: listed ? configured.filter((id) => listed.includes(id)) : null,
      missing_from_account: listed ? configured.filter((id) => !listed.includes(id)) : null,
      capabilities: caps,
      validator: env.VALIDATOR_MODEL || "openai/gpt-oss-120b",
      validator_enabled: String(env.ENABLE_VALIDATOR).toLowerCase() === "true",
      reasoning_effort: env.GROQ_REASONING_EFFORT || null,
      checked_live: Boolean(url && url.searchParams.get("models") === "1"),
      probe_url: "/models?probe=1",
    },
    repo: env.REPO || null,
    assets_branch: env.ASSETS_BRANCH || "assets",
    kv: Boolean(env.RATE_KV),
    time: new Date().toISOString(),
  });
}

// --------------------------------------------------------------------------- //
// entry
// --------------------------------------------------------------------------- //

export default {
  async fetch(request, env, ctx) {
    const cors = corsHeaders(env, request);
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    const respond = (res) => {
      const headers = new Headers(res.headers);
      for (const [k, v] of Object.entries(cors)) headers.set(k, v);
      return new Response(res.body, { status: res.status, headers });
    };

    try {
      if (url.pathname === "/health" || url.pathname === "/") return respond(await handleHealth(env, url));
      if (url.pathname === "/assets") return respond(await handleAssets(env));
      if (url.pathname === "/models") return respond(await handleModels(env, url));

      if (url.pathname === "/generate") {
        if (request.method !== "POST") throw new HttpError(405, "method_not_allowed");
        return respond(await handleGenerate(request, env));
      }

      const statusMatch = url.pathname.match(/^\/status\/([A-Za-z0-9_-]{4,40})$/);
      if (statusMatch) {
        if (request.method !== "GET" && request.method !== "HEAD") {
          throw new HttpError(405, "method_not_allowed");
        }
        return respond(await handleStatus(env, statusMatch[1].toLowerCase()));
      }

      throw new HttpError(404, "not_found", { path: url.pathname });
    } catch (err) {
      if (err instanceof HttpError) {
        return respond(json({ error: err.code, ...err.extra }, err.status));
      }
      // never leak internals
      console.log("unhandled", err && err.stack ? err.stack : String(err));
      return respond(json({ error: "internal_error" }, 500));
    }
  },
};
