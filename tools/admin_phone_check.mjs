#!/usr/bin/env node
// Render the admin pages at phone widths in headless Chrome and measure what
// a review keeps finding by eye: sideways scrolling, things cut off at the
// right edge, buttons too small to hit with a thumb, tiny text, inputs that
// make Safari zoom, siblings drawn on top of each other.
//
//   node tools/admin_phone_check.mjs /tmp/admin-phone-data/pages.json \
//        --out /tmp/admin-phone-shots [--widths 360,390,430] [--themes light,dark]
//        [--only events,templates] [--no-shots] [--desktop] [--expand] [--eval "js"]
//   node tools/admin_phone_check.mjs /tmp/guest-phone-data/pages.json --select-touch [--sizes 390x844,360x740]
//        (selecting photos by touch in the gallery, see "Selecting by touch" in the doc)
//   node tools/admin_phone_check.mjs --kiosk-keys http://127.0.0.1:8774 --out /tmp/kiosk-keys
//        [--sizes 1707x960] [--expect-arrows off]
//        (the booth's kiosk with the keyboard alone, see "Kiosk with the arrow keys" in the doc)
//
// pages.json comes from tools/admin_phone_seed.py. Needs Node 22+ (built-in
// WebSocket and fetch) and Google Chrome or Chromium; no npm packages.
// Writes <out>/gates.json and, unless --no-shots, one PNG per page, width and
// theme named <page>-<width>-<theme>.png. Exit code 1 if a hard gate fails at
// the narrowest width. See docs/ADMIN-PHONE-CHECK.md.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : dflt; };
const flag = (name) => args.includes(`--${name}`);
// --kiosk-keys <booth url>: no pages.json, the kiosk of a running booth.
const kioskKeys = opt("kiosk-keys", "");
const cfg = kioskKeys ? { base: kioskKeys, pages: {}, cookie: "" } : JSON.parse(fs.readFileSync(args[0], "utf8"));
const out = opt("out", "/tmp/admin-phone-shots");
const desktop = flag("desktop");
const HEIGHTS = { 360: 740, 390: 844, 430: 932 };
// --sizes 360x740,844x390: explicit viewports, landscape included. A size
// whose height is under 500 px is a phone held sideways, not a desk.
const sizes = opt("sizes", "") ? opt("sizes").split(",").map((s) => s.split("x").map(Number)) : null;
if (sizes) for (const [w, h] of sizes) HEIGHTS[w] = h;
const widths = sizes ? sizes.map(([w]) => w) : (opt("widths", desktop ? "1440" : "360,390,430")).split(",").map(Number);
const themes = opt("themes", "light,dark").split(",");
const only = opt("only", "") ? opt("only").split(",") : Object.keys(cfg.pages);
const shots = !flag("no-shots");
// --guest: guest pages. Small targets and zooming inputs fail the run too,
// and the first screen is measured: is a photo in it, how much do sticky bars take.
const guest = flag("guest");
// --first-screen: the screenshot is what the phone shows before scrolling.
const firstScreen = flag("first-screen");
fs.mkdirSync(out, { recursive: true });

