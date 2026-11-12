/**
 * Atelier · Garment Studio
 *
 * Frontend for the headless Blender garment pipeline. The app is driven by
 * `manifest.json`, which `.github/workflows/blender-build.yml` regenerates from
 * whatever the pipeline actually produced. Nothing in here hard-codes an outfit
 * or an asset path: add a build to `outputs/`, the manifest lists it, the studio
 * shows it.
 *
 * Public hooks used by tools/browser_test.mjs and the unit tests:
 *   window.atelier.state        - current selection / viewer state
 *   window.atelier.ready        - resolves once the manifest has been applied
 *   window.atelier.selectVariant(id) / selectOutfit(id) / resetCamera()
 */

const MANIFEST_FALLBACK = "manifest.json";
// Camera presets are angles only: the radius comes from model-viewer's own
// framing (plus a margin) so a tall parka and a cropped jacket both fit.
const CAMERA_PRESETS = {
  hero: { theta: -28, phi: 78 },
  front: { theta: 0, phi: 82 },
  side: { theta: 90, phi: 80 },
  back: { theta: 180, phi: 82 },
  top: { theta: -20, phi: 24 },
};
const FRAME_MARGIN = 1.22;
const CAMERA_FOV = "30deg";
const STORAGE_KEY = "atelier.selection.v1";

const state = {
  manifest: null,
  manifestUrl: MANIFEST_FALLBACK,
  manifestError: null,
  characters: [],
  outfits: [],
  variant: null,
  outfitId: null,
  entry: null,
  autorotate: false,
  animations: [],
  animationName: null,
  playing: false,
  downloading: null,
  fullscreen: false,
  radius: 0,
  progress: 0,
  viewer: "boot", // boot | loading | ready | error | empty
  error: null,
  panel: "outfits",
};

const listeners = new Set();
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function onStateChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) {
    try {
      fn(state);
    } catch (error) {
      console.error("[atelier] state listener failed", error);
    }
  }
}

function setState(patch) {
  Object.assign(state, patch);
  emit();
}

/* -------------------------------------------------------------------------- */
/* small helpers                                                               */
/* -------------------------------------------------------------------------- */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const ICONS = {
  download:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10m0 0 3.6-3.6M12 14l-3.6-3.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M5 18h14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  cube:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 20 7.5v9L12 21 4 16.5v-9z" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M12 12 20 7.5M12 12 4 7.5M12 12v9" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
  garment:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3.5 12 5l3-1.5 4 2.5-1.6 3L20 11l-2 2.5-2-1.2V21H8v-8.7l-2 1.2L4 11l2.6-2L5 6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
};

