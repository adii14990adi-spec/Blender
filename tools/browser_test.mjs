#!/usr/bin/env node
/**
 * End-to-end browser tests for the Atelier web viewer.
 *
 * Serves the assembled site (default `docs/`) under a *repository subpath*
 * (`/Blender/`) - exactly how GitHub project pages host it - then drives a real
 * Chromium through the flows that matter:
 *
 *   shell rendering, GLB loading, character switching without a reload, outfit
 *   switching, downloads, camera controls, fullscreen, animation transport,
 *   deep links, error/empty states, desktop layout, and a strict "no 404s" pass.
 *
 * Screenshots for the test report are written to `docs/screenshots/`.
 *
 * Chrome discovery: `--chrome <path>`, else $CHROME_PATH, else the usual
 * `/usr/bin/google-chrome`, `/usr/bin/chromium`, macOS app path.
 * Extra flags: `--chrome-args "--no-sandbox --use-gl=angle"` or $CHROME_ARGS.
 */

import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { access, mkdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const SUBPATH = '/Blender/';

/* ------------------------------------------------------------------ args */
function parseArgs(argv) {
  const opts = {
    site: path.join(REPO, 'docs'),
    shots: path.join(REPO, 'docs', 'screenshots'),
    fixtures: path.join(REPO, 'tests', 'fixtures'),
    chrome: process.env.CHROME_PATH || '',
    chromeArgs: process.env.CHROME_ARGS || '',
    port: 8123,
    only: '',
    headful: false,
    keepOpen: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];
    if (arg === '--site') opts.site = path.resolve(next());
    else if (arg === '--shots') opts.shots = path.resolve(next());
    else if (arg === '--fixtures') opts.fixtures = path.resolve(next());
    else if (arg === '--chrome') opts.chrome = next();
    else if (arg === '--chrome-args') opts.chromeArgs = next();
    else if (arg === '--port') opts.port = Number(next());
    else if (arg === '--only') opts.only = next();
    else if (arg === '--headful') opts.headful = true;
    else if (arg === '--keep-open') opts.keepOpen = true;
    else if (arg === '--help' || arg === '-h') {
      console.log('usage: browser_test.mjs [--site docs] [--shots dir] [--chrome path] [--port n] [--only name]');
      process.exit(0);
    }
  }
  return opts;
}

const CHROME_CANDIDATES = [
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];

async function findChrome(explicit) {
  const candidates = [explicit, ...CHROME_CANDIDATES].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      /* keep looking */
    }
  }
  throw new Error(
    'no Chrome/Chromium found; pass --chrome <path> or set CHROME_PATH',
  );
}

/* ---------------------------------------------------------------- server */
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.glb': 'model/gltf-binary',
  '.fbx': 'application/octet-stream',
  '.md': 'text/markdown; charset=utf-8',
};

async function resolveFile(root, urlPath) {
  const safe = path.normalize(decodeURIComponent(urlPath)).replace(/^(\.\.[/\\])+/, '');
  const target = path.join(root, safe);
  if (!target.startsWith(root)) return null;
  try {
    const info = await stat(target);
    if (info.isDirectory()) {
      const index = path.join(target, 'index.html');
      await stat(index);
      return index;
    }
    return target;
  } catch {
    return null;
  }
}

function startServer({ site, fixtures, port }) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const pathname = url.pathname;

    let file = null;
    if (pathname.startsWith(`${SUBPATH}fixtures/`)) {
      file = await resolveFile(fixtures, pathname.slice(`${SUBPATH}fixtures/`.length));
    } else if (pathname.startsWith(SUBPATH)) {
      file = await resolveFile(site, pathname.slice(SUBPATH.length) || 'index.html');
    } else if (pathname === SUBPATH.slice(0, -1)) {
      res.writeHead(301, { Location: SUBPATH });
      res.end();
      return;
    }

    if (!file) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end(`not found: ${pathname}`);
      return;
    }

    const type = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
    res.writeHead(200, {
      'Content-Type': type,
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
    });
    if (req.method === 'HEAD') {
      res.end();
      return;
    }
    createReadStream(file).pipe(res);
  });

  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