const CHROME = [process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find((p) => p && fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME=/path/to/chrome"); process.exit(2); }
const port = 9600 + Math.floor(Math.random() * 300);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "admin-phone-chrome-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--hide-scrollbars", "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
process.on("exit", () => { try { chrome.kill(); } catch {} });

async function waitForChrome() {
  for (let i = 0; i < 50; i++) {
    try { return await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Chrome did not start");
}
await waitForChrome();
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let seq = 0; const pending = new Map(); const waiters = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  if (d.method) for (const w of waiters.splice(0)) w(d);
};
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
  return r.result.result.value;
}
await send("Page.enable");
await send("Network.enable");
if (cfg.cookie) await send("Network.setCookie", { name: "session", value: cfg.cookie, url: cfg.base + "/" });

// Runs inside the page. Returns counts and the first offenders of each gate.
const MEASURE = `(() => {
  const W = __WIDTH__;   // the width asked for: a mobile layout viewport grows to fit content that is too wide
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    const cls = (el.getAttribute("class") || "").trim().split(/\\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) s += "." + cls.join(".");
    const txt = (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().replace(/\\s+/g, " ").slice(0, 32);
    // Where it sits: the nearest ancestor with an id, so a report line can be found.
    const home = el.parentElement && el.parentElement.closest("[id]");
    const at = home && home.id ? " @" + home.id : "";
    return (txt ? s + " «" + txt + "»" : s) + at;
  };
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) return false;
    if (el.closest("[hidden], [inert], [aria-hidden=true], details:not([open]) > :not(summary)")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0;   // left of the screen = an off-canvas menu that is shut
  };
  const scroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === "auto" || ox === "scroll") return p;
    }
    return null;
  };
  const all = [...document.body.querySelectorAll("*")].filter((el) => !["SCRIPT", "STYLE", "TEMPLATE", "OPTION", "BR", "svg", "path"].includes(el.tagName) && !(el instanceof SVGElement && el.tagName !== "svg"));
  const vis = all.filter(shown);
  const res = { width: W, scrollWidth: document.documentElement.scrollWidth, pageScroll: document.documentElement.scrollWidth > W + 0.5 };
  // 1. crossing the right edge (outside a scroll container of its own)
  const cross = vis.filter((el) => { const r = el.getBoundingClientRect(); return r.right > W + 0.5 && !scroller(el); });
  // report the outermost offenders only
  res.crossRight = cross.filter((el) => !cross.includes(el.parentElement)).map(name);
  // 2. touch targets
  const sel = "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex='-1'])";
  const targets = vis.filter((el) => el.matches(sel) && !el.closest("[data-check-skip]"));
  const boxOf = (el) => {
    let r = el.getBoundingClientRect();
    if (el.matches("input[type=checkbox], input[type=radio], input[type=file]")) {
      const lab = el.closest("label") || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
      if (lab && shown(lab)) r = lab.getBoundingClientRect();
    }
    return r;
  };
  // A sticky bar or a fixed layer lies over the page on purpose.
  const floating = (el) => { for (let p = el; p && p !== document.body; p = p.parentElement) { const ps = getComputedStyle(p).position; if (ps === "sticky" || ps === "fixed") return p; } return null; };
  const inline = (el) => el.tagName === "A" && getComputedStyle(el).display === "inline" && el.parentElement &&
     [...el.parentElement.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 2);
  const small = []; const inlineSmall = [];
  const boxes = targets.map((el) => [el, boxOf(el)]);
  for (const [el, r] of boxes) {
    const big = Math.max(r.width, r.height), lit = Math.min(r.width, r.height);
    // Guest pages (--guest): 44 px in both directions, the size the brief asks for on touch.
    if (__STRICT__ ? lit < 43.5 : (big < 43.5 || lit < 23.5)) (inline(el) ? inlineSmall : small).push(name(el) + " " + Math.round(r.width) + "x" + Math.round(r.height));
  }
  res.smallTargets = small; res.inlineLinksSmall = inlineSmall.length;
  // spacing: two separate targets closer than 8 px (neither inside the other)
  const close = [];
  const tb = boxes.filter(([el]) => !inline(el));
  for (let i = 0; i < tb.length; i++) for (let j = i + 1; j < tb.length; j++) {
    const [a, ra] = tb[i], [b, rb] = tb[j];
    if (a.contains(b) || b.contains(a) || floating(a) !== floating(b)) continue;
    const lab = (x) => x.closest("label");
    if (lab(a) && lab(a) === lab(b)) continue;
    const gx = Math.max(0, rb.left - ra.right, ra.left - rb.right);
    const gy = Math.max(0, rb.top - ra.bottom, ra.top - rb.bottom);
    if (gx < 7.5 && gy < 7.5 && !(gx === 0 && gy === 0 && (ra.width * ra.height === 0))) close.push(name(a) + " | " + name(b) + " gap " + Math.round(Math.max(gx, gy)));
  }
  res.tooClose = close;
  // 3. text size
  const tiny = []; const smallText = [];
  for (const el of vis) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || el.closest("[data-check-skip]")) continue;   // miniature previews are pictures, not text
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 11.5) tiny.push(name(el) + " " + fs + "px");
    else if (fs < 13.95 && !el.closest("label, legend, th, dt, small, .badge, .form-label, .form-hint, .form-help, .text-xs, .text-muted, [class*=label], [class*=meta], [class*=hint], [class*=pill], code, kbd")) smallText.push(name(el) + " " + fs + "px");
  }
  res.tinyText = tiny; res.smallText = smallText;
  // 4. inputs below 16 px make Safari zoom in on focus
  res.zoomInputs = vis.filter((el) => el.matches("input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=range]):not([type=color]):not([type=file]):not([type=submit]):not([type=button]), select, textarea") && parseFloat(getComputedStyle(el).fontSize) < 15.95).map(name);
  // 5. siblings drawn over each other (in-flow siblings only)
  const overl = [];
  for (const parent of vis) {
    // Inline runs that wrap share line boxes; only boxes laid out as blocks can collide.
    const kids = [...parent.children].filter((k) => shown(k) && !["absolute", "fixed", "sticky"].includes(getComputedStyle(k).position) && getComputedStyle(k).display !== "inline");
    for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
      const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
      const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left), oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (ox > 2 && oy > 2) overl.push(name(kids[i]) + " / " + name(kids[j]));
    }
  }
  res.overlaps = overl;
  // 6. the first screen (guest pages): a photo in it, and what the sticky bars cover
  const H = innerHeight;
  const photoSel = __PHOTOSEL__;
  if (photoSel) {
    const ph = [...document.querySelectorAll(photoSel)].filter(shown);
    res.photos = ph.length;
    res.firstPhotoVisible = ph.some((el) => { const r = el.getBoundingClientRect(); return r.top < H - 40 && r.bottom > 0; });
    const tops = ph.map((el) => el.getBoundingClientRect().top + scrollY);
    res.firstPhotoTop = tops.length ? Math.round(Math.min(...tops)) : null;
    res.mediaLoading = [...document.querySelectorAll("video")].filter((v) => v.preload !== "none" || v.autoplay).length;
    res.autoplay = [...document.querySelectorAll("video[autoplay]")].length;
  }
  // Scrolled down, the bars that stay at the top form one band from 0 to the
  // lowest bottom edge among them; that band is what the photos lose.
  const y0 = scrollY;
  scrollTo({ top: Math.min(900, document.documentElement.scrollHeight - H), behavior: "instant" });
  let band = 0;
  res.scrolledTo = Math.round(scrollY);
  for (const el of vis) {
    const ps = getComputedStyle(el).position;
    if (ps !== "sticky" && ps !== "fixed") continue;
    const r = el.getBoundingClientRect();
    if (r.height > 0 && r.height < H / 2 && r.top < 140 && r.bottom > 0 && shown(el)) band = Math.max(band, r.bottom);
  }
  scrollTo({ top: y0, behavior: "instant" });
  res.stickyPx = Math.round(band);
  res.stickyShare = Math.round(band * 100 / H);
  return res;
})()`;

async function load(url) {
  const loaded = new Promise((resolve) => {
    const t = setTimeout(resolve, 15000);
    const hook = (d) => { if (d.method === "Page.loadEventFired") { clearTimeout(t); resolve(); } else waiters.push(hook); };
    waiters.push(hook);
  });
  await send("Page.navigate", { url });
  await loaded;
  await sleep(500);
}

// ── --select-touch: picking several photos with a finger ──────────────
// Real touch input over CDP (touchStart/touchMove/touchEnd, not clicks), at
// phone sizes, on the gallery pages of tools/guest_phone_seed.py, as a guest
// ("Meine Auswahl") and as a logged-in client (the pick dots). Writes
// <out>/select-touch.json and a screenshot per run; exit 1 if a gate fails.
// See docs/ADMIN-PHONE-CHECK.md, "Selecting by touch".
async function finger(type, x, y) {
  await send("Input.dispatchTouchEvent", { type,
    touchPoints: type === "touchEnd" ? [] : [{ x, y, id: 1, radiusX: 5, radiusY: 5, force: 1 }] });
}
async function tap(x, y, { move = 0, hold = 50 } = {}) {
  await finger("touchStart", x, y);
  if (move) { await sleep(15); await finger("touchMove", x + move * 0.6, y + move * 0.8); }
  await sleep(hold);
  await finger("touchEnd", x + move * 0.6, y + move * 0.8);
}
async function drag(x, y, dy, steps = 12, every = 16) {
  await finger("touchStart", x, y);
  for (let i = 1; i <= steps; i++) { await finger("touchMove", x, y - dy * i / steps); await sleep(every); }
  await finger("touchEnd", x, y - dy);
}
// In the page: count large-view openings, context menus and every change of
// a tile's selected state, so a double toggle (on, then off by a ghost
// click) shows even when the end state looks right.
const PROBE = `(() => {
  const pb = window.__pb = { opens: 0, ctx: 0, flips: {} };
  const lb = document.getElementById("lightbox");
  let was = false;
  if (lb) new MutationObserver(() => { const now = lb.classList.contains("open"); if (now && !was) pb.opens++; was = now; })
    .observe(lb, { attributes: true, attributeFilter: ["class"] });
  document.addEventListener("contextmenu", () => pb.ctx++, true);
  const on = (el) => el.classList.contains("is-selected") || el.classList.contains("is-picked");
  document.querySelectorAll(".photo-item").forEach((el) => {
    let st = on(el);
    new MutationObserver(() => { const n = on(el); if (n !== st) { st = n; (pb.flips[el.dataset.id] ||= []).push(Math.round(performance.now())); } })
      .observe(el, { attributes: true, attributeFilter: ["class"] });
  });
  return 1;
})()`;
const STATE = `(() => ({
  selected: document.querySelectorAll(".photo-item.is-selected, .photo-item.is-picked").length,
  opens: __pb.opens, ctx: __pb.ctx, y: Math.round(scrollY), text: String(getSelection()).length,
  flips: Object.values(__pb.flips).reduce((a, f) => a + f.length, 0),
  doubles: Object.values(__pb.flips).filter((f) => f.some((t, i) => i && t - f[i - 1] < 700)).length,
  lbOpen: !!document.querySelector("#lightbox.open") }))()`;
// Mutation records arrive after the clearing, so wait for them before the
// counters go back to zero - or the clearing counts as the next step's toggles.
const RESET = `(async () => { if (document.querySelector("#lightbox.open")) document.getElementById("lbClose").click();
  if (window.clearSelection) clearSelection(); await new Promise((z) => setTimeout(z, 60));
  __pb.flips = {}; __pb.opens = 0; __pb.ctx = 0; getSelection().removeAllRanges(); return 1; })()`;
// Puts tile i in the middle of the screen and returns its box, plus whether
// the point asked for (fx, fy as fractions, or px from the edge if > 1)
// really lands on the tile and not on a bar or a button lying over it.
const tileAt = (i, fx = 0.5, fy = 0.5) => evaluate(`(() => {
  const el = [...document.querySelectorAll(".photo-item")].filter((e) => e.style.display !== "none")[${i}];
  if (!el) return null;
  el.scrollIntoView({ block: "center", behavior: "instant" });
  const px = (f, a, len) => f > 1 ? a + f : f < 0 ? a + len + f : a + len * f;
  let r = el.getBoundingClientRect();
  // A tile taller than the screen (a phone held sideways): bring the point
  // itself to the middle, not the tile, or it lands under the nav.
  const y1 = px(${fy}, r.top, r.height);
  if (y1 < 120 || y1 > innerHeight - 120) { scrollBy({ top: y1 - innerHeight / 2, behavior: "instant" }); r = el.getBoundingClientRect(); }
  const x = px(${fx}, r.left, r.width), y = px(${fy}, r.top, r.height);
  const hit = document.elementFromPoint(x, y);
  return { id: el.dataset.id, x, y, w: r.width, h: r.height, onTile: !!hit && hit.closest(".photo-item") === el,
           hit: hit ? hit.tagName.toLowerCase() + "." + (hit.getAttribute("class") || "").split(" ")[0] : "" };
})()`);
// What share of the tile a finger can hit without landing on a button or a bar.
const COVER = `(() => {
  const el = [...document.querySelectorAll(".photo-item")].filter((e) => e.style.display !== "none")[0];
  el.scrollIntoView({ block: "center", behavior: "instant" });
  const r = el.getBoundingClientRect(); let ok = 0, n = 0;
  for (let i = 1; i < 10; i++) for (let j = 1; j < 10; j++) {
    const py = r.top + r.height * j / 10;
    if (py < 80 || py > innerHeight - 80) continue;   // off screen or under a bar: a tile taller than the screen
    const hit = document.elementFromPoint(r.left + r.width * i / 10, py); n++;
    if (hit && hit.closest(".photo-item") === el && !hit.closest("button:not(.photo-select-cb), a:not(.photo-open)")) ok++;
  }
  return Math.round(ok * 100 / n);
})()`;
const BAR = `(() => {
  scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  const bar = document.getElementById("selectionBar");
  if (!bar) return null;
  const b = bar.getBoundingClientRect(), H = innerHeight;
  const tiles = [...document.querySelectorAll(".photo-item")].filter((e) => e.style.display !== "none");
  // Scrolled to the very end, the last tiles must lie clear of the bar.
  const end = Math.max(...tiles.map((t) => t.getBoundingClientRect().bottom));
  const under = tiles.filter((t) => { const q = t.getBoundingClientRect();
    return q.bottom > end - 60 && q.bottom > b.top + 1 && q.top < b.bottom - 1; }).length;
  // A bar at the top lets the tiles scroll under it on purpose; only a bar
  // at the bottom can hide the last row for good.
  const cs = getComputedStyle(bar);
  return { position: cs.position, top: Math.round(b.top), bottom: Math.round(b.bottom), H,
           inView: b.top >= 0 && b.bottom <= H + 0.5, atBottom: b.top > H / 2, coversLastRow: b.top > H / 2 && under > 0, tilesUnder: under };
})()`;

async function selectTouch() {
  const pagesWanted = opt("only", "") ? opt("only").split(",") : ["gallery", "gallery-grid", "gallery-list"];
  const sizesWanted = sizes || [[390, 844], [360, 740]];
  const report = {}; const why = [];
  for (const theme of themes) {
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
    const ts = (await send("Page.addScriptToEvaluateOnNewDocument", {
      source: `try { localStorage.setItem("pb-theme", ${JSON.stringify(theme)}); } catch (e) {}` })).result.identifier;
    for (const [w, h] of sizesWanted) {
      await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: true });
      for (const role of ["guest", "client"]) {
        for (const page of (role === "client" ? ["gallery"] : pagesWanted)) {
          if (!cfg.pages[page]) continue;
          if (role === "client" && !cfg.clientCookie) continue;
          await send("Network.setCookie", { name: "session", value: role === "client" ? cfg.clientCookie : cfg.cookie, url: cfg.base + "/" });
          await load(cfg.base + cfg.pages[page]);
          await evaluate(PROBE);
          const key = `${role}:${page}@${w}x${h}:${theme}`;
          const shotName = path.join(out, `select-${role}-${page}-${w}x${h}-${theme}.png`);
          const r = report[key] = role === "client" ? await clientRun(shotName) : await guestRun(shotName);
          for (const g of r.gates) why.push(`${key} ${g}`);
          console.log(key, JSON.stringify(r.summary));
        }
      }
    }
    await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: ts });
  }
  fs.writeFileSync(path.join(out, "select-touch.json"), JSON.stringify(report, null, 1));
  for (const line of why) console.log("GATE", line);
  console.log(why.length ? `SELECT-TOUCH GATE FAILED (${why.length})` : "select-touch: all gates pass");
  ws.close(); chrome.kill(); await sleep(300);
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5 }); } catch {}
  return why.length ? 1 : 0;
}