function humanBytes(bytes) {
  if (typeof bytes !== "number" || Number.isNaN(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return index === 0 ? `${value} B` : `${value.toFixed(1)} ${units[index]}`;
}

function formatNumber(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US")
    : "—";
}

function formatTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

function titleCase(text) {
  return String(text || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/* -------------------------------------------------------------------------- */
/* manifest                                                                    */
/* -------------------------------------------------------------------------- */
function resolveManifestUrl() {
  const params = new URLSearchParams(window.location.search);
  const override = params.get("manifest");
  if (override) return override;
  return MANIFEST_FALLBACK;
}

async function fetchManifest(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`${url} → HTTP ${response.status} ${response.statusText || ""}`.trim());
  }
  const manifest = await response.json();
  if (!manifest || !Array.isArray(manifest.outfits)) {
    throw new Error(`${url} is not an Atelier manifest (missing "outfits")`);
  }
  return manifest;
}

function candidateFor(variantId) {
  const outfit = state.outfits.find((item) => item.id === state.outfitId);
  return outfit?.variants?.[variantId] ?? null;
}

function outfitById(id) {
  return state.outfits.find((outfit) => outfit.id === id) ?? null;
}

function firstOutfitFor(variantId) {
  return state.outfits.find((outfit) => outfit.variants?.[variantId]) ?? null;
}

function variantLabel(id) {
  const character = state.characters.find((item) => item.id === id);
  return character?.label ?? titleCase(id);
}

/** Assets the browser can actually load right now, in preference order. */
function buildableOutfits(variantId) {
  return state.outfits.filter((outfit) => {
    const entry = outfit.variants?.[variantId];
    return Boolean(entry && (entry.glb?.path || entry.fbx?.path));
  });
}

/* -------------------------------------------------------------------------- */
/* rendering: characters                                                       */
/* -------------------------------------------------------------------------- */
function renderCharacters() {
  const host = $('[data-role="characters"]');
  if (!host) return;
  host.replaceChildren();

  if (!state.characters.length) {
    host.dataset.state = "loading";
    host.append(
      el("span", { class: "segmented__skeleton" }),
      el("span", { class: "segmented__skeleton" }),
    );
    return;
  }

  host.dataset.state = "ready";
  for (const character of state.characters) {
    const available = Boolean(firstOutfitFor(character.id));
    const button = el("button", {
      class: "segmented__button",
      type: "button",
      "data-variant": character.id,
      "aria-pressed": String(character.id === state.variant),
      "aria-label": `${character.label} character`,
      disabled: !available,
      title: available ? `${character.label} builds` : `No published builds for ${character.label}`,
    });
    button.textContent = character.label;
    button.addEventListener("click", () => selectVariant(character.id));
    host.append(button);
  }
}

/* -------------------------------------------------------------------------- */
/* rendering: outfit library                                                   */
/* -------------------------------------------------------------------------- */
function compatibilityChips(outfit) {
  const chips = [];
  for (const character of state.characters) {
    const has = Boolean(outfit.variants?.[character.id]?.glb?.path);
    chips.push(
      el("span", {
        class: `chip ${has ? "chip--on" : "chip--off"}`,
        title: has ? `${character.label} build published` : `No ${character.label} build`,
        text: character.label.slice(0, 1),
      }),
    );
  }
  return chips;
}

function validationTone(status) {
  if (status === "pass") return "ok";
  if (status === "fail" || status === "error") return "bad";
  return "warn";
}

function outfitCard(outfit) {
  const entry = outfit.variants?.[state.variant] ?? null;
  const other = Object.keys(outfit.variants || {})[0];
  const preview = entry?.preview?.path ?? outfit.variants?.[other]?.preview?.path ?? null;
  const status = entry?.validation?.status ?? outfit.variants?.[other]?.validation?.status;
  const current = outfit.id === state.outfitId;

  const thumb = el("div", { class: "outfit-card__thumb" });
  if (preview) {
    const img = el("img", {
      src: preview,
      alt: `${outfit.name} preview`,
      loading: "lazy",
      decoding: "async",
      width: "320",
      height: "400",
    });
    img.addEventListener("error", () => {
      img.remove();
      thumb.append(
        el("div", { class: "thumb-fallback", html: `${ICONS.garment}<span>no preview</span>` }),
      );
    });
    thumb.append(img);
  } else {
    thumb.append(el("div", { class: "thumb-fallback", html: `${ICONS.garment}<span>no preview</span>` }));
  }
  thumb.append(
    el("span", {
      class: "outfit-card__state",
      "data-tone": validationTone(status),
      text: entry ? (status === "pass" ? "validated" : status || "unvalidated") : "other character",
    }),
  );

  const card = el(
    "button",
    {
      class: "outfit-card",
      type: "button",
      role: "listitem",
      "aria-current": current ? "true" : "false",
      "data-outfit": outfit.id,
      title: outfit.variants?.[state.variant]
        ? `Load ${outfit.name}`
        : `${outfit.name} is published for ${(outfit.compatibility || []).map(variantLabel).join(", ") || "no character"}`,
    },
    [
      thumb,
      el("p", { class: "outfit-card__name", text: outfit.name }),
      el(
        "p",
        { class: "outfit-card__meta" },
        [
          el("span", { class: "chip", text: outfit.kind ? titleCase(outfit.kind) : "garment" }),
          ...compatibilityChips(outfit),
        ],
      ),
    ],
  );
  card.addEventListener("click", () => selectOutfit(outfit.id));
  return card;
}

function renderOutfits() {
  const grids = $$('[data-role="outfit-grid"]');
  const count = $$('[data-role="outfit-count"]');
  for (const node of count) node.textContent = String(state.outfits.length || "—");

  if (!state.outfits.length) {
    for (const grid of grids) {
      grid.replaceChildren(
        el("p", {
          class: "empty-note",
          text: "No outfits are published yet. Run the pipeline and re-deploy.",
        }),
      );
    }
    return;
  }

  const sorted = [...state.outfits].sort((a, b) => {
    const aHas = a.variants?.[state.variant] ? 0 : 1;
    const bHas = b.variants?.[state.variant] ? 0 : 1;
    return aHas - bHas || a.name.localeCompare(b.name);
  });

  for (const grid of grids) {
    grid.replaceChildren(...sorted.map(outfitCard));
  }
}

/* -------------------------------------------------------------------------- */
/* rendering: asset + build panels                                             */
/* -------------------------------------------------------------------------- */
function downloadRow({ label, path, bytes, format, disabledReason }) {
  const disabled = Boolean(disabledReason);
  const row = el(
    "a",
    {
      class: `download${disabled ? " download--ghost" : ""}`,
      href: disabled ? undefined : path,
      download: path ? path.split("/").pop() : undefined,
      rel: "noopener",
      "aria-disabled": disabled ? "true" : "false",
      "data-format": format,
      title: disabled
        ? disabledReason
        : `Download ${label} (${humanBytes(bytes)})`,
    },
    [
      el("span", { class: "download__label", html: `${ICONS.download}<span>${label}</span>` }),
      el("span", { class: "download__size", text: disabled ? disabledReason : humanBytes(bytes) }),
    ],
  );
  if (disabled) row.setAttribute("href", "#");
  return row;
}

function metaRow(label, value) {
  return el("div", { class: "meta-row" }, [
    el("dt", { text: label }),
    el("dd", { text: value }),
  ]);
}

function buildStages(entry) {
  const stages = entry?.validation?.stages ?? [];
  if (!stages.length) {
    return el("p", { class: "empty-note", text: "No validation report published for this build." });
  }
  return el(
    "ul",
    { class: "stage-list" },
    stages.map((stage) =>
      el("li", { class: "stage-item", "data-status": stage.status }, [
        el("span", { class: "stage-item__dot" }),
        el("span", { class: "stage-item__name", text: stage.stage }),
        el("span", { class: "stage-item__status", text: stage.status }),
      ]),
    ),
  );
}

function assetPanel() {
  const entry = state.entry;
  const outfit = outfitById(state.outfitId);
  const panel = el("section", { class: "panel" });
  const availability = state.downloading;

  panel.append(
    el("div", { class: "panel__head" }, [
      el("div", {}, [
        el("h3", { class: "panel__title", text: outfit?.name ?? "No outfit selected" }),
        el(
          "p",
          {
            class: "panel__sub",
            text: entry ? `${variantLabel(entry.variant)} build · ${entry.outfit}` : "—",
          },
        ),
      ]),
      el("span", {
        class: `pill`,
        "data-tone": entry ? validationTone(entry.validation?.status) : "muted",
        text: entry ? (entry.validation?.status === "pass" ? "validated" : entry.validation?.status || "unvalidated") : "none",
      }),
    ]),
  );

  const glb = entry?.glb ?? null;
  const fbx = entry?.fbx ?? null;
  const downloads = el("div", { class: "downloads" });
  downloads.append(
    downloadRow({
      label: "glTF binary",
      path: glb?.path,
      bytes: glb?.bytes,
      format: "glb",
      disabledReason: glb ? availability?.glb : "not built",
    }),
    downloadRow({
      label: "Autodesk FBX",
      path: fbx?.path,
      bytes: fbx?.bytes,
      format: "fbx",
      disabledReason: fbx ? availability?.fbx : "not built",
    }),
  );
  panel.append(downloads);

  const stats = entry?.stats ?? {};
  panel.append(
    el("dl", { class: "meta-list", style: "margin-top:12px" }, [
      metaRow("Triangles", formatNumber(stats.triangles)),
      metaRow("Vertices", formatNumber(stats.vertices)),
      metaRow("Materials", formatNumber(stats.materials)),
      metaRow("Skin joints", formatNumber(stats.joints)),
      metaRow("Animation clips", formatNumber(stats.animations ?? 0)),
      metaRow("GLB size", humanBytes(glb?.bytes)),
      metaRow("FBX size", humanBytes(fbx?.bytes)),
      metaRow("Built", entry?.built_at ?? "—"),
    ]),
  );
  return panel;
}

function buildPanel() {
  const entry = state.entry;
  const panel = el("section", { class: "panel" });
  panel.append(
    el("div", { class: "panel__head" }, [
      el("div", {}, [
        el("h3", { class: "panel__title", text: "Build & validation" }),
        el("p", { class: "panel__sub", text: "Recorded by the headless pipeline" }),
      ]),
    ]),
    buildStages(entry),
    el("dl", { class: "meta-list", style: "margin-top:12px" }, [
      metaRow("Pipeline", entry?.validation?.pipeline_version ?? "—"),
      metaRow("Manifest", state.manifest?.generated_at ?? "—"),
      metaRow("Schema", String(state.manifest?.schema ?? "—")),
    ]),
  );
  return panel;
}

function renderAssetPanels() {
  const hosts = $$('[data-role="asset-panels"]');
  for (const host of hosts) {
    host.replaceChildren(assetPanel(), buildPanel());
  }
}

/* -------------------------------------------------------------------------- */
/* rendering: view panel (mobile)                                              */
/* -------------------------------------------------------------------------- */
function renderViewPanel() {
  const host = $('[data-role="view-panel"]');
  if (!host) return;
  host.replaceChildren();

  const presets = el("div", { class: "chip-row" });
  for (const [name, preset] of Object.entries(CAMERA_PRESETS)) {
    const button = el("button", {
      class: "button",
      type: "button",
      "data-camera": name,
      text: name === "hero" ? "3/4" : titleCase(name),
    });
    button.addEventListener("click", () => setCamera(preset, { toast: `Camera: ${titleCase(name)}` }));
    presets.append(button);
  }

  const rotate = el("button", {
    class: "button",
    type: "button",
    "aria-pressed": String(state.autorotate),
    text: state.autorotate ? "Auto-rotate on" : "Auto-rotate off",
  });
  rotate.addEventListener("click", () => toggleAutorotate());

  const animationRow = el("div", { class: "view-row" });
  if (state.animations.length) {
    const select = el("select", { class: "select", "aria-label": "Animation clip" });
    for (const clip of state.animations) {
      select.append(
        el("option", { value: clip, text: titleCase(clip), selected: clip === state.animationName }),
      );
    }
    select.addEventListener("change", () => {
      setState({ animationName: select.value, playing: true });
      const viewer = modelViewer();
      if (viewer) {
        viewer.animationName = select.value;
        viewer.play?.({ repetitions: Infinity });
      }
      renderViewPanel();
    });
    const playPause = el("button", {
      class: "button",
      type: "button",
      text: state.playing ? "Pause" : "Play",
    });
    playPause.addEventListener("click", () => togglePlayback());
    animationRow.append(
      el("span", { class: "view-row__label", text: "Animation" }),
      select,
      playPause,
    );
    animationRow.dataset.role = "animation-row";
  } else {
    animationRow.append(
      el("span", { class: "view-row__label", text: "Animation" }),
      el("p", {
        class: "empty-note",
        text: "This export has no animation clips — it ships as a static skinned mesh. Use auto-rotate for motion.",
      }),
    );
    animationRow.dataset.role = "animation-empty";
  }

  host.append(
    el("div", { class: "view-row" }, [
      el("span", { class: "view-row__label", text: "Camera" }),
      presets,
    ]),
    el("div", { class: "view-row" }, [
      el("span", { class: "view-row__label", text: "Motion" }),
      rotate,
    ]),
    animationRow,
  );
}

/* -------------------------------------------------------------------------- */
/* viewer plumbing                                                             */
/* -------------------------------------------------------------------------- */
function modelViewer() {
  return $('[data-role="model"]');
}

function setOverlay(name, { message = "", title = "" } = {}) {
  const loading = $('[data-role="overlay-loading"]');
  const error = $('[data-role="overlay-error"]');
  const empty = $('[data-role="overlay-empty"]');
  if (!loading || !error || !empty) return;
  loading.hidden = name !== "loading";
  error.hidden = name !== "error";
  empty.hidden = name !== "empty";
  if (name === "error") {
    $('[data-role="error-message"]').textContent = message;
  }
  if (name === "empty") {
    $('[data-role="empty-title"]').textContent = title || "No builds published yet";
    $('[data-role="empty-message"]').textContent = message;
  }
}

function setProgress(value) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  state.progress = percent;
  const bar = $('[data-role="progress-bar"]');
  const track = $('[data-role="progress"]');
  const hint = $('[data-role="loading-hint"]');
  if (bar) bar.style.width = `${percent}%`;
  if (track) track.setAttribute("aria-valuenow", String(percent));
  if (hint) hint.textContent = percent >= 100 ? "Finishing…" : `${percent}%`;
}

function orbitString({ theta, phi }, radius) {
  return `${theta}deg ${phi}deg ${radius.toFixed(2)}m`;
}

function setCamera(preset, { toast } = {}) {
  const viewer = modelViewer();
  if (!viewer) return;
  const radius = state.radius || viewer.getCameraOrbit?.()?.radius || 2.5;
  viewer.setAttribute("camera-orbit", orbitString(preset, radius));
  viewer.setAttribute("field-of-view", CAMERA_FOV);
  viewer.jumpCameraToGoal?.();
  if (toast) showToast(toast);
}

/** Zoom out from model-viewer's tight auto framing so sleeves are not cropped. */
function frameModel(viewer) {
  const orbit = viewer.getCameraOrbit?.();
  if (!orbit || !Number.isFinite(orbit.radius)) return;
  const radius = orbit.radius * FRAME_MARGIN;
  state.radius = radius;
  viewer.setAttribute("camera-orbit", orbitString(CAMERA_PRESETS.hero, radius));
  viewer.jumpCameraToGoal?.();
}

function resetCamera() {
  setCamera(CAMERA_PRESETS.hero, { toast: "Camera reset" });
}

function toggleAutorotate(force) {
  const next = typeof force === "boolean" ? force : !state.autorotate;
  setState({ autorotate: next });
  const viewer = modelViewer();
  if (viewer) viewer.autoRotate = next;
  const button = $('[data-action="autorotate"]');
  if (button) button.setAttribute("aria-pressed", String(next));
  renderViewPanel();
}

function togglePlayback() {
  const viewer = modelViewer();
  if (!viewer || !state.animations.length) return;
  const next = !state.playing;
  setState({ playing: next });
  if (next) viewer.play?.({ repetitions: Infinity });
  else viewer.pause?.();
  updateAnimationButton();
  renderViewPanel();
}

function updateAnimationButton() {
  const button = $('[data-action="animation"]');
  if (!button) return;
  button.hidden = state.animations.length === 0;
  button.setAttribute("aria-pressed", String(state.playing));
  const icon = $('[data-role="animation-icon"]', button);
  if (icon) {
    icon.setAttribute(
      "d",
      state.playing ? "M8 5.5h3.2v13H8zM12.8 5.5H16v13h-3.2z" : "M8 5.5v13l10-6.5z",
    );
  }
}

function showToast(message, timeout = 2200) {
  const toast = $('[data-role="toast"]');
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  toast.dataset.visible = "true";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.dataset.visible = "false";
    setTimeout(() => {
      toast.hidden = true;
    }, 220);
  }, timeout);
}