/* ----------------------------------------------------------------- tests */
class Runner {
  constructor() {
    this.results = [];
  }

  async run(name, fn) {
    const started = Date.now();
    try {
      const details = (await fn()) || '';
      this.results.push({ name, ok: true, details, ms: Date.now() - started });
      console.log(`  \x1b[32m✓\x1b[0m ${name}${details ? ` — ${details}` : ''}`);
    } catch (error) {
      this.results.push({ name, ok: false, details: error.message, ms: Date.now() - started });
      console.log(`  \x1b[31m✗\x1b[0m ${name}\n      ${error.message.split('\n').join('\n      ')}`);
    }
  }

  get failed() {
    return this.results.filter((result) => !result.ok);
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

/* helpers ----------------------------------------------------------------- */
// Pixel 7-class viewport. `isMobile: false` keeps the CSS viewport exactly at
// 412 px so the responsive breakpoints are tested at their real widths.
const MOBILE = { width: 412, height: 915, deviceScaleFactor: 2, isMobile: false, hasTouch: true };
const DESKTOP = { width: 1440, height: 900, deviceScaleFactor: 1, isMobile: false, hasTouch: false };
const MOBILE_UA =
  'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';

async function newPage(browser, viewport, { mobile = true } = {}) {
  // A fresh context per scenario: the studio remembers the last selection in
  // localStorage, and tests must not inherit each other's state.
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  page.__context = context;
  await page.setViewport(viewport);
  if (mobile) await page.setUserAgent(MOBILE_UA);
  const problems = [];
  const responses = [];
  page.on('response', (response) => {
    responses.push({ url: response.url(), status: response.status() });
  });
  page.on('requestfailed', (request) => {
    const reason = request.failure()?.errorText || 'unknown';
    // Changing garment aborts the in-flight GLB on purpose.
    if (reason.includes('ERR_ABORTED')) return;
    problems.push(`${request.url()} → ${reason}`);
  });
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  return { page, responses, problems };
}

async function openStudio(page, base, query = '') {
  await page.goto(`${base}${query}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => Boolean(window.atelier), { timeout: 20000 });
  await page.waitForFunction(
    () => ['ready', 'error', 'empty'].includes(window.atelier.state.viewer),
    { timeout: 45000 },
  );
  return page.evaluate(() => ({ ...window.atelier.state, manifest: undefined }));
}

async function waitForViewer(page, variant, outfit) {
  await page.waitForFunction(
    (expectedVariant, expectedOutfit) => {
      const state = window.atelier?.state;
      return (
        state &&
        state.viewer === 'ready' &&
        state.variant === expectedVariant &&
        (!expectedOutfit || state.outfitId === expectedOutfit)
      );
    },
    { timeout: 45000 },
    variant,
    outfit,
  );
}

async function shot(page, dir, name) {
  await mkdir(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`) });
}

function badResponses(responses) {
  return responses.filter((response) => response.status >= 400);
}