async function screenshot(file) {
  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(file, Buffer.from(shot.result.data, "base64"));
}

async function guestRun(shotName) {
  const r = { gates: [] }; const s = {};
  const toggle = await evaluate(`(() => { const b = document.getElementById("selectToggle"); if (!b) return null;
    b.scrollIntoView({ block: "center", behavior: "instant" }); const q = b.getBoundingClientRect();
    return { x: q.left + q.width / 2, y: q.top + q.height / 2 }; })()`);
  if (!toggle) { r.gates.push("no select button"); r.summary = { error: "no #selectToggle" }; return r; }
  // Enter selection mode by touch, then tap a tile at once.
  await tap(toggle.x, toggle.y); await sleep(30);
  let t = await tileAt(0); await tap(t.x, t.y); await sleep(250);
  r.rightAfterEnter = await evaluate(STATE);
  s.afterEnter = r.rightAfterEnter.selected;
  // 1. Ten taps on ten tiles, in the middle of each.
  await evaluate(RESET);
  let jumps = 0;
  for (let i = 0; i < 10; i++) {
    t = await tileAt(i); if (!t) break; await sleep(60);
    const y0 = (await evaluate(STATE)).y;
    await tap(t.x, t.y); await sleep(160);
    const st = await evaluate(STATE);
    if (Math.abs(st.y - y0) > 2) jumps++;
  }
  r.tenTaps = { ...(await evaluate(STATE)), jumps, at: await evaluate("location.pathname") };
  if (!(await tileAt(0))) { r.gates.push(`no tiles left after ten taps (page now ${r.tenTaps.at})`); r.summary = { ten: r.tenTaps }; return r; }
  // The screenshot: four tiles chosen, the bar with its count in view.
  await tileAt(3); await sleep(250); await screenshot(shotName);
  s.ten = `${r.tenTaps.selected}/10 opens ${r.tenTaps.opens} jumps ${jumps} doubles ${r.tenTaps.doubles}`;
  // 2. On the check box itself (top left corner), five tiles.
  await evaluate(RESET);
  for (let i = 0; i < 5; i++) { t = await tileAt(i, 24, 24); await tap(t.x, t.y); await sleep(160); }
  r.checkbox = await evaluate(STATE); s.checkbox = `${r.checkbox.selected}/5`;
  // 3. On the edges: left, right, top, bottom, 4 px in.
  await evaluate(RESET);
  const edges = [[4, 0.5], [-4, 0.5], [0.5, 4], [0.5, -4]];
  r.edgeHits = [];
  for (let i = 0; i < 4; i++) { t = await tileAt(i, ...edges[i]); r.edgeHits.push(t.onTile ? "tile" : t.hit); await tap(t.x, t.y); await sleep(160); }
  r.edges = await evaluate(STATE);
  r.edgeFlips = await evaluate(`JSON.stringify(__pb.flips)`);
  s.edges = `${r.edges.selected}/4 ${r.edgeHits.join(",")} ${r.edgeFlips}`;
  // 3b. Bottom right corner, where share, download, heart and the colour
  // flag sit outside selection mode.
  await evaluate(RESET);
  for (let i = 0; i < 2; i++) { t = await tileAt(i, -22, -22); await tap(t.x, t.y); await sleep(200); }
  r.corner = await evaluate(STATE); s.corner = `${r.corner.selected}/2`;
  // 4. Two taps on two tiles within 300 ms.
  await evaluate(RESET);
  // Near the bottom of tile 1, then any other tile with a part on screen (the next one, sideways).
  const a = await tileAt(1, 0.5, -30); const b = await evaluate(`(() => { const el = [...document.querySelectorAll(".photo-item")].find((e) => {
      const q = e.getBoundingClientRect(), c = (Math.max(q.top, 90) + Math.min(q.bottom, innerHeight - 90)) / 2;
      return e.dataset.id !== "${a.id}" && Math.min(q.bottom, innerHeight - 90) - Math.max(q.top, 90) > 40 && document.elementFromPoint(q.left + q.width / 2, c)?.closest(".photo-item") === e; });
    if (!el) return null; const q = el.getBoundingClientRect();
    return { x: q.left + q.width / 2, y: (Math.max(q.top, 90) + Math.min(q.bottom, innerHeight - 90)) / 2 }; })()`);
  r.quickPairSecond = !!b;
  if (!b) r.gates.push("no second tile on screen for the quick pair (check, not page)");
  await tap(a.x, a.y, { hold: 30 }); await sleep(90); if (b) await tap(b.x, b.y, { hold: 30 }); await sleep(400);
  r.quickPair = await evaluate(STATE); s.quickPair = `${r.quickPair.selected}/2`;
  // 5. A finger that moves a little while tapping (6 and 10 px).
  await evaluate(RESET);
  t = await tileAt(2); await tap(t.x, t.y, { move: 6 }); await sleep(200);
  t = await tileAt(3); await tap(t.x, t.y, { move: 10 }); await sleep(200);
  r.jitter = await evaluate(STATE); s.jitter = `${r.jitter.selected}/2`;
  // 6. Scrolling by dragging over the tiles must not toggle anything.
  await evaluate(RESET);
  t = await tileAt(4); const y0 = (await evaluate(STATE)).y;
  await drag(t.x, t.y, 260); await sleep(500);
  await drag(t.x, t.y - 40, -200); await sleep(500);
  r.dragScroll = { ...(await evaluate(STATE)), moved: (await evaluate(STATE)).y !== y0 };
  s.drag = `toggles ${r.dragScroll.flips} moved ${r.dragScroll.moved}`;
  // 7. A tap while the page still glides after a fast flick.
  await evaluate(RESET);
  t = await tileAt(6); await drag(t.x, t.y, 300, 4, 8); await sleep(40);
  t = await evaluate(`(() => ({ x: innerWidth / 4, y: innerHeight / 2 }))()`);
  await tap(t.x, t.y); await sleep(400);
  r.flickTap = await evaluate(STATE); s.flickTap = `toggles ${r.flickTap.flips} opens ${r.flickTap.opens}`;
  // 8. Long press (900 ms) on a tile.
  await evaluate(RESET);
  t = await tileAt(5); await tap(t.x, t.y, { hold: 900 }); await sleep(300);
  r.longPress = await evaluate(STATE); s.longPress = `sel ${r.longPress.selected} ctx ${r.longPress.ctx} text ${r.longPress.text}`;
  // 9. Hit area and how the selected state is told.
  await evaluate(RESET);
  r.cover = await evaluate(COVER); s.cover = r.cover + "%";
  t = await tileAt(0); await tap(t.x, t.y); await sleep(200);
  r.marked = await evaluate(`(() => { const el = document.querySelector(".photo-item.is-selected"); if (!el) return null;
    const ctl = el.querySelector("[aria-pressed], input[type=checkbox]");
    const mark = el.querySelector(".photo-select-cb, .select-mark"); const m = mark ? mark.getBoundingClientRect() : null;
    return { state: ctl ? (ctl.getAttribute("aria-pressed") || String(ctl.checked)) : null,
             name: ctl ? (ctl.getAttribute("aria-label") || "") : "", mark: m ? Math.round(Math.min(m.width, m.height)) : 0,
             counter: (document.getElementById("selectionCount") || {}).textContent || "" }; })()`);
  s.marked = r.marked;
  // 10. The bar at the end of a long gallery.
  r.bar = await evaluate(BAR); s.bar = r.bar && `${r.bar.position} inView ${r.bar.inView} coversLast ${r.bar.coversLastRow}`;
  // 11. "Alle" under a filter, then back to everything.
  await evaluate(RESET);
  r.filterAll = await evaluate(`(async () => {
    const f = [...document.querySelectorAll("button.filter-btn")].find((b) => b.dataset.filter && b.dataset.filter !== "all");
    if (!f) return null; f.click(); await new Promise((z) => setTimeout(z, 100));
    const visible = [...document.querySelectorAll(".photo-item")].filter((e) => e.style.display !== "none").length;
    selectAllVisible(); const selected = document.querySelectorAll(".photo-item.is-selected").length;
    document.querySelector('button.filter-btn[data-filter="all"]').click(); await new Promise((z) => setTimeout(z, 100));
    const after = document.querySelectorAll(".photo-item.is-selected").length;
    return { filter: f.dataset.filter, visible, selected, afterAll: after, counter: (document.getElementById("downloadCount") || {}).textContent };
  })()`);
  s.filterAll = r.filterAll;
  // 12. Leaving selection mode clears it, and the ZIP link answers.
  r.zip = await evaluate(`(async () => {
    const ids = [...document.querySelectorAll(".photo-item")].slice(0, 2).map((e) => e.dataset.id).join(",");
    const slug = location.pathname.split("/")[2];
    const res = await fetch("/gallery/" + slug + "/selection.zip?ids=" + ids + "&size=original");
    return { status: res.status, type: res.headers.get("content-type") };
  })()`);
  await evaluate(`toggleSelectMode()`);
  r.leave = await evaluate(STATE); s.leave = `sel ${r.leave.selected}`;
  s.zip = r.zip;
  // The gates (docs/ADMIN-PHONE-CHECK.md).
  if (r.tenTaps.selected !== 10) r.gates.push(`ten taps selected ${r.tenTaps.selected}, want 10`);
  if (r.tenTaps.opens) r.gates.push(`ten taps opened the large view ${r.tenTaps.opens}x`);
  if (r.tenTaps.jumps) r.gates.push(`ten taps scrolled the page ${r.tenTaps.jumps}x`);
  for (const k of ["tenTaps", "checkbox", "edges", "quickPair", "jitter", "longPress"]) if (r[k].doubles) r.gates.push(`${k}: double toggle`);
  if (r.checkbox.selected !== 5) r.gates.push(`taps on the check mark selected ${r.checkbox.selected}, want 5`);
  if (r.edges.selected !== 4) r.gates.push(`taps on the tile edges selected ${r.edges.selected}, want 4`);
  if (r.quickPair.selected !== 2) r.gates.push(`two quick taps selected ${r.quickPair.selected}, want 2`);
  if (r.jitter.selected !== 2) r.gates.push(`taps with a little movement selected ${r.jitter.selected}, want 2`);
  if (r.dragScroll.flips) r.gates.push(`dragging to scroll toggled ${r.dragScroll.flips} tiles`);
  if (r.rightAfterEnter.selected !== 1) r.gates.push("a tap right after entering selection mode did not select");
  if (r.cover < 100) r.gates.push(`only ${r.cover}% of a tile selects (a button lies over the rest)`);
  if (r.corner.selected !== 2) r.gates.push(`taps on the button corner selected ${r.corner.selected}, want 2`);
  if (r.longPress.ctx || r.longPress.text) r.gates.push("long press opened a menu or selected text");
  if (r.bar && !r.bar.inView) r.gates.push("selection bar out of reach at the end of the gallery");
  if (r.bar && r.bar.coversLastRow) r.gates.push("selection bar covers the last row");
  if (r.leave.selected) r.gates.push("leaving selection mode kept a selection");
  r.summary = s;
  return r;
}