/* -------------------------------------------------------------------------- */
/* selection                                                                   */
/* -------------------------------------------------------------------------- */
function selectVariant(id, { silent = false } = {}) {
  const character = state.characters.find((item) => item.id === id);
  if (!character) return false;

  let outfit = outfitById(state.outfitId);
  let switchedOutfit = false;
  if (!outfit?.variants?.[id]) {
    const fallback = firstOutfitFor(id);
    if (!fallback) {
      setState({ variant: id, entry: null, viewer: "empty", error: null });
      renderCharacters();
      setOverlay("empty", {
        title: `No ${character.label} builds`,
        message: `The manifest lists no published export for ${character.label}. Build it with: python tools/run_headless.py --variant ${id} --stage full`,
      });
      showToast(`No ${character.label} builds published yet`);
      return false;
    }
    outfit = fallback;
    switchedOutfit = true;
  }

  setState({ variant: id, outfitId: outfit.id });
  renderCharacters();
  renderOutfits();
  loadSelected({ silent, announce: switchedOutfit ? outfit.name : null });
  syncUrl();
  persistSelection();
  return true;
}

function selectOutfit(id, { silent = false } = {}) {
  const outfit = outfitById(id);
  if (!outfit) return false;

  let variant = state.variant;
  let switchedCharacter = false;
  if (!outfit.variants?.[variant]) {
    variant = (outfit.compatibility || [])[0] ?? null;
    switchedCharacter = Boolean(variant);
    if (!variant) {
      showToast(`${outfit.name} has no published build`);
      return false;
    }
  }

  setState({ variant, outfitId: id });
  renderCharacters();
  renderOutfits();
  loadSelected({ silent });
  if (switchedCharacter) showToast(`${outfit.name} loads for ${variantLabel(variant)}`);
  syncUrl();
  persistSelection();
  return true;
}

