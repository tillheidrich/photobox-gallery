#!/usr/bin/env node
// Product screenshots for the website and the README, from the real program.
//
//   node tools/marketing_shots.mjs --out /private/tmp/mk/shots
//        [--work /private/tmp/mk/run] [--booth-root ../pg-other-checkout]
//        [--only kiosk,guest,admin,prints] [--reuse] [--keep]
//        [--export <dir>] [--widths 640,1280,1920]
//
// Starts tools/marketing/stage.py (a seeded demo gallery and a real booth
// whose camera draws a lakeside scene: no person in any picture, nothing
// downloaded), drives headless Chrome over the DevTools protocol and writes
// one PNG per screen at device scale 2. --reuse takes a stage that is
// already running (its state.json in --work). --booth-root runs the booth
// from another checkout, so the kiosk shots can be taken again on a later
// commit without touching the rest. --export writes WebP and AVIF copies at
// the given widths (tools/marketing/export.py).
//
// Needs Node 22+ and Google Chrome or Chromium; no npm packages. Ports: the
// stage uses 9810/9811, Chrome 9820. Nothing is printed: the booth prints to
// a hot folder inside --work.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import url from "node:url";

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(`--${n}`); return i >= 0 ? args[i + 1] : d; };
const flag = (n) => args.includes(`--${n}`);
const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const work = opt("work", "/private/tmp/mk-1511/run");
const out = opt("out", "/private/tmp/mk-1511/shots");
const only = (opt("only", "kiosk,guest,admin,prints")).split(",");
const python = process.env.PYTHON || "python3";
fs.mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── the stage ──────────────────────────────────────────────────────────────
let stage = null;
if (!flag("reuse")) {
  const a = [path.join(here, "marketing", "stage.py"), "up", "--work", work];
  if (opt("booth-root")) a.push("--booth-root", path.resolve(opt("booth-root")));
  stage = spawn(python, a, { cwd: root, stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve, reject) => {
    let buf = "";
    stage.stdout.on("data", (d) => { buf += d; process.stdout.write(d); if (buf.includes("READY")) resolve(); });
    stage.on("exit", (c) => reject(new Error(`stage ended with ${c}`)));
  });
}
const S = JSON.parse(fs.readFileSync(path.join(work, "state.json"), "utf8"));
const stopStage = () => { if (stage && !flag("keep")) try { stage.kill("SIGTERM"); } catch {} };
process.on("exit", stopStage);

// ── Chrome ─────────────────────────────────────────────────────────────────
const CHROME = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium"].find((p) => p && fs.existsSync(p));
const port = 9820;
const profile = path.join(work, "chrome-profile");
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--hide-scrollbars", "--no-first-run", "--disable-gpu", "--force-color-profile=srgb", "--lang=de-DE",
  "about:blank"], { stdio: "ignore" });
process.on("exit", () => { try { chrome.kill(); } catch {} });
for (let i = 0; i < 60; i++) { try { await fetch(`http://127.0.0.1:${port}/json/version`); break; } catch { await sleep(200); } }
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let seq = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
  return r.result?.result?.value;
};
await send("Page.enable");
await send("Network.enable");
// The demo must never look like a real evening: no animation caught half way.
const until = async (expr, ms = 20000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await js(expr)) return true; await sleep(150); } return false; };