async function clientRun(shotName) {
  const r = { gates: [] }; const s = {};
  const api = await evaluate(`(document.getElementById("clientPicks") || {}).dataset ? document.getElementById("clientPicks").dataset.api : null`);
  if (!api) { r.gates.push("no client pick bar"); r.summary = { error: "no #clientPicks" }; return r; }
  // Start from a client with no selection at all (the first pick makes one).
  const removed = await evaluate(`(async () => { const j = await (await fetch(${JSON.stringify(api)})).json();
    for (const x of j.selections) await fetch(${JSON.stringify(api)} + "/" + x.id + "/items", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delete: true }) });
    return j.selections.length; })()`);
  if (removed) { await send("Page.reload"); await sleep(1500); await evaluate(PROBE); }
  // A phone on mobile data: every answer takes 400 ms. On the desk the
  // server answers before the next tap, which hides every race.
  await send("Network.emulateNetworkConditions", { offline: false, latency: 400, downloadThroughput: -1, uploadThroughput: -1 });
  // Ten pick dots, 90 ms apart, as fast as a thumb goes down a grid.
  for (let i = 0; i < 10; i++) {
    const p = await evaluate(`(() => { const el = [...document.querySelectorAll(".photo-item")][${i}]; el.scrollIntoView({ block: "center", behavior: "instant" });
      const d = el.querySelector(".pick-dot").getBoundingClientRect(); return { x: d.left + d.width / 2, y: d.top + d.height / 2, w: d.width, h: d.height }; })()`);
    if (i === 0) r.dot = { w: Math.round(p.w), h: Math.round(p.h) };
    await tap(p.x, p.y, { hold: 30 }); await sleep(90);
  }
  // Seen at once (before the server answered) and after it did.
  r.seenAtOnce = (await evaluate(STATE)).selected;
  await screenshot(shotName);
  // The server state once the requests have settled (two equal reads in a row, at most 12 s).
  const read = () => evaluate(`(async () => { const j = await (await fetch(${JSON.stringify(api)}, { credentials: "same-origin" })).json();
    return { selections: j.selections.length, counts: j.selections.map((x) => x.media_ids.length) }; })()`);
  let last = null;
  for (let i = 0; i < 12; i++) { await sleep(1000); const now = JSON.stringify(await read()); if (now === last) break; last = now; }
  r.server = JSON.parse(last);
  r.after = await evaluate(STATE);
  // The same dot twice, quickly: back to where it was.
  const p = await evaluate(`(() => { const el = [...document.querySelectorAll(".photo-item")][12]; el.scrollIntoView({ block: "center", behavior: "instant" });
    const d = el.querySelector(".pick-dot").getBoundingClientRect(); return { x: d.left + d.width / 2, y: d.top + d.height / 2, id: el.dataset.id }; })()`);
  await tap(p.x, p.y, { hold: 30 }); await sleep(120); await tap(p.x, p.y, { hold: 30 }); await sleep(2500);
  r.twice = await evaluate(`(async () => { const j = await (await fetch(${JSON.stringify(api)}, { credentials: "same-origin" })).json();
    return { onServer: j.selections.some((x) => x.media_ids.includes(${p.id})), onTile: document.querySelector('.photo-item[data-id="${p.id}"]').classList.contains("is-picked") }; })()`);
  await send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  if (r.seenAtOnce < 9) r.gates.push(`only ${r.seenAtOnce} of 10 picks shown before the server answered`);
  s.dot = r.dot; s.seenAtOnce = r.seenAtOnce; s.picked = r.after.selected; s.opens = r.after.opens; s.server = r.server; s.twice = r.twice;
  if (r.after.selected !== 10) r.gates.push(`ten pick dots picked ${r.after.selected}, want 10`);
  if (r.after.opens) r.gates.push(`pick dots opened the large view ${r.after.opens}x`);
  if (r.server.selections !== 1) r.gates.push(`fast picks made ${r.server.selections} selections, want 1`);
  if (r.server.counts[0] !== 10) r.gates.push(`server holds ${r.server.counts.join("+")} picks, want 10`);
  if (r.twice.onServer || r.twice.onTile) r.gates.push("two quick taps on one dot left it picked");
  if (Math.min(r.dot.w, r.dot.h) < 44) r.gates.push(`pick dot tap area ${r.dot.w}x${r.dot.h}, want 44`);
  r.summary = s;
  return r;
}