function redirectIfUnavailable(entry) {
  // The manifest only lists files that existed at build time, but a stale CDN
  // or a partially uploaded deploy can still 404. Falling back keeps the studio
  // usable and tells the user exactly what happened.
  const glb = entry?.glb?.path;
  if (!glb) return;
  fetch(glb, { method: "HEAD", cache: "no-store" })
    .then((response) => {
      if (response.ok || state.entry !== entry) return;
      const message = `The manifest lists ${glb}, but the server answered HTTP ${response.status}. The deployment may still be uploading.`;
      setState({ viewer: "error", error: `HTTP ${response.status}` });
      setOverlay("error", { message: message });
      showToast(`Asset missing on the server (HTTP ${response.status})`);
    })
    .catch(() => {
      /* HEAD is a nicety; the viewer's own error handler is authoritative. */
    });
}

function loadSelected({ silent = false, announce = null } = {}) {
  const entry = candidateFor(state.variant);
  const viewer = modelViewer();
  const outfit = outfitById(state.outfitId);

  $('[data-role="hud-outfit"]').textContent = outfit?.name ?? "—";
  $('[data-role="hud-variant"]').textContent = state.variant
    ? `${variantLabel(state.variant)}${outfit?.kind ? ` · ${titleCase(outfit.kind)}` : ""}`
    : "—";

  setState({ entry, animations: [], playing: false, animationName: null, downloading: null });
  updateAnimationButton();
  renderAssetPanels();
  renderViewPanel();

  if (!entry?.glb?.path) {
    setState({ viewer: "empty" });
    setOverlay("empty", {
      title: "No GLB for this build",
      message: "Only an FBX was published — download it from the asset panel.",
    });
    return;
  }

  if (!viewer || !window.customElements?.get?.("model-viewer")) {
    setState({ viewer: "error" });
    setOverlay("error", {
      message:
        "The 3D engine is unavailable: vendor/model-viewer.min.js did not register <model-viewer>. Re-deploy the site or check the browser console.",
    });
    return;
  }

  // Re-selecting what is already on screen must not restart the load: setting
  // the same src attribute fires no "load" event, which used to leave the UI
  // stuck on the loading overlay.
  if (
    viewer &&
    state.viewer === "ready" &&
    state.entry === entry &&
    viewer.getAttribute("src") === entry.glb.path
  ) {
    setOverlay("none");
    return;
  }

  setState({ viewer: "loading", error: null });
  setProgress(0);
  setOverlay("loading");
  viewer.setAttribute("alt", `${outfit?.name ?? "Garment"} on the ${variantLabel(state.variant)} body`);
  viewer.setAttribute("src", entry.glb.path);
  viewer.autoRotate = state.autorotate;

  if (!silent) verifyDownloads(entry);
  if (announce) showToast(`Switched to ${announce}`);
  redirectIfUnavailable(entry);
}