async function viewport(w, h, mobile = false) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile });
  await send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
}
async function go(u, settle = 900) {
  await send("Page.navigate", { url: u });
  await until("document.readyState === 'complete'", 20000);
  await until("[...document.images].every((i) => i.complete)", 8000);
  await sleep(settle);
}
async function cookie(value) {
  await send("Network.clearBrowserCookies");
  if (value) await send("Network.setCookie", { name: "session", value, url: S.gallery + "/" });
}
const made = [];
async function shot(name, { full = false } = {}) {
  const p = { format: "png", captureBeyondViewport: full };
  if (full) {
    const m = await send("Page.getLayoutMetrics");
    const cs = m.result.cssContentSize || m.result.contentSize;
    p.clip = { x: 0, y: 0, width: cs.width, height: Math.min(cs.height, 4000), scale: 1 };
  }
  const r = await send("Page.captureScreenshot", p);
  const file = path.join(out, `${name}.png`);
  fs.writeFileSync(file, Buffer.from(r.result.data, "base64"));
  made.push(file);
  console.log("shot", name);
}
// A screen-sized cut of the page that starts a little above an element, also
// where the page scrolls inside a container of its own.
// With alignBottom the cut ends a little below the element instead (an
// element at the end of the page).
async function shotAt(name, selector, height, above = 24, alignBottom = false) {
  const box = await js(`(() => { const e = document.querySelector(${JSON.stringify(selector)});
      if (!e) return null; e.closest('details')?.setAttribute('open', '');
      const r = e.getBoundingClientRect(); return [r.top + window.scrollY, r.bottom + window.scrollY]; })()`);
  if (box === null) { console.log("missing", selector); return shot(name); }
  const w = await js("window.innerWidth");
  const y = alignBottom ? box[1] + above - height : box[0] - above;
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: Math.max(0, y), width: w, height, scale: 1 } });
  const file = path.join(out, `${name}.png`);
  fs.writeFileSync(file, Buffer.from(r.result.data, "base64"));
  made.push(file);
  console.log("shot", name);
}
const click = (sel) => js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; e.click(); return true; })()`);
const screen = (n) => `document.querySelector('#app')?.dataset.screen === ${JSON.stringify(n)}`;

// ── kiosk ──────────────────────────────────────────────────────────────────
if (only.includes("kiosk")) {
  await cookie(null);
  await viewport(1600, 900);
  await go(S.booth + "/", 1500);
  await until(screen("start"));
  await sleep(1200);
  await shot("kiosk-start");
  // Collage: the set-up screen with the layouts as pictures of the sheet.
  await click('#modes .mode[data-mode="collage"]');
  await until(screen("setup"));
  await until("[...document.querySelectorAll('#templateChips img')].length > 0 && [...document.querySelectorAll('#templateChips img')].every((i) => i.complete && i.naturalWidth)", 15000);
  await js(`(() => { const c = [...document.querySelectorAll('#templateChips [data-template], #templateChips button')]
      .find((b) => /gartenfest-hoch-4/.test(b.outerHTML)); if (c) c.click(); })()`);
  await sleep(1200);
  await shot("kiosk-layouts");
  await click('[data-action="go"]');
  await until(screen("capture"));
  await sleep(3200);                       // the countdown is set to 9 s in the demo profile
  await shot("kiosk-countdown");
  await until(screen("result"), 90000);
  await sleep(2500);
  await shot("kiosk-result");
  await click('[data-action="done"]');
  await until(screen("start"));
  await sleep(800);
  await click('#modes .mode[data-mode="boomerang"]');
  if (await until(screen("setup"), 8000)) { await sleep(1500); await shot("kiosk-boomerang"); }
  await click('[data-action="home"]');
  await until(screen("start"));
  await sleep(600);
  await click("#galleryBtn");
  await until(screen("gallery"));
  await until("[...document.querySelectorAll('#galleryGrid img')].every((i) => i.complete)", 8000);
  await sleep(1200);
  await shot("kiosk-gallery");
  await click('[data-action="home"]');
}

// ── guests ─────────────────────────────────────────────────────────────────
const G = S.gallery, slug = S.slug;
if (only.includes("guest")) {
  await cookie(null);
  await viewport(390, 844, true);
  await go(`${G}/gallery/${slug}`, 1500);
  await shot("guest-gallery-phone");
  await js("document.querySelector('.photo-item a, .photo-item img, .photo-item')?.click()");
  await sleep(1500);
  await shot("guest-lightbox-phone");
  await go(`${G}/gallery/${slug}`, 1000);
  await js("document.querySelector('#guestUploadZone')?.closest('.card')?.setAttribute('id', 'mkUpload')");
  await js("document.querySelector('#mkUpload')?.scrollIntoView({ block: 'center' })");
  await sleep(1000);
  await shotAt("guest-upload-phone", "#mkUpload", 844, 40, true);
  const run = S.runs.find((r) => r.template === "gartenfest-hoch-4") || S.runs[0];
  await go(run.share_url, 1500);
  await shot("guest-run-phone");
  await cookie(S.guest_cookie);
  await go(`${G}/meine`, 1500);
  await shot("guest-mine-phone");
  await cookie(null);
  await viewport(1440, 900);
  await go(`${G}/gallery/${slug}`, 1500);
  await shot("guest-gallery-desktop");
  await js("document.querySelector('.photo-item a, .photo-item img, .photo-item')?.click()");
  await sleep(1500);
  await shot("guest-lightbox-desktop");
}

// ── admin ──────────────────────────────────────────────────────────────────
if (only.includes("admin")) {
  await cookie(S.admin_cookie);
  await viewport(1440, 900);
  const E = S.events.main, D = S.device_id;
  await go(`${G}/admin/dashboard`, 1200); await shot("admin-dashboard");
  await go(`${G}/admin/events/${E}/edit`, 1200);
  await js("(() => { const e = document.querySelector('#layouts'); if (!e) return; e.closest('details')?.setAttribute('open', ''); e.scrollIntoView({ block: 'start' }); })()");
  await until("[...document.querySelectorAll('#layouts img')].every((i) => i.complete && i.naturalWidth)", 20000);
  await sleep(1200); await shotAt("admin-event-layouts", "#layouts", 900, 48);
  await go(`${G}/admin/vorlagen/bibliothek`, 2000); await shot("admin-template-library");
  const tpl = S.templates.find((t) => /gartenfest-hoch-4/.test(t)) || S.templates[0];
  await go(`${G}/admin/vorlagen/editor?datei=${encodeURIComponent(tpl)}`, 2500); await shot("admin-template-editor");
  await go(`${G}/admin/booths`, 1200); await shot("admin-booth-status");
  await go(`${G}/admin/devices/${D}/booth`, 1200); await shot("admin-booth-profile");
  await cookie(null);
}

// ── print sheets, as the booth composed them ───────────────────────────────
if (only.includes("prints")) {
  for (const [name, re] of [["print-kollektion", /gartenfest-hoch-4/], ["print-schlicht", /schlicht-2-schwarz/]]) {
    const r = S.runs.find((x) => re.test(x.template || ""));
    if (!r) continue;
    const res = await fetch(`${S.booth}/media/${r.uid}/result`);
    fs.writeFileSync(path.join(out, `${name}.jpg`), Buffer.from(await res.arrayBuffer()));
    made.push(path.join(out, `${name}.jpg`));
    console.log("sheet", name);
  }
}

fs.writeFileSync(path.join(out, "shots.json"), JSON.stringify({ made: made.map((f) => path.basename(f)),
  booth_root: S.booth_root, taken: new Date().toISOString() }, null, 1));
if (opt("export")) {
  const r = spawnSync(python, [path.join(here, "marketing", "export.py"), out, opt("export"), opt("widths", "640,1280,1920")],
    { stdio: "inherit" });
  if (r.status) process.exitCode = 1;
}
ws.close();
stopStage();
process.exit(process.exitCode || 0);