if (flag("select-touch")) process.exit(await selectTouch());

// ── --kiosk-keys: the guest screens with the keyboard alone ──────────
// Drives a running booth's kiosk (mock camera) with key events only:
// start -> a mode by the arrows -> Enter -> set-up: change the format and
// the layout with the arrows (the live preview's box must change) -> start
// with Enter on "los" -> result: the arrows reach "Drucken" (not pressed)
// -> "Fertig". Then the buzzer: an arrow focus on another control, and
// PageDown / F13 still start the operator's mode. With --expect-arrows off
// (a booth with controls.arrow_navigation off) ArrowRight must start, as
// before. Writes <out>/kiosk-keys.json and screenshots with the focus ring;
// exit 1 if a gate fails.
const KEYS = { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Enter: 13, Escape: 27, PageDown: 34, F13: 124, Backspace: 8 };
async function key(name) {
  const p = { key: name, code: name, windowsVirtualKeyCode: KEYS[name] || 0 };
  await send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...p });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...p });
  await sleep(220);
}
const focusInfo = `(() => { const a = document.activeElement; const r = a && a.getBoundingClientRect();
  const cs = a && getComputedStyle(a);
  return { screen: document.querySelector('#app').dataset.screen, id: a && a.id, mode: a && a.dataset.mode || '',
    format: a && a.dataset.format || '', template: a && a.dataset.template || '', action: a && a.dataset.action || '',
    tile: a && a.dataset.item || '', full: !document.querySelector('#fullOverlay').hidden,
    shown: (document.querySelector('#fullMedia video, #fullMedia img') || {}).src || '',
    ring: !!(a && a.classList.contains('is-fokus')), outline: cs ? cs.outlineStyle + ' ' + cs.outlineWidth : '',
    visible: !!(r && r.width > 8 && r.height > 8 && !a.closest('[hidden]')),
    frame: (() => { const f = document.querySelector('#setupFrame'); if (!f) return null; const b = f.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; })(),
    pressedFormat: (document.querySelector('#formatChips [aria-pressed=true]') || {}).dataset?.format || '',
    pressedTemplate: (document.querySelector('#templateChips [aria-pressed=true]') || {}).dataset?.template || '' }; })()`;