/** HEAD-check both downloads so the panel never offers a dead link. */
async function verifyDownloads(entry) {
  setState({ downloading: {} });
  const result = { glb: null, fbx: null };
  await Promise.all(
    ["glb", "fbx"].map(async (format) => {
      const path = entry?.[format]?.path;
      if (!path) return;
      try {
        const response = await fetch(path, { method: "HEAD", cache: "no-store" });
        if (!response.ok) result[format] = `missing (HTTP ${response.status})`;
      } catch {
        result[format] = "offline";
      }
    }),
  );
  if (state.entry !== entry) return; // selection changed while we were checking
  setState({ downloading: result });
}

/* -------------------------------------------------------------------------- */
/* URL + storage                                                               */
/* -------------------------------------------------------------------------- */
function syncUrl() {
  const params = new URLSearchParams(window.location.search);
  if (state.variant) params.set("variant", state.variant);
  if (state.outfitId) params.set("outfit", state.outfitId);
  const query = params.toString();
  const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  try {
    window.history.replaceState({}, "", url);
  } catch {
    /* file:// previews can refuse history writes */
  }
}

function persistSelection() {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ variant: state.variant, outfit: state.outfitId }),
    );
  } catch {
    /* private mode */
  }
}

function restoreSelection() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------------------- */
/* panels + fullscreen                                                         */
/* -------------------------------------------------------------------------- */
const PANEL_TITLES = { outfits: "Outfits", view: "View", asset: "Asset" };