/* ------------------------------------------------------------------- main */
async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const chrome = await findChrome(opts.chrome);
  const server = await startServer(opts);
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}${SUBPATH}`;
  const extraArgs = opts.chromeArgs
    .split(/\s+/)
    .filter(Boolean)
    .concat([
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--hide-scrollbars',
      '--mute-audio',
      // CI runners and this sandbox have no GPU: allow Chrome's software
      // rasteriser, which recent Chrome requires an explicit opt-in for.
      '--enable-unsafe-swiftshader',
    ]);

  console.log(`site:    ${opts.site}`);
  console.log(`shots:   ${opts.shots}`);
  console.log(`chrome:  ${chrome}`);
  console.log(`serving: ${base}`);

  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: !opts.headful,
    args: extraArgs,
    protocolTimeout: 60000,
  });

  const runner = new Runner();
  const should = (name) => !opts.only || name.includes(opts.only);

  try {
    /* ---------------------------------------------------------- mobile */
    const mobile = await newPage(browser, MOBILE);
    const { page } = mobile;

    if (should('shell')) {
      await runner.run('mobile · shell renders from the manifest', async () => {
        const state = await openStudio(page, base);
        const chrome = await page.evaluate(() => ({
          characters: document.querySelectorAll('[data-role="characters"] button').length,
          cards: document.querySelectorAll('[data-panel-name="outfits"] .outfit-card').length,
          tabbar: getComputedStyle(document.querySelector('.tab-bar')).display,
          sidebar: getComputedStyle(document.querySelector('.sidebar')).display,
          pill: document.querySelector('[data-role="build-pill"]').textContent.replace(/\s+/g, ' ').trim(),
        }));
        assertEqual(chrome.characters, 2, 'character buttons');
        assert(chrome.cards >= 2, `outfit cards (got ${chrome.cards})`);
        assertEqual(chrome.tabbar, 'grid', 'bottom tab bar visible on mobile');
        assertEqual(chrome.sidebar, 'none', 'sidebar hidden on mobile');
        assert(
          /^\d+\/\d+ validated$/.test(chrome.pill),
          `build pill shows validation summary (got "${chrome.pill}")`
        );
        assert(state.manifest === undefined, 'manifest payload is not exposed in state');
        await waitForViewer(page, state.variant, state.outfitId);
        await shot(page, opts.shots, 'mobile-outfits');
        return `${chrome.characters} characters · ${chrome.cards} outfits · ${chrome.pill}`;
      });
    }

    if (should('model')) {
      await runner.run('mobile · GLB loads and renders', async () => {
        const info = await page.evaluate(() => {
          const viewer = document.querySelector('model-viewer');
          return {
            loaded: viewer.loaded,
            visible: viewer.modelIsVisible,
            src: viewer.getAttribute('src'),
            size: [viewer.clientWidth, viewer.clientHeight],
            overlayHidden: document.querySelector('[data-role="overlay-loading"]').hidden,
          };
        });
        assert(info.loaded, 'model-viewer reports loaded');
        assert(info.visible, 'model is visible');
        assert(/^outputs\/.+\.glb$/.test(info.src), `src is a relative asset path (got ${info.src})`);
        assert(info.size[0] > 200 && info.size[1] > 200, 'viewport has a real size');
        assert(info.overlayHidden, 'loading overlay dismissed');
        return `${info.src} → ${info.size[0]}×${info.size[1]}`;
      });
    }

    if (should('variant')) {
      await runner.run('mobile · character switch without a reload', async () => {
        await page.evaluate(() => {
          window.__noReloadMarker = 'kept';
        });
        const current = await page.evaluate(() => window.atelier.state.variant);
        const target = current === 'female' ? 'male' : 'female';
        await page.click(`[data-variant="${target}"]`);
        await waitForViewer(page, target);
        const state = await page.evaluate(() => ({
          marker: window.__noReloadMarker,
          src: document.querySelector('model-viewer').getAttribute('src'),
          pressed: document.querySelector('[data-variant="female"]').getAttribute('aria-pressed'),
          url: location.search,
        }));
        assertEqual(state.marker, 'kept', 'the page did not reload');
        assert(state.src.startsWith(`outputs/${target}/`), `${target} asset loaded (src ${state.src})`);
        assertEqual(state.pressed, 'true', `${target} button is pressed`);
        assert(new RegExp(`variant=${target}`).test(state.url), 'URL tracks the selection');
        await shot(page, opts.shots, `mobile-${target}`);
        return state.src;
      });
    }

    if (should('outfit')) {
      await runner.run('mobile · outfit library switches builds', async () => {
        const before = await page.evaluate(() => window.atelier.state.outfitId);
        const visibleCards = await page.$$('[data-panel-name="outfits"] .outfit-card');
        assert(visibleCards.length >= 2, `at least two outfit cards (got ${visibleCards.length})`);
        const target = await page.evaluate(() => {
          const cards = Array.from(document.querySelectorAll('[data-panel-name="outfits"] .outfit-card'));
          const other = cards.find((card) => card.dataset.outfit !== window.atelier.state.outfitId);
          return other?.dataset.outfit;
        });
        assert(target, 'found another outfit to select');
        await page.click(`[data-panel-name="outfits"] .outfit-card[data-outfit="${target}"]`);
        const activeVariant = await page.evaluate(() => window.atelier.state.variant);
        await waitForViewer(page, activeVariant, target);
        const state = await page.evaluate(() => ({
          outfitId: window.atelier.state.outfitId,
          src: document.querySelector('model-viewer').getAttribute('src'),
          hud: document.querySelector('[data-role="hud-outfit"]').textContent,
          current: document.querySelector('.outfit-card[aria-current="true"]')?.dataset.outfit,
        }));
        assertEqual(state.outfitId, target, 'state follows the card');
        assert(state.src.includes(target), `asset path follows the card (src ${state.src})`);
        assertEqual(state.current, target, 'card marked aria-current');
        assert(state.hud.length > 0, 'HUD shows the outfit name');
        await shot(page, opts.shots, 'mobile-outfit-switch');
        return `${before} → ${state.outfitId}`;
      });
    }

    if (should('panel')) {
      await runner.run('mobile · view panel exposes camera + animation state', async () => {
        await page.click('.tab[data-panel-target="view"]');
        const panel = await page.evaluate(() => ({
          visible: !document.querySelector('[data-panel-name="view"]').hidden,
          cameras: document.querySelectorAll('[data-camera]').length,
          animationNote: document.querySelector('[data-role="animation-empty"]')?.textContent.trim().slice(0, 200),
        }));
        assert(panel.visible, 'view panel is shown');
        assertEqual(panel.cameras, 5, 'camera presets');
        assert(/no animation clips/i.test(panel.animationNote || ''), 'static build explains the missing clips');
        await shot(page, opts.shots, 'mobile-view-panel');
        await page.click('.tab[data-panel-target="outfits"]');
        return `${panel.cameras} presets`;
      });
    }

    if (should('controls')) {
      await runner.run('mobile · camera reset, auto-rotate and fullscreen', async () => {
        const viewer = 'model-viewer';
        await page.click('[data-action="autorotate"]');
        const rotating = await page.evaluate((sel) => document.querySelector(sel).autoRotate, viewer);
        assert(rotating === true, 'auto-rotate toggled on');
        await page.click('[data-action="autorotate"]');

        await page.evaluate((sel) => {
          document.querySelector(sel).setAttribute('camera-orbit', '45deg 60deg 3.4m');
        }, viewer);
        await page.click('[data-action="reset-camera"]');
        const orbits = await page.evaluate((sel) => ({
          current: document.querySelector(sel).getAttribute('camera-orbit'),
          hero: window.atelier.CAMERA_PRESETS.hero,
        }), viewer);
        const expectedPrefix = `${orbits.hero.theta}deg ${orbits.hero.phi}deg `;
        assert(
          orbits.current.startsWith(expectedPrefix),
          `camera reset to the hero orbit (got ${orbits.current}, expected ${expectedPrefix}…)`,
        );

        await page.click('[data-action="fullscreen"]');
        await new Promise((resolve) => setTimeout(resolve, 600));
        const full = await page.evaluate(() => ({
          fs: Boolean(document.fullscreenElement),
          pseudo: document.querySelector('[data-role="stage-frame"]').classList.contains('stage__frame--pseudo-fullscreen'),
          body: document.body.classList.contains('is-fullscreen'),
        }));
        assert(full.fs || full.pseudo, 'fullscreen engaged (native or pseudo fallback)');
        assert(full.body, 'body tracks the fullscreen state');
        await shot(page, opts.shots, 'mobile-fullscreen');
        await page.evaluate(async () => {
          if (document.fullscreenElement) await document.exitFullscreen();
          document.querySelector('[data-role="stage-frame"]').classList.remove('stage__frame--pseudo-fullscreen');
          document.dispatchEvent(new Event('fullscreenchange'));
        });
        await new Promise((resolve) => setTimeout(resolve, 400));
        const after = await page.evaluate(() => document.body.classList.contains('is-fullscreen'));
        assert(!after, 'exiting fullscreen restores the studio layout');
        return full.fs ? 'native fullscreen' : 'pseudo fullscreen fallback';
      });
    }

    if (should('downloads')) {
      await runner.run('mobile · download links point at real files', async () => {
        await page.click('.tab[data-panel-target="asset"]');
        const links = await page.$$eval('[data-panel-name="asset"] .download', (nodes) =>
          nodes.map((node) => ({
            href: node.getAttribute('href'),
            absolute: node.href,
            download: node.getAttribute('download'),
            size: node.querySelector('.download__size').textContent,
            disabled: node.getAttribute('aria-disabled'),
          })),
        );
        assertEqual(links.length, 2, 'GLB + FBX rows');
        for (const link of links) {
          assert(!/^\//.test(link.href) && !/^https?:/i.test(link.href), `href is relative (got ${link.href})`);
          assert(link.download, `download attribute on ${link.href}`);
          assertEqual(link.disabled, 'false', `link enabled (${link.href})`);
          const status = await page.evaluate(async (absolute) => {
            const response = await fetch(absolute, { method: 'HEAD' });
            return response.status;
          }, link.absolute);
          assertEqual(status, 200, `${link.href} is downloadable`);
        }
        const sizes = links.map((link) => link.size).join(' / ');
        assert(/\d/.test(sizes), 'sizes rendered');
        await shot(page, opts.shots, 'mobile-asset-panel');
        await page.click('.tab[data-panel-target="outfits"]');
        return `${links.length} files · ${sizes}`;
      });
    }

    if (should('network')) {
      await runner.run('mobile · no failed requests or console errors', async () => {
        const bad = badResponses(mobile.responses);
        assertEqual(bad.length, 0, `no 4xx/5xx responses (${bad.map((r) => `${r.status} ${r.url}`).join(', ')})`);
        assertEqual(mobile.problems.length, 0, `no failed requests or page errors (${mobile.problems.join('; ')})`);
        return `${mobile.responses.length} requests, all ok`;
      });
    }

    await page.__context.close();

    /* -------------------------------------------------------- animations */
    if (should('animation')) {
      await runner.run('mobile · animation transport works when clips exist', async () => {
        const anim = await newPage(browser, MOBILE);
        await openStudio(anim.page, base, '?manifest=fixtures/manifest.animated.json');
        await anim.page.waitForFunction(() => window.atelier.state.viewer === 'ready', { timeout: 45000 });
        await anim.page.waitForFunction(() => window.atelier.state.animations.length > 0, { timeout: 15000 });
        const info = await anim.page.evaluate(() => ({
          clips: window.atelier.state.animations,
          buttonHidden: document.querySelector('[data-action="animation"]').hidden,
          playing: window.atelier.state.playing,
        }));
        assert(info.clips.length >= 1, 'manifest asset exposes an animation clip');
        assert(!info.buttonHidden, 'play/pause control is visible');
        await anim.page.click('[data-action="animation"]');
        const paused = await anim.page.evaluate(() => ({
          playing: window.atelier.state.playing,
          pressed: document.querySelector('[data-action="animation"]').getAttribute('aria-pressed'),
        }));
        assertEqual(paused.playing, false, 'pause toggles playback off');
        assertEqual(paused.pressed, 'false', 'button reflects the paused state');
        await anim.page.click('[data-action="animation"]');
        const resumed = await anim.page.evaluate(() => window.atelier.state.playing);
        assertEqual(resumed, true, 'play toggles playback back on');
        await shot(anim.page, opts.shots, 'mobile-animation');
        await anim.page.__context.close();
        return `${info.clips.length} clip(s): ${info.clips.join(', ')}`;
      });
    }

    if (should('error')) {
      await runner.run('mobile · missing manifest shows an actionable error', async () => {
        const broken = await newPage(browser, MOBILE);
        await openStudio(broken.page, base, '?manifest=fixtures/manifest.missing.json');
        const view = await broken.page.evaluate(() => ({
          empty: !document.querySelector('[data-role="overlay-empty"]').hidden,
          title: document.querySelector('[data-role="empty-title"]').textContent,
          message: document.querySelector('[data-role="empty-message"]').textContent,
          pill: document.querySelector('[data-role="build-pill"]').textContent.replace(/\s+/g, ' ').trim(),
        }));
        assert(view.empty, 'empty/error overlay shown');
        assert(/manifest/i.test(view.title), `title mentions the manifest (got "${view.title}")`);
        assert(/404/.test(view.message), `message carries the HTTP status (got "${view.message}")`);
        assertEqual(view.pill, 'manifest error', 'header pill flags the failure');
        await shot(broken.page, opts.shots, 'mobile-manifest-error');
        await broken.page.__context.close();
        return view.message.slice(0, 80);
      });
    }

    if (should('empty')) {
      await runner.run('mobile · empty manifest renders the empty state', async () => {
        const empty = await newPage(browser, MOBILE);
        await openStudio(empty.page, base, '?manifest=fixtures/manifest.empty.json');
        const view = await empty.page.evaluate(() => ({
          overlay: !document.querySelector('[data-role="overlay-empty"]').hidden,
          title: document.querySelector('[data-role="empty-title"]').textContent,
          cards: document.querySelectorAll('[data-panel-name="outfits"] .outfit-card').length,
        }));
        assert(view.overlay, 'empty overlay shown');
        assert(/no builds/i.test(view.title), `empty title (got "${view.title}")`);
        assertEqual(view.cards, 0, 'no cards rendered');
        await empty.page.__context.close();
        return view.title;
      });
    }

    /* ---------------------------------------------------------- desktop */
    if (should('desktop')) {
      await runner.run('desktop · sidebar + stage + properties layout', async () => {
        const desk = await newPage(browser, DESKTOP, { mobile: false });
        const deskState = await openStudio(desk.page, base);
        await waitForViewer(desk.page, deskState.variant);
        const layout = await desk.page.evaluate(() => {
          const style = (selector) => getComputedStyle(document.querySelector(selector));
          const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
          const stage = document.querySelector('.stage').getBoundingClientRect();
          const props = document.querySelector('.properties').getBoundingClientRect();
          return {
            sidebar: style('.sidebar').display,
            properties: style('.properties').display,
            tabbar: style('.tab-bar').display,
            sheet: style('.sheet').display,
            widths: [Math.round(sidebar.width), Math.round(stage.width), Math.round(props.width)],
            downloads: document.querySelectorAll('.properties .download').length,
          };
        });
        assertEqual(layout.sidebar, 'flex', 'sidebar visible on desktop');
        assertEqual(layout.properties, 'flex', 'properties panel visible on desktop');
        assertEqual(layout.tabbar, 'none', 'mobile tab bar hidden on desktop');
        assertEqual(layout.sheet, 'none', 'mobile sheet hidden on desktop');
        assert(layout.widths[1] > layout.widths[0] && layout.widths[1] > layout.widths[2], `stage is the widest column (${layout.widths})`);
        assertEqual(layout.downloads, 2, 'downloads rendered in the properties panel');
        await shot(desk.page, opts.shots, 'desktop-studio');
        await desk.page.__context.close();
        return `columns ${layout.widths.join(' / ')} px`;
      });
    }

    if (should('deeplink')) {
      await runner.run('mobile · deep link selects character, outfit and camera', async () => {
        const deep = await newPage(browser, MOBILE);
        await openStudio(deep.page, base, '?variant=female&outfit=parka_urban_01');
        await waitForViewer(deep.page, 'female', 'parka_urban_01');
        const selected = await deep.page.evaluate(() => ({
          current: document.querySelector('.outfit-card[aria-current="true"]')?.dataset.outfit,
          pressed: document.querySelector('[data-variant="female"]').getAttribute('aria-pressed'),
          src: document.querySelector('model-viewer').getAttribute('src'),
        }));
        assertEqual(selected.current, 'parka_urban_01', 'deep-linked outfit selected');
        assertEqual(selected.pressed, 'true', 'deep-linked character selected');
        assert(selected.src.startsWith('outputs/female/parka_urban_01/'), `deep-linked asset loaded (${selected.src})`);
        await shot(deep.page, opts.shots, 'mobile-deeplink');
        await deep.page.__context.close();
        return selected.src;
      });
    }

    if (should('subpath')) {
      await runner.run('deployment · every request stays under the project subpath', async () => {
        const audit = await newPage(browser, MOBILE);
        const auditState = await openStudio(audit.page, base);
        await waitForViewer(audit.page, auditState.variant);
        const urls = audit.responses.map((response) => response.url).filter((url) => url.startsWith('http'));
        const outside = urls.filter((url) => {
          const { pathname } = new URL(url);
          return pathname !== SUBPATH.slice(0, -1) && !pathname.startsWith(SUBPATH);
        });
        const rootRelative = urls.filter((url) => {
          const { pathname } = new URL(url);
          return ['/manifest.json', '/index.html', '/outputs/'].some((prefix) => pathname.startsWith(prefix));
        });
        assertEqual(outside.length, 0, `no requests outside ${SUBPATH} (${outside.join(', ')})`);
        assertEqual(rootRelative.length, 0, `no root-relative asset paths (${rootRelative.join(', ')})`);
        await audit.page.__context.close();
        return `${urls.length} requests, all under ${SUBPATH}`;
      });
    }

    /* ----------------------------------------------------------- report */
    const summary = {
      generated_at: new Date().toISOString(),
      base_url: base,
      chrome,
      results: runner.results,
      passed: runner.results.filter((r) => r.ok).length,
      failed: runner.failed.length,
    };
    await mkdir(path.join(opts.shots, '..'), { recursive: true });
    const fs = await import('node:fs/promises');
    const reportPath = path.join(opts.shots, '..', 'browser-test-results.json');
    await fs.writeFile(reportPath, `${JSON.stringify(summary, null, 2)}\n`);

    // Human-readable companion so the docs never drift from the last run.
    const markdown = [
      '# Browser test results',
      '',
      `Generated ${summary.generated_at} against \`${base}\` with ${path.basename(chrome)}.`,
      '',
      `**${summary.passed} passed, ${summary.failed} failed**`,
      '',
      '| # | Scenario | Result | Time | Notes |',
      '| --- | --- | --- | --- | --- |',
      ...runner.results.map((result, index) =>
        `| ${index + 1} | ${result.name} | ${result.ok ? 'pass' : 'FAIL'} | ${(result.ms / 1000).toFixed(1)} s | ${String(result.details).replace(/\|/g, '\\|').replace(/\n/g, ' ').slice(0, 160)} |`,
      ),
      '',
      'Screenshots: ' + (opts.shots.includes('docs') ? '`screenshots/`' : `\`${opts.shots}\``),
      '',
    ].join('\n');
    await fs.writeFile(path.join(opts.shots, '..', 'browser-test-report.md'), markdown);

    console.log(
      `\n${summary.passed} passed, ${summary.failed} failed · report: ${path.relative(REPO, reportPath)}`,
    );
    if (opts.keepOpen) {
      console.log('keeping the browser open (ctrl-c to quit)');
      await new Promise(() => {});
    }
    return summary.failed === 0 ? 0 : 1;
  } finally {
    await browser.close();
    server.close();
  }
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
