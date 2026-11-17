#!/usr/bin/env node
// One-finger pan on the booth's pages, the way the touch screen does it.
//
//   node tools/touch_pan_check.mjs http://127.0.0.1:9832 --pin-file <file with the admin PIN>
//        [--out /tmp/touch-pan] [--size 1920x1200] [--shots]
//
// Headless Chrome with touch emulation, the window's own guard script
// (boothcore/window.py GUARD_JS, read from the source) and pywebview's
// zoomable=False touch blocker injected after every load, as in the own
// window. For every page and every scrollable box on it, a touch scroll
// gesture (Input.synthesizeScrollGesture, gestureSourceType "touch") must
// move it; a two-finger pinch must not zoom. Writes <out>/touch-pan.json,
// exit code 1 when a scroller does not move under a finger.
// Needs Node 22+ and Chrome; no npm packages.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : dflt; };
const base = args[0];
const out = opt("out", "/tmp/touch-pan");
const [W, H] = opt("size", "1920x1200").split("x").map(Number);
const pinFile = opt("pin-file", "");
const PIN = pinFile ? fs.readFileSync(pinFile, "utf8").trim() : "";
const shots = args.includes("--shots");
// --mouse: the same pans as left-button mouse drags (a touch screen in mouse mode).
const MOUSE = args.includes("--mouse");
fs.mkdirSync(out, { recursive: true });

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const windowPy = fs.readFileSync(path.join(ROOT, "booth/boothcore/window.py"), "utf8");
const GUARD = windowPy.split('GUARD_JS = r"""')[1].split('"""')[0];
// pywebview 6.2 js/customize.js with zoomable=False: a non-passive touchstart
// on body that cancels multi-finger touches.
const PYWEBVIEW = `document.body.addEventListener('touchstart', function (e) {
  if ((e.touches.length > 1) || e.targetTouches.length > 1) { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); }
}, {passive: false});
document.body.style.cssText += ';-webkit-user-select:none;user-select:none';`;

const CHROME = [process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find((p) => p && fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME=/path/to/chrome"); process.exit(2); }
const port = 9300 + Math.floor(Math.random() * 300);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "touch-pan-chrome-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--disable-gpu", "--disable-pinch", "--overscroll-history-navigation=0",
  "--disable-features=ElasticOverscroll", "about:blank"], { stdio: "ignore" });
process.on("exit", () => { try { chrome.kill(); } catch {} });

for (let i = 0; i < 50; i++) {
  try { await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch {}
  await new Promise((r) => setTimeout(r, 200));
}
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let seq = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
  return r.result.result.value;
}
await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 10 });
// --block "*/static/pan.js": the page without that file, to see what it changes.
if (opt("block", "")) { await send("Network.enable"); await send("Network.setBlockedURLs", { urls: opt("block").split(",") }); }

async function open(url) {
  await send("Page.navigate", { url });
  for (let i = 0; i < 60; i++) {
    await sleep(150);
    try { if (await evaluate("document.readyState") === "complete") break; } catch {}
  }
  await evaluate(GUARD);
  await evaluate(PYWEBVIEW);
  await sleep(300);
}
async function login() {
  const needs = await evaluate(`!!document.getElementById("pad") && !document.getElementById("pin").hidden`);
  if (!needs || !PIN) return;
  await evaluate(`(() => { const pad = [...document.querySelectorAll("#pad button")];
    for (const d of ${JSON.stringify(PIN)}) pad.find((b) => b.textContent === d).click();
    pad.find((b) => b.textContent === "OK").click(); })()`);
  await sleep(1200);
}

// Every box that can scroll right now, plus the page itself.
const FIND = `(() => {
  const out = [];
  const se = document.scrollingElement;
  if (se.scrollHeight > se.clientHeight + 4) out.push({ sel: "document", axis: "y", x: innerWidth / 2, y: innerHeight * 0.6 });
  let n = 0;
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight || cs.visibility === "hidden") continue;
    // A few pixels of overflow (a negative margin for the scrollbar) is not a box to pan.
    const y = /(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 16;
    const x = /(auto|scroll)/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 16;
    if (!x && !y) continue;
    el.dataset.panId = String(++n);
    const cx = Math.min(Math.max(r.left + r.width / 2, 5), innerWidth - 5);
    const cy = Math.min(Math.max(r.top + Math.min(r.height / 2, 120), 5), innerHeight - 5);
    out.push({ sel: '[data-pan-id="' + n + '"]', name: el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
      (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\\s+/)[0] : ""),
      axis: y ? "y" : "x", x: cx, y: cy, touchAction: cs.touchAction,
      hit: (document.elementFromPoint(cx, cy) || {}).tagName, size: [el.scrollWidth, el.clientWidth, el.scrollHeight, el.clientHeight] });
  }
  return out;
})()`;
const pos = (sel) => `(() => { const el = ${JSON.stringify(sel)} === "document" ? document.scrollingElement : document.querySelector(${JSON.stringify(sel)});
  return el ? [el.scrollLeft, el.scrollTop] : null; })()`;