function setPanel(name) {
  if (!PANEL_TITLES[name]) return;
  setState({ panel: name });
  document.body.dataset.panel = name;
  for (const tab of $$(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.panelTarget === name));
  }
  for (const panel of $$('[data-panel-name]')) {
    panel.hidden = panel.dataset.panelName !== name;
  }
  const title = $('[data-role="sheet-title"]');
  if (title) title.textContent = PANEL_TITLES[name];
  const count = $('[data-role="sheet-count"]');
  if (count) count.textContent = name === "outfits" ? `${state.outfits.length} available` : "";
}

function fullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

async function toggleFullscreen() {
  const frame = $('[data-role="stage-frame"]');
  if (!frame) return;
  try {
    if (fullscreenElement()) {
      await (document.exitFullscreen?.() ?? Promise.resolve());
    } else if (frame.requestFullscreen) {
      await frame.requestFullscreen({ navigationUI: "hide" });
    } else if (frame.webkitRequestFullscreen) {
      frame.webkitRequestFullscreen();
    } else {
      frame.classList.toggle("stage__frame--pseudo-fullscreen");
    }
  } catch (error) {
    console.warn("[atelier] fullscreen request failed", error);
    frame.classList.toggle("stage__frame--pseudo-fullscreen");
  }
  syncFullscreenState();
}