async function until(expr, ms = 20000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { if (await evaluate(expr)) return true; } catch {} await sleep(150); }
  return false;
}
async function pressUntil(keyName, test, max = 10) {
  for (let i = 0; i < max; i++) {
    const f = await evaluate(focusInfo);
    if (test(f)) return f;
    await key(keyName);
  }
  return evaluate(focusInfo);
}
async function kioskKeysRun() {
  const [w, h] = (opt("sizes", "1707x960").split(",")[0]).split("x").map(Number);
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  const gates = [], log = [];
  const step = async (name, extra = {}) => { const f = await evaluate(focusInfo); log.push({ step: name, ...f, ...extra }); return f; };
  const shot = (name) => screenshot(path.join(out, `kiosk-keys-${name}.png`));
  await load(cfg.base + "/");
  await until("!!document.querySelector('#modes .mode')");
  await sleep(1200);
  if (opt("expect-arrows", "on") === "off") {
    await key("ArrowRight");
    const ok = await until("document.querySelector('#app').dataset.screen !== 'start'", 4000);
    const f = await step("ArrowRight with arrow navigation off");
    if (!ok) gates.push("arrow navigation off: ArrowRight did not start (old behaviour lost)");
    fs.writeFileSync(path.join(out, "kiosk-keys.json"), JSON.stringify({ gates, log }, null, 1));
    console.log(JSON.stringify({ gates, log }, null, 1));
    return gates.length ? 1 : 0;
  }
  // start: the first arrow shows the focus on the operator's mode, the next ones walk
  await key("ArrowRight");
  let f = await step("first arrow");
  if (!f.ring || !f.mode) gates.push("start: the first arrow did not show a focus on a mode tile");
  if (f.screen !== "start") gates.push("start: an arrow started a run");
  f = await pressUntil("ArrowRight", (x) => x.mode === "boomerang");
  await shot("1-start-focus");
  if (f.mode !== "boomerang") gates.push("start: the arrows did not reach the boomerang tile");
  await key("Enter");
  await until("document.querySelector('#app').dataset.screen === 'setup'", 5000);
  await sleep(900);
  f = await step("set-up entered");
  if (f.action !== "go" || !f.ring) gates.push("set-up: the focus is not on the main action");
  // up to the layout row, then up to the format row
  f = await pressUntil("ArrowUp", (x) => !!x.format, 4);
  const frameBefore = f.frame, fmtBefore = f.pressedFormat;
  await key("ArrowLeft");
  await sleep(700);
  f = await step("format changed with ArrowLeft");
  if (f.pressedFormat === fmtBefore) gates.push("set-up: ArrowLeft in the format row did not choose");
  if (JSON.stringify(f.frame) === JSON.stringify(frameBefore)) gates.push("set-up: the live preview did not follow the format");
  await shot("2-setup-format");
  await key("ArrowDown");
  f = await step("down to the layout row");
  if (!f.template) gates.push("set-up: ArrowDown from the format row did not reach the layout row");
  const tplBefore = f.pressedTemplate;
  await key("ArrowRight");
  await sleep(500);
  f = await step("layout changed with ArrowRight");
  if (f.pressedTemplate === tplBefore) gates.push("set-up: ArrowRight in the layout row did not choose");
  await shot("3-setup-layout");
  f = await pressUntil("ArrowDown", (x) => x.action === "go", 4);
  if (f.action !== "go") gates.push("set-up: the arrows did not reach the start button");
  await key("Enter");
  const started = await until("['capture','processing','result'].includes(document.querySelector('#app').dataset.screen)", 8000);
  if (!started) gates.push("set-up: Enter on the start button did not start");
  await until("document.querySelector('#rec').classList.contains('on')", 15000);
  await sleep(600);
  await shot("4-recording");
  const result = await until("document.querySelector('#app').dataset.screen === 'result'", 60000);
  if (!result) gates.push("no result screen");
  await sleep(1500);
  f = await step("result entered");
  if (f.id !== "doneBtn") gates.push("result: the focus is not on Fertig");
  f = await pressUntil("ArrowUp", (x) => x.id === "printBtn", 6);
  if (f.id !== "printBtn") f = await pressUntil("ArrowRight", (x) => x.id === "printBtn", 6);
  await shot("5-result-print-focus");
  if (f.id !== "printBtn") gates.push("result: the arrows did not reach Drucken");
  if (!f.ring) gates.push("result: no visible ring on Drucken");
  f = await pressUntil("ArrowDown", (x) => x.id === "doneBtn", 6);
  if (f.id !== "doneBtn") f = await pressUntil("ArrowRight", (x) => x.id === "doneBtn", 6);
  await key("Enter");
  const home = await until("document.querySelector('#app').dataset.screen === 'start'", 8000);
  if (!home) gates.push("result: Enter on Fertig did not go back to the start");
  await step("after Fertig");
  // the buzzer: an arrow focus elsewhere, then PageDown and F13 still start the operator's mode
  await sleep(1400);
  f = await pressUntil("ArrowUp", (x) => x.action === "gallery" || x.action === "lang", 4);
  await step("arrow focus away from the tiles", { on: f.action });
  for (const buzzer of ["PageDown", "F13"]) {
    await sleep(1400);
    await key(buzzer);
    const s = await until("document.querySelector('#app').dataset.screen === 'setup'", 4000);
    const g = await step(`buzzer ${buzzer}`);
    if (!s) gates.push(`buzzer ${buzzer} did not start the operator's mode (screen ${g.screen})`);
    await key("Escape");
    await until("document.querySelector('#app').dataset.screen === 'start'", 4000);
  }
  // the booth's gallery: a tile, its big view, the next picture with the arrows
  await sleep(1400);
  f = await pressUntil("ArrowUp", (x) => x.action === "gallery", 4);
  if (f.action === "gallery") {
    await key("Enter");
    await until("document.querySelector('#app').dataset.screen === 'gallery' && !!document.querySelector('#galleryGrid .tile')", 6000);
    await sleep(600);
    f = await pressUntil("ArrowDown", (x) => !!x.tile, 4);
    if (!f.tile) f = await pressUntil("ArrowRight", (x) => !!x.tile, 4);
    await shot("6-gallery-focus");
    if (!f.tile) gates.push("gallery: the arrows did not reach a tile");
    await key("Enter");
    await until("document.querySelector('#app').dataset.screen === 'result'", 6000);
    await sleep(800);
    f = await pressUntil("ArrowLeft", (x) => x.action === "full", 4);
    await key("Enter");
    await sleep(600);
    f = await step("big view");
    const first = f.shown;
    if (!f.full) gates.push("gallery: Enter on the picture did not open the big view");
    await key("ArrowRight");
    await sleep(700);
    f = await step("big view, ArrowRight");
    await shot("7-gallery-big-view-next");
    const tiles = await evaluate("document.querySelectorAll('#galleryGrid .tile').length");
    if (tiles > 1 && f.full && f.shown === first) gates.push("gallery: ArrowRight in the big view did not show the next picture");
    await key("Escape");
    await sleep(400);
    f = await step("big view closed");
    if (f.full) gates.push("gallery: Escape did not close the big view");
    await key("Escape");
    await until("document.querySelector('#app').dataset.screen === 'start'", 4000);
  } else gates.push("start: the arrows did not reach the gallery button");
  // Backspace goes back from the set-up screen
  await sleep(1400);
  await key("PageDown");
  await until("document.querySelector('#app').dataset.screen === 'setup'", 4000);
  await key("Backspace");
  if (!(await until("document.querySelector('#app').dataset.screen === 'start'", 4000))) gates.push("Backspace did not go back");
  fs.writeFileSync(path.join(out, "kiosk-keys.json"), JSON.stringify({ gates, log }, null, 1));
  console.log(JSON.stringify({ gates, log }, null, 1));
  return gates.length ? 1 : 0;
}
if (kioskKeys) process.exit(await kioskKeysRun());