async function pan(s) {
  const before = await evaluate(pos(s.sel));
  // Raw touch points, as the digitizer sends them: down, twelve moves, up.
  // They pass the page's touch listeners and touch-action like a finger.
  // Where the box is now: an earlier pan may have moved it (a sticky bar).
  if (s.sel !== "document") {
    const c = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(s.sel)}).getBoundingClientRect();
      return [Math.min(Math.max(r.left + r.width / 2, 5), innerWidth - 5), Math.min(Math.max(r.top + Math.min(r.height / 2, 120), 5), innerHeight - 5)]; })()`);
    [s.x, s.y] = c;
  }
  // A box already scrolled to its end (a carousel showing the chosen card) is panned back.
  const atEnd = await evaluate(`(() => { const el = ${JSON.stringify(s.sel)} === "document" ? document.scrollingElement : document.querySelector(${JSON.stringify(s.sel)});
    return ${JSON.stringify(s.axis)} === "y" ? el.scrollTop >= el.scrollHeight - el.clientHeight - 2 : el.scrollLeft >= el.scrollWidth - el.clientWidth - 2; })()`);
  const dir = atEnd ? -1 : 1;
  if (process.env.PAN_PRE) await evaluate(process.env.PAN_PRE);
  const x0 = Math.round(s.x), y0 = Math.round(s.y);
  const at = (k) => s.axis === "y" ? { x: x0, y: y0 - dir * k * 30 } : { x: x0 - dir * k * 30, y: y0 };
  if (MOUSE) {
    // A touch screen in mouse mode: the finger is a left-button drag (static/pan.js).
    const m = (type, p, extra = {}) => send("Input.dispatchMouseEvent", { type, ...p, button: "left", ...extra });
    await m("mouseMoved", at(0), { buttons: 0, button: "none" });
    await m("mousePressed", at(0), { buttons: 1, clickCount: 1 });
    for (let k = 1; k <= 12; k++) { await m("mouseMoved", at(k), { buttons: 1 }); await sleep(16); }
    await m("mouseReleased", at(12), { buttons: 0, clickCount: 1 });
  } else {
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ ...at(0), id: 1 }] });
    for (let k = 1; k <= 12; k++) {
      await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ ...at(k), id: 1 }] });
      await sleep(16);
    }
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  }
  await sleep(500);
  const after = await evaluate(pos(s.sel));
  if (process.env.PAN_DEBUG) s.debug = await evaluate(process.env.PAN_DEBUG);
  const moved = before && after && (after[0] !== before[0] || after[1] !== before[1]);
  return { ...s, before, after, moved: !!moved };
}
async function pinchZooms() {
  await send("Input.synthesizePinchGesture", { x: W / 2, y: H / 2, scaleFactor: 2, gestureSourceType: "touch" });
  await sleep(400);
  return await evaluate("visualViewport.scale");
}

const PAGES = (opt("pages", "/admin,/admin/vorlagen,/admin/hilfe")).split(",");
const report = { base, size: [W, H], pages: [] };
let bad = 0;
for (const p of PAGES) {
  // "/admin/vorlagen@#neu": open the page, then tap #neu (a state behind a button).
  const [url, click] = p.split("@");
  await open(base + url);
  await login();
  if (click) { await evaluate(`document.querySelector(${JSON.stringify(click)}).click()`); await sleep(1200); }
  const page = { path: p, scrollers: [] };
  for (const s of await evaluate(FIND)) {
    const r = await pan(s);
    page.scrollers.push(r);
    if (!r.moved) bad++;
  }
  page.pinchScale = await pinchZooms();
  if (page.pinchScale > 1.01) bad++;
  if (shots) {
    const img = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(out, p.replace(/\W+/g, "_") + ".png"), Buffer.from(img.result.data, "base64"));
  }
  report.pages.push(page);
  console.log(p, page.scrollers.map((s) => `${s.name || s.sel}:${s.axis}:${s.moved ? "moved" : "STUCK"}`).join(" "),
    "pinch", page.pinchScale);
}
report.bad = bad;
fs.writeFileSync(path.join(out, "touch-pan.json"), JSON.stringify(report, null, 2));
chrome.kill();
process.exit(bad ? 1 : 0);