function syncFullscreenState() {
  const frame = $('[data-role="stage-frame"]');
  const active = Boolean(fullscreenElement()) || frame?.classList.contains("stage__frame--pseudo-fullscreen");
  setState({ fullscreen: active });
  document.body.classList.toggle("is-fullscreen", active);
  const button = $('[data-action="fullscreen"]');
  if (button) button.setAttribute("aria-pressed", String(active));
}

/* -------------------------------------------------------------------------- */
/* viewer events                                                               */
/* -------------------------------------------------------------------------- */
function wireViewer() {
  const viewer = modelViewer();
  if (!viewer) return;

  viewer.addEventListener("progress", (event) => {
    const detail = event.detail || {};
    if (typeof detail.totalProgress === "number") setProgress(detail.totalProgress);
  });

  viewer.addEventListener("load", () => {
    const animations = viewer.availableAnimations || [];
    setState({ viewer: "ready", animations, playing: false });
    setState({ animationName: animations[0] ?? null, playing: false });
    setOverlay("none");
    setProgress(1);
    if (animations.length) {
      viewer.animationName = animations[0];
      viewer.autoplay = true;
      const firstClip = animations[0];
      setState({ animationName: firstClip, playing: true });
      viewer.play?.({ repetitions: Infinity });
    }
    frameModel(viewer);
    updateAnimationButton();
    renderViewPanel();
    renderAssetPanels();
    const hint = $('[data-role="model-hint"]');
    if (hint) {
      const touch = window.matchMedia?.("(pointer: coarse)")?.matches;
      const zoom = touch ? "pinch to zoom" : "scroll to zoom";
      hint.textContent = animations.length
        ? `Drag to orbit · ${zoom} · space to play/pause`
        : `Drag to orbit · ${zoom}`;
    }
  });

  viewer.addEventListener("error", (event) => {
    const detail = event.detail || {};
    const message =
      detail.sourceError?.message ||
      detail.message ||
      "The GLB could not be parsed or fetched.";
    setState({ viewer: "error", error: message });
    setOverlay("error", { message });
  });

  viewer.addEventListener("camera-change", () => {
    /* keep the HUD hint alive; no state churn needed */
  });
}

/* -------------------------------------------------------------------------- */
/* boot                                                                        */
/* -------------------------------------------------------------------------- */
function bindChrome() {
  for (const tab of $$(".tab")) {
    tab.addEventListener("click", () => setPanel(tab.dataset.panelTarget));
  }

  for (const button of $$('[data-action]')) {
    button.addEventListener("click", () => {
      switch (button.dataset.action) {
        case "reset-camera":
          resetCamera();
          break;
        case "autorotate":
          toggleAutorotate();
          break;
        case "animation":
          togglePlayback();
          break;
        case "fullscreen":
          toggleFullscreen();
          break;
        case "retry":
          loadSelected();
          break;
        case "reload":
          window.location.reload();
          break;
        case "next-variant": {
          const other = state.characters.find((character) => character.id !== state.variant);
          if (other) selectVariant(other.id);
          break;
        }
        default:
          break;
      }
    });
  }

  document.addEventListener("fullscreenchange", syncFullscreenState);
  document.addEventListener("webkitfullscreenchange", syncFullscreenState);

  document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLElement && /input|select|textarea/i.test(event.target.tagName)) {
      return;
    }
    switch (event.key) {
      case "r":
      case "R":
        resetCamera();
        break;
      case "o":
      case "O":
        toggleAutorotate();
        break;
      case "f":
      case "F":
        toggleFullscreen();
        break;
      case " ":
        if (state.animations.length) {
          event.preventDefault();
          togglePlayback();
        }
        break;
      case "Escape":
        if (fullscreenElement()) document.exitFullscreen?.();
        break;
      default:
        break;
    }
  });

  const docsLink = $('[data-role="docs-link"]');
  if (docsLink) {
    docsLink.addEventListener("click", (event) => {
      if (!docsLink.getAttribute("href")) event.preventDefault();
    });
  }
}