const results = fs.existsSync(path.join(out, "gates.json")) ? JSON.parse(fs.readFileSync(path.join(out, "gates.json"), "utf8")) : {};
let themeScript = null;
for (const theme of themes) {
  if (themeScript) await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: themeScript });
  themeScript = (await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `try { localStorage.setItem("pb-theme", ${JSON.stringify(theme)}); } catch (e) {}` })).result.identifier;
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  for (const w of widths) {
    const phone = w < 700 || (HEIGHTS[w] || 900) < 500;
    const h = HEIGHTS[w] || (phone ? 844 : 900);
    await send("Emulation.setTouchEmulationEnabled", { enabled: phone, maxTouchPoints: phone ? 5 : 0 });
    for (const page of only) {
      const url = cfg.base + cfg.pages[page];
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: phone ? 2 : 1, mobile: phone });
      await load(url);
      // --expand: open every folded section and <details>, so what hides in them is measured too.
      // --eval "js": run after load (type into a search field, open a menu) before measuring.
      if (opt("eval")) { await evaluate(opt("eval")); await sleep(600); }
      if (flag("expand")) { await evaluate(`document.querySelectorAll(".adm-sec.is-collapsed").forEach((s) => s.classList.remove("is-collapsed")); document.querySelectorAll("details:not([open])").forEach((d) => d.open = true); 1`); await sleep(300); }
      if (theme === themes[0]) {
        try {
          const m = await evaluate(MEASURE.replace("__WIDTH__", String(w)).replace("__PHOTOSEL__", JSON.stringify(guest ? (cfg.photoSelector || "") : "")).replace("__STRICT__", guest && phone ? "true" : "false"));
          (results[page] ||= {})[w] = m;
          console.log(page, w, `scroll ${m.scrollWidth}/${m.width}`, `cross ${m.crossRight.length}`, `small ${m.smallTargets.length}`,
            `close ${m.tooClose.length}`, `tiny ${m.tinyText.length}`, `zoom ${m.zoomInputs.length}`, `overlap ${m.overlaps.length}`,
            ...(guest ? [`sticky ${m.stickyPx}px/${m.stickyShare}%`, `photo1 ${m.firstPhotoVisible ? "yes" : "no"}@${m.firstPhotoTop}`,
                         `videos-loading ${m.mediaLoading ?? "-"}`] : []));
        } catch (e) { console.log(page, w, "measure failed", String(e).slice(0, 200)); }
      }
      if (shots && firstScreen) {
        const shot = await send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(path.join(out, `${page}-${w}x${h}-${theme}.png`), Buffer.from(shot.result.data, "base64"));
      } else if (shots) {
        const lm = await send("Page.getLayoutMetrics");
        const full = Math.min(Math.ceil(lm.result.cssContentSize.height), phone ? 6000 : 4000);
        await send("Emulation.setDeviceMetricsOverride", { width: w, height: Math.max(full, h), deviceScaleFactor: phone ? 2 : 1, mobile: phone });
        await sleep(300);
        const shot = await send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(path.join(out, `${page}-${w}-${theme}.png`), Buffer.from(shot.result.data, "base64"));
      }
    }
  }
}
fs.writeFileSync(path.join(out, "gates.json"), JSON.stringify(results, null, 1));
const narrow = Math.min(...widths);
let failed = Object.entries(results).filter(([, r]) => r[narrow] && (r[narrow].pageScroll || r[narrow].crossRight.length));
if (guest) {
  // Guest pages: every size counts, and so do thumbs and Safari's zoom.
  const why = [];
  for (const [p, r] of Object.entries(results)) for (const w of widths) {
    const m = r[w]; if (!m) continue;
    if (m.pageScroll || m.crossRight.length) why.push(`${p}@${w} scrolls sideways`);
    if (m.smallTargets.length) why.push(`${p}@${w} ${m.smallTargets.length} targets under 44px`);
    if (m.zoomInputs.length) why.push(`${p}@${w} ${m.zoomInputs.length} inputs under 16px`);
    if (p.startsWith("gallery") && w === narrow && m.photos && !m.firstPhotoVisible) why.push(`${p}@${w} no photo in the first screen`);
  }
  for (const line of why) console.log("GATE", line);
  failed = why.map((l) => [l]);
}
console.log(failed.length ? `HARD GATE FAILED at ${narrow}px: ${failed.map(([p]) => p).join(", ")}` : `all pages pass the hard gates at ${narrow}px`);
ws.close();
chrome.kill();
await sleep(500);
try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5 }); } catch {}
process.exit(failed.length && !desktop ? 1 : 0);