function applyManifest(manifest) {
  const characters = Array.isArray(manifest.characters) ? manifest.characters : [];
  const outfits = Array.isArray(manifest.outfits)
    ? manifest.outfits.filter((outfit) => outfit && outfit.id && outfit.variants)
    : [];

  const params = new URLSearchParams(window.location.search);
  const stored = restoreSelection();
  const availableCharacters = characters.length
    ? characters
    : [...new Set(outfits.flatMap((outfit) => Object.keys(outfit.variants)))].map((id) => ({
        id,
        label: titleCase(id),
      }));

  const requestedVariant = params.get("variant") || stored?.variant;
  const requestedOutfit = params.get("outfit") || stored?.outfit;

  const defaultCharacter =
    availableCharacters.find((character) => character.id === manifest.default_variant)?.id ??
    availableCharacters[0]?.id ??
    null;

  let variant = availableCharacters.find((character) => character.id === requestedVariant)?.id ?? defaultCharacter;

  let outfit = outfitByIdIn(outfits, requestedOutfit) ?? null;
  if (outfit && variant && !outfit.variants?.[variant]) {
    const other = (outfit.compatibility || []).find((id) => outfit.variants?.[id]);
    if (other) variant = other;
    else outfit = null;
  }
  if (!outfit || !variant) {
    outfit = outfits.find((item) => variant && item.variants?.[variant]) ?? outfits[0] ?? null;
    if (outfit && variant && !outfit.variants?.[variant]) {
      variant = Object.keys(outfit.variants)[0] ?? variant;
    }
  }

  setState({
    manifest,
    manifestUrl: state.manifestUrl,
    manifestError: null,
    characters: availableCharacters,
    outfits,
    variant,
    outfitId: outfit?.id ?? null,
  });

  const pill = $('[data-role="build-pill"]');
  if (pill) {
    const validated = outfits.filter((item) =>
      Object.values(item.variants || {}).some((entry) => entry.validation?.status === "pass"),
    ).length;
    pill.hidden = false;
    pill.dataset.tone = outfits.length === 0 ? "bad" : validated === outfits.length ? "ok" : "warn";
    const count = $('[data-role="build-count"]', pill);
    const label = $('[data-role="build-label"]', pill);
    if (count) count.textContent = outfits.length ? `${validated}/${outfits.length}` : "0/0";
    if (label) label.textContent = outfits.length ? "validated" : "builds";
  }

  const version = $('[data-role="pipeline-version"]');
  if (version) version.textContent = `pipeline ${manifest.pipeline_version ?? "—"}`;
  const generated = $('[data-role="generated-at"]');
  if (generated) {
    generated.textContent = manifest.generated_at
      ? `manifest ${formatTimestamp(manifest.generated_at)}`
      : "manifest —";
  }

  renderCharacters();
  renderOutfits();
  setPanel(state.panel);
  if (!outfit) {
    setState({ viewer: "empty" });
    setOverlay("empty", {
      title: "No builds published yet",
      message:
        "The manifest is valid but lists no outfits. Run the Blender pipeline, then re-deploy the site.",
    });
    return;
  }
  loadSelected({ silent: true });
  syncUrl();
}

function outfitByIdIn(outfits, id) {
  if (!id) return null;
  return outfits.find((outfit) => outfit.id === id) ?? null;
}

async function boot() {
  bindChrome();
  wireViewer();
  setPanel(state.panel);

  if (!window.customElements?.get?.("model-viewer")) {
    setOverlay("error", {
      message:
        "The model-viewer bundle did not load. Check that vendor/model-viewer.min.js was deployed next to index.html.",
    });
    setState({ viewer: "error" });
  }

  state.manifestUrl = resolveManifestUrl();
  try {
    const manifest = await fetchManifest(state.manifestUrl);
    applyManifest(manifest);
  } catch (error) {
    console.error("[atelier] manifest failed", error);
    setState({ viewer: "empty", manifestError: String(error.message || error) });
    renderCharacters();
    setOverlay("empty", {
      title: "Could not read manifest.json",
      message: `${error.message || error}. Deploy the site with tools/build_site.py, or pass ?manifest=<url> to preview another build.`,
    });
    const pill = $('[data-role="build-pill"]');
    if (pill) {
      pill.hidden = false;
      pill.dataset.tone = "bad";
      const count = $('[data-role="build-count"]', pill);
      const label = $('[data-role="build-label"]', pill);
      if (count) count.textContent = "";
      if (label) label.textContent = "manifest error";
    }
  }
  emit();
  resolveReady(state);
}

let resolveReady;
const ready = new Promise((resolve) => {
  resolveReady = resolve;
});

// Test/debug surface (tools/browser_test.mjs drives the studio through it).
window.atelier = {
  state,
  ready,
  selectVariant: (id) => selectVariant(id),
  selectOutfit: (id) => selectOutfit(id),
  resetCamera,
  setPanel,
  toggleAutorotate,
  toggleFullscreen,
  onStateChange,
  CAMERA_PRESETS,
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}

export { state, selectVariant, selectOutfit, resetCamera, setPanel, onStateChange };
