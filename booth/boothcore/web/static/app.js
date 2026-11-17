/* Photobox Booth — Kiosk-Oberfläche. Kein Build-Schritt, läuft offline. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const app = $("#app");
  const I18N = window.BOOTH_I18N;
  // Die Fachsymbole des Erscheinungsbilds „Abzug“ (24er Raster,
  // Strichstärke 1,75, currentColor). Quelle: _design/brand/icons — hier
  // eingebettet, weil die Box nichts nachlädt.
  const ICONS = {
    photo: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><rect x="7.5" y="5.5" width="9" height="9" rx="1"/></svg>',
    collage: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M12 3v18M3 12h18"/></svg>',
    boomerang: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 9.5h13.2l-3.2-3.2"/><path d="M20.5 14.5H7.3l3.2 3.2"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6" width="13" height="12" rx="2.5"/><path d="M15.5 10.4 21.5 7v10l-6-3.4z"/></svg>',
    gif: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="7" width="14" height="11.5" rx="2"/><path d="M7 4h11a2 2 0 0 1 2 2v9.2"/><path d="M8.8 10.6 12.8 12.8 8.8 15z"/></svg>',
    // Das Abspielzeichen auf einer Kachel ist kein Fachsymbol, sondern eine
    // Marke auf dem Bild: gefüllt, damit es auf jedem Foto steht.
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/></svg>',
  };
  const VIDEO_MODES = ["boomerang", "video"];
  const LIVE = "/api/live.mjpg";

  let S = null;              // Zustand vom Server
  let lang = "de";
  let flow = {};             // aktueller Durchgang
  let view = {};             // angezeigtes Ergebnis/Galerie-Element
  let idleTimer = null, resultTimer = null, mediaTimer = null, confirmCb = null;
  let reviewGo = null, reviewTimer = null;      // Durchsehen: offener Abschluss und Zeitschaltung
  let mailAddress = "";      // Eingabe der Bildschirmtastatur
  let wasLocked = false;     // letzter bekannter Sperrzustand (für den Bildschirmschoner)
  // Arrow-key focus (arrowKeys): the element, whether the arrows put it there, when.
  let navFocus = null, navByArrows = false, navAt = 0;

  // ── Hilfen ──────────────────────────────────────────────
  // Schlüsselmodus: /?keys=1 zeigt statt jedes Textes seinen Schlüssel. So findet
  // der Betreiber im Galerie-Admin genau das Feld, das er ändern will.
  const SHOW_KEYS = new URLSearchParams(location.search).has("keys");
  const t = (key, vars) => {
    if (SHOW_KEYS) return key;
    const custom = (S && S.profile.texts && S.profile.texts[lang]) || {};
    let s = custom[key] ?? (I18N[lang] || {})[key] ?? I18N.de[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
    return s;
  };
  // The built-in looks carry German names from the booth ("Kühl",
  // "Schwarzweiß"); an English guest read them untranslated, and the long one
  // broke as "Schwarzwei / ß". LUT looks and an operator's own keep their name.
  const lookLabel = (l) => { const k = "look." + l.id; const s = t(k); return s === k ? l.label : s; };
  function applyTexts() {
    document.documentElement.lang = lang;
    for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
    // Namen für Knöpfe, die nur ein Zeichen zeigen. Ohne das hörte ein
    // Screenreader „close" in einer deutschen Oberfläche und „-" statt
    // „weniger Kopien".
    for (const el of document.querySelectorAll("[data-a11y]")) {
      el.setAttribute("aria-label", t(el.dataset.a11y));
    }
  }
  const api = async (path, opts = {}) => {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (e) {}
      const err = new Error(msg); err.status = res.status;
      err.retryAfter = Number(res.headers.get("Retry-After")) || 0;
      throw err;
    }
    return res.json();
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Everything from the profile, the server or a guest that goes into an HTML
  // string passes through esc (R2-U1); tools/check_kiosk_escaping.py fails on
  // a new ${…} in markup that does not.
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // A URL inside CSS url("…"): quotes, brackets, backslashes and spaces encoded.
  const cssUrl = (u) => `url("${String(u ?? "").replace(/["'()\\\s<>]/g, (c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))}")`;
  // ICONS by a name from the profile: only its own entries, never a prototype key.
  const iconFor = (m) => (Object.prototype.hasOwnProperty.call(ICONS, m) ? ICONS[m] : "");
  const num = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v) : d);
  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.classList.add("on");
    // Über diesen Weg laufen alle Meldungen der Box — Kamera aus, Druck in der
    // Schlange, E-Mail ungültig. Ein Screenreader sah davon nichts, obwohl die
    // Seite mit #ansage genau den richtigen Bereich dafür hat.
    const ansage = $("#ansage");
    if (ansage) ansage.textContent = text;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("on"), 3000);
  }
  const humanError = (e) => { const k = e && e.message; const s = t(k); return s === k ? t("camera.generic") : s; };

  // ── Töne ────────────────────────────────────────────────
  // Eigene Dateien aus dem Profil schlagen den eingebauten Piepser; ohne Datei
  // bleibt es beim WebAudio-Ton. Ein fehlender oder kaputter Ton darf niemals
  // eine Aufnahme stoppen — darum ist jeder Versuch eingepackt und ein
  // abgelehntes play() wird stillschweigend verschluckt.
  const SOUND_EVENTS = ["countdown", "shutter", "done", "print"];
  let audio;                 // WebAudio für die eingebauten Ersatztöne
  let soundBank = {};        // Ereignis -> vorgeladenes Audio-Element
  let soundsUnlocked = false;

  const soundCfg = () => (S && S.profile.sounds) || {};
  const soundVolume = () => {
    const v = Number(soundCfg().volume);
    return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.8;
  };

  // Einmal je Profilstand laden: dieselbe Datei soll nicht bei jedem Auslösen neu geholt werden.
  function preloadSounds() {
    soundBank = {};
    const cfg = soundCfg();
    for (const key of SOUND_EVENTS) {
      if (!cfg[key]) continue;
      try {
        const el = new Audio("/assets/" + encodeURIComponent(cfg[key]));
        el.preload = "auto";
        el.volume = soundVolume();
        el.load();
        soundBank[key] = el;
      } catch (e) {}
    }
  }

  function playSound(key, fallback) {
    if (soundCfg().enabled === false) return;
    const src = soundBank[key];
    if (!src) return void (fallback && fallback());
    try {
      // cloneNode: schnelle Folgen (Countdown) würden sich sonst gegenseitig abschneiden
      const el = src.cloneNode();
      el.volume = soundVolume();
      const p = el.play();
      if (p && p.catch) p.catch(() => {});
    } catch (e) {}
  }

  // Kiosk-Browser lassen Ton erst nach der ersten Berührung zu: dort einmal stumm
  // anspielen, damit der spätere Auslöser ohne Verzögerung klingt.
  function unlockSounds() {
    if (soundsUnlocked) return;
    soundsUnlocked = true;
    try { audio = audio || new AudioContext(); if (audio.state === "suspended") audio.resume(); } catch (e) {}
    for (const el of Object.values(soundBank)) {
      try {
        el.muted = true;
        const back = () => { try { el.pause(); el.currentTime = 0; el.muted = false; } catch (e) {} };
        const p = el.play();
        if (p && p.then) p.then(back, back); else back();
      } catch (e) {}
    }
  }
  document.addEventListener("pointerdown", unlockSounds, { passive: true });
  document.addEventListener("keydown", unlockSounds);

  function beep(freq = 880, dur = 0.09, vol = 0.18) {
    if (!S || soundCfg().enabled === false || !S.profile.countdown.sound) return;
    try {
      audio = audio || new AudioContext();
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = freq; o.type = "sine"; g.gain.value = vol;
      o.connect(g); g.connect(audio.destination); o.start();
      g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + dur); o.stop(audio.currentTime + dur + 0.02);
    } catch (e) {}
  }
  const shutterSound = () => { beep(1600, 0.05, 0.25); setTimeout(() => beep(1100, 0.06, 0.2), 60); };

  // ── Bildschirme ─────────────────────────────────────────
  function show(screen) {
    app.dataset.screen = screen;
    requestAnimationFrame(placeNotice);   // the start screen places it, others centre it
    fokusZuruecksetzen();          // jeder Bildschirm fängt bei null an
    barrierefrei(screen);
    navEnter();                    // a keyboard guest finds the focus on the main action
    const liveOn = screen === "setup" || screen === "capture";
    for (const img of document.querySelectorAll("img.live")) {
      const want = liveOn && img.closest(".screen").id === "s-" + screen;
      if (want && !(img.getAttribute("src") || "").includes("live")) img.src = LIVE + "?t=" + Date.now();
      if (!want && img.getAttribute("src")) img.removeAttribute("src");
      img.classList.toggle("mirror", !!(S && S.mirror_preview));
    }
    // The stream's first frame tells its size; lay out again once it is there.
    if (liveOn) { setTimeout(applyFrame, 50); setTimeout(applyFrame, 600); }
    if (screen !== "start") { clearInterval(mediaTimer); stopAttract(false); }
    armIdle();
    armAttract();
  }
  // ── Bedienbar ohne Maus, ohne Auge ──────────────────────
  //
  // Die Bildschirme der Box liegen übereinander und werden nur ausgeblendet.
  // Für das Auge reicht das; für Tastatur und Screenreader nicht: Ohne
  // Zutun tabbt man in Schaltflächen, die niemand sieht. ``inert`` nimmt die
  // unsichtbaren Bildschirme vollständig aus Bedienung und Vorlesen heraus.
  //
  // Dazu wandert der Fokus mit: Wer mit Tabulator arbeitet, fängt sonst nach
  // jedem Wechsel wieder ganz vorn an.
  function barrierefrei(screen) {
    document.querySelectorAll(".screen").forEach((el) => {
      const aktiv = el.id === "s-" + screen;
      if ("inert" in HTMLElement.prototype) el.inert = !aktiv;
      el.setAttribute("aria-hidden", aktiv ? "false" : "true");
    });
    const ziel = document.querySelector(`#s-${screen} h1, #s-${screen} [autofocus], #s-${screen} button:not([disabled])`);
    // Nur bei Tastaturbedienung: Sonst zeichnet ein Fingertipp einen Fokusrahmen,
    // der am Kiosk nichts zu suchen hat.
    if (ziel && tastaturModus) {
      if (!ziel.hasAttribute("tabindex") && ziel.tagName === "H1") ziel.tabIndex = -1;
      try { ziel.focus({ preventScroll: true }); } catch (e) { /* egal */ }
    }
    const ansage = $("#ansage");
    if (ansage) ansage.textContent = t("screen." + screen) || "";
  }

  // Merken, ob gerade mit Tastatur gearbeitet wird — nur dann Fokus setzen.
  let tastaturModus = false;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Tab" || e.key === "Enter" || e.key === " " || /^Arrow/.test(e.key)) tastaturModus = true;
  }, true);
  // A finger or the mouse: Enter/Space no longer press what the arrows reached.
  document.addEventListener("pointerdown", () => { tastaturModus = false; navByArrows = false; }, true);

  function armIdle() {
    clearTimeout(idleTimer);
    const sc = app.dataset.screen;
    if (["setup", "consent", "error", "gallery"].includes(sc) || (sc === "result" && view.fromGallery)) {
      idleTimer = setTimeout(home, ((S && S.profile.idle_seconds) || 60) * 1000);
    }
  }
  document.addEventListener("pointerdown", () => {
    if (dismissAttract()) return;   // diese Berührung weckt nur auf, sonst nichts
    bumpTimers(false);
  }, { passive: true });
  // Any sign of a guest (touch, key, buzzer action) restarts the idle, attract
  // and result timers. `keys`: a key or button action may arrive while a window
  // is open (text, drawing, full screen) - those hold the result timer on
  // purpose, so it is not restarted under them.
  function bumpTimers(keys) {
    armIdle();
    armAttract();
    if (keys && overlayOpen()) return;
    // A finger that has just started a stroke on the drawing canvas holds the
    // result timer until it lifts (drawLift starts it again).
    if (app.dataset.screen === "result" && !view.fromGallery && !afterTimer && drawPointer === null) startResultTimer();
  }
  document.addEventListener("contextmenu", (e) => e.preventDefault());

  // The language each run starts in: the one remote control set (kiosk_lang),
  // if the profile still offers it, else the profile's first.
  function startLang() {
    const langs = (S && S.profile && S.profile.languages) || ["de"];
    return (S && S.kiosk_lang && langs.includes(S.kiosk_lang)) ? S.kiosk_lang : (langs[0] || "de");
  }

  // ── Typing dialogs: the booth must know (final review A10) ──
  // While someone types an e-mail address, a greeting or a PIN, remote control
  // takes no screenshot and no live picture. The booth cannot see the page, so
  // the kiosk says it: on every state poll and at once when such a dialog
  // opens or closes (fernsteuerung.py, _no_guest_at_the_result).
  // <typing-helpers> — pure, no DOM; booth/tests/test_kiosk_reports_typing.py runs it with Node.
  const TYPING_OVERLAYS = ["mailOverlay", "textOverlay", "pinOverlay"];
  function typingOpen(isHidden) {
    return TYPING_OVERLAYS.some((id) => isHidden(id) === false);
  }
  function typingQuery(open) { return "/api/state?typing=" + (open ? "1" : "0"); }
  // </typing-helpers>
  const typingNow = () => typingOpen((id) => { const el = document.getElementById(id); return el ? el.hidden : true; });
  let typingSaid = null;
  function reportTyping() {
    const open = typingNow();
    if (open === typingSaid) return;
    typingSaid = open;
    fetch(typingQuery(open)).catch(() => { typingSaid = null; });   // the next poll says it again
  }
  if (window.MutationObserver) {
    const watch = new MutationObserver(reportTyping);
    for (const id of TYPING_OVERLAYS) {
      const el = document.getElementById(id);
      if (el) watch.observe(el, { attributes: true, attributeFilter: ["hidden"] });
    }
  }

  // ── Zustand ─────────────────────────────────────────────
  async function refresh() {
    try {
      const open = typingNow();
      typingSaid = open;
      const next = await api(typingQuery(open));
      const changed = !S || next.profile_rev !== S.profile_rev || JSON.stringify(next.start_media) !== JSON.stringify(S.start_media);
      // A language set by remote control applies at once, but only on the
      // start screen: a guest in the middle of a run keeps theirs.
      const langSet = !!S && (next.kiosk_lang || "") !== (S.kiosk_lang || "");
      S = next;
      preloadPhaseMedia();
      if (!S.profile.languages.includes(lang)) lang = startLang();
      if (changed) {
        applyMirror();
        applyTheme();                // Farben und Form greifen sofort, auch mitten im Durchgang
        applyBackdrops();
        applyControls();
        applyLayout();
        applyPreviewGain();
        applyPrivacy();
        applyTexts();
        preloadSounds();
        stopAttract();              // neue Einstellungen: Wartezeit und Quelle frisch aufsetzen
        applyFrame();               // a new format, fit or clip layout frames the live picture anew
        if (app.dataset.screen === "start" || app.dataset.screen === "boot") renderStart();
      }
      renderStatus();
      if (langSet && ["start", "boot"].includes(app.dataset.screen)) {
        lang = startLang();
        applyTexts(); renderStart(); renderStatus();
      }
      // A camera that changed its live-view size (another backend, a webcam
      // switched to 16:9) gets its framing laid out again.
      const shown = { setup: "#setupLive", capture: "#captureLive" }[app.dataset.screen];
      const im = shown && $(shown);
      if (im && im.naturalWidth && im.dataset.laid !== im.naturalWidth + "x" + im.naturalHeight) applyFrame();
      // Die letzte Aufnahme und der Galerie-Link stehen im Zustand, nicht im
      // Profil: Beides ändert sich im Betrieb, ohne dass jemand etwas umstellt.
      if (app.dataset.screen === "start" && S && S.profile) renderStartExtras(S.profile.start);
      // Auslösung von außen (Buzzer über WLAN, Leitstand): nur vom Startbildschirm
      // aus, sonst würde ein laufender Durchgang mittendrin abgebrochen.
      if (next.trigger && !S.locked && ["start", "boot"].includes(app.dataset.screen)) {
        stopAttract();
        openSetup(next.trigger.mode);
      }
    } catch (e) { /* Dienst startet noch */ }
  }

  // Mirror-Booth: Die ganze Oberfläche wird gespiegelt, Beschriftungen werden
  // zurückgespiegelt — sonst stünde der Text seitenverkehrt auf dem Glas.
  // Datensparsamer Betrieb: Hinweis zeigen, alles verbergen, was es nicht gibt.
  function applyPrivacy() {
    const p = (S && S.privacy) || {};
    const start = $("#privacyStart"), result = $("#privacyResult");
    app.dataset.privacy = p.print_only ? "1" : "";
    start.hidden = !p.print_only;
    result.hidden = !p.print_only;
    if (!p.print_only) return;
    start.textContent = p.notice || t("privacy.start");
    result.textContent = t("privacy.result");
  }

  // Bei Blitzbelichtung zeigt die Kamera ein fast schwarzes Live-Bild: sie
  // simuliert die Belichtung ohne das Licht, das erst beim Auslösen kommt.
  // Since 15.11.2026 the booth brightens the frames it serves itself
  // (flash.preview_gain, a lookup table on /api/live.mjpg — a CSS brightness
  // only stretched the black it got). No filter here any more, or the gain
  // would count twice; one left from an older page is taken off.
  function applyPreviewGain() {
    for (const el of document.querySelectorAll(".live, #captureLive")) el.style.filter = "";
  }

  // ── Where the guest notice goes on the start screen ─────
  // Top centre, fixed, it lay over the status chip, "Galerie", "EN" and the
  // lock (and took their taps) as soon as the window was small: 1024x768 on a
  // booth started without its monitor, 17.11.2026. Now it takes the first
  // band from the top in which it covers no corner control, no title and no
  // tile, at the widest free stretch of that band, and wraps there. Other
  // screens keep the plain centred pill. Pinned by
  // booth/tests/test_start_screen_fits_small_windows.py.
  function placeNotice() {
    const n = $("#notice");
    if (!n) return;
    n.classList.remove("notice--placed");
    for (const k of ["left", "top", "width"]) n.style[k] = "";
    if (n.hidden || app.dataset.screen !== "start") return;
    const W = innerWidth, H = innerHeight, gap = 16;
    const edgeX = Math.max(28, W * 0.035), edgeY = Math.max(24, H * 0.035);
    const shown = (el) => el && !el.hidden && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
    const sels = ["#statusline", "#s-start .start-tools", "#adminHotspot", "#remoteMark", "#s-start .start-copy",
                  "#startSteps", "#startLast", "#startQr", "#startLogo", "#privacyStart"];
    const avoid = [...sels.map((s) => document.querySelector(s)), ...document.querySelectorAll("#modes .mode")]
      .filter(shown).map((el) => el.getBoundingClientRect()).filter((r) => r.width && r.height);
    // The widest stretch of [edgeX, W - edgeX] free of everything in rows y0..y1.
    const freeAt = (y0, y1) => {
      const cuts = avoid.filter((r) => r.top < y1 + gap && r.bottom > y0 - gap)
        .map((r) => [r.left - gap, r.right + gap]).sort((a, b) => a[0] - b[0]);
      let best = [0, 0], x = edgeX;
      for (const [a, b] of cuts) {
        if (a > x && a - x > best[1] - best[0]) best = [x, a];
        x = Math.max(x, b);
      }
      if (W - edgeX - x > best[1] - best[0]) best = [x, W - edgeX];
      return best;
    };
    n.classList.add("notice--placed");
    const minWidth = Math.min(320, W * 0.5);
    const tops = [edgeY, ...avoid.map((r) => r.bottom + gap)].filter((y) => y >= edgeY && y < H).sort((a, b) => a - b);
    for (const top of tops) {
      let span = freeAt(top, top + 60);
      for (let i = 0; i < 3 && span[1] - span[0] >= minWidth; i++) {
        const width = Math.min(span[1] - span[0], Math.max(minWidth, W * 0.8), 900);
        n.style.width = "max-content";
        n.style.maxWidth = width + "px";
        const h = n.getBoundingClientRect().height;
        n.style.maxWidth = "";
        const again = freeAt(top, top + h);
        if (again[0] === span[0] && again[1] === span[1] && top + h <= H - edgeY) {
          const w = Math.min(n.scrollWidth, width);
          n.style.width = w + "px";
          n.style.left = Math.round(span[0] + (span[1] - span[0] - w) / 2) + "px";
          n.style.top = Math.round(top) + "px";
          return;
        }
        span = again;
      }
    }
    // Nowhere free (a tiny window): the old centred pill, at least whole.
    n.classList.remove("notice--placed");
    n.style.width = "";
  }
  window.addEventListener("resize", placeNotice);

  function applyMirror() {
    const m = (S && S.profile.mirror) || {};
    app.dataset.mirrorH = m.horizontal ? "1" : "";
    app.dataset.mirrorV = m.vertical ? "1" : "";
  }

  function renderStatus() {
    const cam = S.camera.state === "ready" ? "ok" : "bad";
    const sync = S.sync.online ? "ok" : (S.sync.pending ? "warn" : "");
    const takeover = S.printer.takeover;     // one printer down, another prints on
    const pr = takeover ? "warn"
      : ({ ready: "ok", printing: "ok", error: "bad", offline: "bad", none: "" }[S.printer.state] || "");
    const prText = takeover ? t("status.printer_takeover", { n: takeover.up[0] }) : t("status.printer");
    // "Online · 2 offen" read as two open somethings (16.11.2026): uploads say
    // so, and waiting or held prints stand at the printer, not at the network.
    const pend = S.sync.pending ? ` · ${esc(t("status.queue", { n: S.sync.pending }))}` : "";
    const printsWaiting = (Number(S.printer.pending) || 0) + (Number(S.printer.held) || 0);
    const prWait = printsWaiting ? ` · ${esc(t("status.prints", { n: printsWaiting }))}` : "";
    // Operator notice: only on the start screen (on the result it would be in
    // every phone photo of the booth), and only as one more item in the status
    // line. As a box of its own it sat on the "Foto" tile and took the tap.
    // The title is enough to know something needs doing; the full text and
    // the history are in the admin page under "Meldungen". The dot stays
    // amber even for critical ones — red belongs to recording and deleting.
    const meldung = S.meldung ? meldungText(S.meldung) : null;
    const meldungSichtbar = !!meldung && app.dataset.screen === "start";
    const meldungHtml = meldungSichtbar
      ? `<span class="betriebsmeldung" data-stufe="${esc(meldung.stufe || "")}"><i class="warn"></i>`
        + `<b>${esc(meldung.titel || "")}</b><span class="nur-vorlesen"> ${esc(meldung.text || "")}</span></span>`
      : "";
    const statusline = $("#statusline");
    const hs = $("#adminHotspot");
    hs.dataset.notice = meldungSichtbar ? "1" : "";
    hs.title = meldungSichtbar ? `${meldung.titel || ""} — ${meldung.text || ""}` : "";
    if ((S.profile.start || {}).show_status === false) {
      // Status line switched off for an own start picture. The operator
      // notice stays findable: one amber dot in the line's place, its title
      // on hover and its text for a screen reader. No notice, nothing at all.
      statusline.dataset.quiet = "1";
      statusline.innerHTML = meldungSichtbar
        ? `<span class="betriebsmeldung betriebsmeldung--punkt" title="${esc(meldung.titel || "")}"><i class="warn"></i>`
          + `<span class="nur-vorlesen">${esc(meldung.titel || "")} ${esc(meldung.text || "")}</span></span>`
        : "";
      // With the visible lock (access.lock_icon) not even the dot: the
      // notice colours the lock and names itself on hover.
      const lockIcon = !!(S.profile.access || {}).lock_icon;
      statusline.hidden = lockIcon || !meldungSichtbar;
    } else {
      statusline.dataset.quiet = "";
      statusline.hidden = false;
      statusline.innerHTML = `<span><i class="${cam}"></i>${esc(t("status.camera"))}</span>`
        + `<span><i class="${pr}"></i>${esc(prText)}${prWait}</span>`
        + `<span><i class="${sync}"></i>${esc(t(S.sync.online ? "status.online" : "status.offline"))}${pend}</span>`
        + meldungHtml;
    }
    // Gäste-Hinweis nur, wenn die Kamera fehlt
    const notice = $("#notice");
    const camBad = S.camera.state !== "ready" && ["start", "setup"].includes(app.dataset.screen);
    notice.hidden = !camBad;
    if (camBad) $("#noticeText").textContent = t(S.camera.error_code || "notice.camera");
    placeNotice();
    // Sperre — beendet den Bildschirmschoner; nach dem Entsperren läuft die Wartezeit von vorn
    if (S.locked !== wasLocked) { wasLocked = S.locked; S.locked ? stopAttract(false) : armAttract(); }
    $("#lockOverlay").hidden = !S.locked;
    const li = $("#lockImage");
    if (S.locked && S.profile.lock.image) { li.hidden = false; li.src = "/assets/" + encodeURIComponent(S.profile.lock.image); } else li.hidden = true;
    // Entsperren nur anbieten, wenn der Betreiber es erlaubt hat und die Sperre
    // an dieser Box gesetzt wurde. Eine Sperre aus der Ferne bleibt bestehen.
    const mayUnlock = S.locked && !S.remote_locked && ((S.profile.access || {}).lock_on_booth !== false);
    $("#unlockBtn").hidden = !mayUnlock;
    // The gallery drives this booth (fernsteuerung.py): a small mark in a
    // corner, never over a guest's face. Operator setting, on by default.
    let rm = document.getElementById("remoteMark");
    if (!rm) {
      rm = document.createElement("div");
      rm.id = "remoteMark";
      rm.className = "remote-mark";
      rm.setAttribute("role", "status");
      document.body.appendChild(rm);
    }
    const rc = S.remote_control || {};
    rm.hidden = !(rc.active && rc.indicator);
    rm.textContent = t("status.remote");
  }


  // ── Aussehen ────────────────────────────────────────────
  // Farben und Form kommen aus dem Profil (Abschnitt „Aussehen“ im Galerie-Admin).
  const RADII = { pill: "999px", round: "28px", soft: "14px", square: "4px" };
  const SIZES = { s: ["64px", "22px", "28px"], m: ["76px", "26px", "34px"], l: ["92px", "31px", "42px"] };
  const FONTS = {
    system: '"Inter","Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif',
    display: '"Archivo","Segoe UI Variable Display","Segoe UI",system-ui,sans-serif',
    serif: 'Georgia,"Iowan Old Style","Times New Roman",serif',
    rounded: 'ui-rounded,"SF Pro Rounded","Segoe UI Variable Display",system-ui,sans-serif',
    mono: 'ui-monospace,"Cascadia Mono",Consolas,"Courier New",monospace',
  };
  const hex = (c, fallback) => (/^#[0-9a-fA-F]{6}$/.test(c || "") ? c : fallback);
  // Zweite Fläche: Grundfläche ein Stück in Richtung Textfarbe geschoben, damit
  // sie sich sowohl auf dunklem als auch auf hellem Untergrund abhebt.
  function mix(a, b, amount) {
    const v = s => [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16));
    const [ar, ag, ab] = v(a), [br, bg, bb] = v(b);
    const c = (x, y) => Math.round(x + (y - x) * amount).toString(16).padStart(2, "0");
    return `#${c(ar, br)}${c(ag, bg)}${c(ab, bb)}`;
  }
  // Eigene Schriftart: die Datei liegt bei den Medien der Box und wird einmal
  // als @font-face angemeldet. Lädt sie nicht, bleibt die Systemschrift stehen —
  // eine Box ohne Beschriftung wäre der schlechtere Ausgang.
  let fontLoaded = "";
  function fontStack(th) {
    const file = (th.font_file || "").trim();
    if (!file) return FONTS[th.font] || FONTS.system;
    if (fontLoaded !== file) {
      fontLoaded = file;
      const style = document.getElementById("boothFont") || document.createElement("style");
      style.id = "boothFont";
      style.textContent = `@font-face{font-family:"BoothCustom";src:url("/assets/${encodeURIComponent(file)}");font-display:swap}`;
      document.head.appendChild(style);
    }
    return `"BoothCustom", ${FONTS[th.font] || FONTS.system}`;
  }

  // Zwei fertige Farbschemata aus dem Erscheinungsbild „Abzug" — Abschnitt
  // „kiosk" in css/brand/tokens.json. Sie stehen hier ausgeschrieben, weil die
  // Box sie ohne Netz und ohne Galerie braucht; ein Test vergleicht beide
  // Fassungen. Geändert werden sie in der Farbquelle, nicht hier von Hand.
  const SCHEMATA = {
    dunkel: { accent: "#e7ad59", on_accent: "#0f0d09", bg: "#0a0806",
              panel: "#23201c", text: "#f5f3f0", muted: "#b5b1ab" },
    hell:   { accent: "#9d6000", on_accent: "#fefdfc", bg: "#fefdfc",
              panel: "#efece9", text: "#1d1a16", muted: "#625d56" },
  };
  // Gefahr und Schatten gehören nicht der Akzentfarbe und hängen nur daran,
  // ob die Oberfläche hell oder dunkel ist.
  const SCHEMA_EXTRA = {
    dunkel: { gefahr: "#f66e60", flaeche: "#7f2c30", schrift: "#fde8e8",
              schatten: "rgba(0,0,0,.8)", tief: "rgba(0,0,0,.9)" },
    hell:   { gefahr: "#d33c33", flaeche: "#d33c33", schrift: "#fefdfc",
              schatten: "rgba(29,26,22,.18)", tief: "rgba(29,26,22,.28)" },
  };

  // Hell oder dunkel? Nicht am Schalter ablesen, sondern an der Farbe: Wer
  // eigene Farben setzt, bekommt trotzdem die passenden Schatten und Linien.
  function istHell(farbe) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(farbe.slice(i, i + 2), 16) / 255)
      .map((x) => (x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4)));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.35;
  }

  function applyTheme() {
    const th = (S && S.profile && S.profile.theme) || {};
    const root = document.documentElement.style;
    // Ein gewähltes Schema schlägt die einzelnen Farbfelder; „eigene" nimmt sie.
    const vor = SCHEMATA[th.mode] || null;
    const accent = hex(vor ? vor.accent : th.accent, hex(S && S.profile && S.profile.accent, "#e5484d"));
    const panel = hex(vor ? vor.panel : th.panel, "#17171b");
    const text = hex(vor ? vor.text : th.text, "#f4f4f5");
    root.setProperty("--accent", accent);
    root.setProperty("--on-accent", hex(vor ? vor.on_accent : th.on_accent, "#ffffff"));
    const bg = hex(vor ? vor.bg : th.bg, "#0c0c0e");
    root.setProperty("--bg", bg);
    root.setProperty("--bg-rgb", [1, 3, 5].map(i => parseInt(bg.slice(i, i + 2), 16)).join(","));
    root.setProperty("--panel", panel);
    root.setProperty("--panel-2", mix(panel, text, 0.12));
    root.setProperty("--text", text);
    root.setProperty("--muted", hex(vor ? vor.muted : th.muted, "#a1a1aa"));
    const schema = istHell(bg) ? "hell" : "dunkel";
    const zu = SCHEMA_EXTRA[schema];
    root.setProperty("--gefahr", zu.gefahr);
    root.setProperty("--gefahr-flaeche", zu.flaeche);
    root.setProperty("--gefahr-schrift", zu.schrift);
    root.setProperty("--schatten", zu.schatten);
    root.setProperty("--schatten-tief", zu.tief);
    app.dataset.scheme = schema;
    const radius = RADII[th.radius] || RADII.pill;
    root.setProperty("--btn-radius", radius);
    root.setProperty("--radius", radius === "999px" ? "22px" : radius);
    // Größe mal Bildschirm-Faktor: „Aussehen" wählt die Stufe, „Bildschirm"
    // die Gesamtgröße — beides zusammen, damit ein kleines Panel nicht zwingt,
    // die Gestaltung zu ändern.
    const [h, f, pad] = SIZES[th.size] || SIZES.m;
    root.setProperty("--btn-min-h", `calc(${h} * var(--ui-scale, 1))`);
    root.setProperty("--btn-font", `calc(${f} * var(--ui-scale, 1))`);
    root.setProperty("--btn-pad", `calc(${pad} * var(--ui-scale, 1))`);
    root.setProperty("--btn-weight", String(th.weight || "600"));
    root.setProperty("--btn-transform", th.uppercase ? "uppercase" : "none");
    root.setProperty("--font-stack", fontStack(th));
    // Überschriften an der Box tragen die Anzeigeschrift: Auf zwei Meter liest
    // sich Archivo wie Beschilderung, nicht wie Fließtext. Wer eine eigene
    // Schrift gewählt oder hochgeladen hat, behält sie überall.
    const eigene = (th.font_file || "").trim() || (th.font && th.font !== "system");
    root.setProperty("--font-display", eigene ? fontStack(th) : FONTS.display);
    app.dataset.uppercase = th.uppercase ? "1" : "0";
    app.dataset.secondary = th.secondary === "outline" ? "outline" : "solid";
  }

  // ── Backdrop per screen ─────────────────────────────────
  // Setup, capture and result can each have a picture or a colour, dimmed so
  // the text on top stays readable. Empty means what it always was: theme.bg.
  // The capture screen keeps the camera picture in front (it is a layer of
  // its own); the backdrop only fills the space around it.
  const BACKDROP_SCREENS = ["setup", "capture", "result"];
  function paintBackdrop(el, cfg) {
    const c = cfg || {};
    const image = String(c.image || "").trim();
    const color = hex(c.color, "");
    const on = !!(image || color);
    const dim = Math.max(0, Math.min(80, Number(c.dim) || 0));
    el.dataset.backdrop = on ? "1" : "";
    el.style.setProperty("--backdrop-image", image ? `url("/assets/${encodeURIComponent(image)}")` : "none");
    el.style.setProperty("--backdrop-color", color || "var(--bg)");
    el.style.setProperty("--backdrop-dim", String(on ? dim / 100 : 0));
  }
  function applyBackdrops() {
    const all = (S && S.profile && S.profile.backdrops) || {};
    for (const name of BACKDROP_SCREENS) paintBackdrop($("#s-" + name), all[name]);
  }

  // ── Media per phase of the run ──────────────────────────
  // "processing", "between" (between two shots of a series), "after" (after
  // the result, before the start screen) and "error" can each show a picture,
  // GIF or video. No phase waits for its medium: it is shown for as long as
  // the phase lasts anyway, and cut off when the phase ends. Only "after"
  // adds time, and only with a duration the operator set. The idle picture is
  // the attract screen, so there is no "idle" here.
  // <phase-media>
  function phaseMedia(phase, cfg, available, opts) {
    if (!["processing", "between", "after", "error"].includes(phase)) return null;
    const o = opts || {};
    const c = (cfg && cfg[phase]) || {};
    let file = String(c.media || "").trim();
    // a file the booth does not have is no medium: the phase looks as without
    if (file && !(available || []).includes(file)) file = "";
    const ext = (file.split(".").pop() || "").toLowerCase();
    let kind = !file ? "" : ["mp4", "webm", "mov", "m4v"].includes(ext) ? "video" : ext === "gif" ? "gif" : "image";
    // "reduce motion": a video stands as its first frame, a GIF cannot be stopped and goes
    if (o.reduceMotion && kind === "gif") { file = ""; kind = ""; }
    const out = { file, kind, fit: c.fit === "contain" ? "contain" : "cover", text: c.text !== false,
                  loop: c.loop !== false, still: !!o.reduceMotion && kind === "video", seconds: 0 };
    if (phase === "between" && !(Number(o.shot) < Number(o.needed))) return null;
    if (phase === "after") {
      out.seconds = Math.max(0, Math.min(15, Math.round(Number(c.seconds) || 0)));
      return out.seconds && (file || out.text) ? out : null;
    }
    return file ? out : null;
  }
  // </phase-media>
  const reduceMotion = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const phaseVideos = {};          // file -> <video> loaded ahead, so the first frame is there at once
  let phaseFiles = null;
  function preloadPhaseMedia() {
    const files = (S && S.phase_media) || [];
    if (phaseFiles === files.join("|")) return;
    phaseFiles = files.join("|");
    for (const f of Object.keys(phaseVideos)) if (!files.includes(f)) delete phaseVideos[f];
    for (const f of files) {
      if (phaseVideos[f]) continue;
      if (!/\.(mp4|webm|mov|m4v)$/i.test(f)) { const i = new Image(); i.src = "/assets/" + encodeURIComponent(f); continue; }
      const v = document.createElement("video");
      v.preload = "auto";
      v.muted = true;
      v.playsInline = true;
      v.setAttribute("muted", "");
      v.setAttribute("playsinline", "");
      v.src = "/assets/" + encodeURIComponent(f);
      v.load();
      phaseVideos[f] = v;
    }
  }
  function pickPhase(phase, opts) {
    return phaseMedia(phase, S && S.profile && S.profile.phase_media, S && S.phase_media,
                      { ...(opts || {}), reduceMotion: reduceMotion() });
  }
  // Fills a layer with the phase's medium or empties it. A medium that fails
  // to load takes itself away again; the screen underneath is complete.
  function paintPhase(el, pick) {
    for (const v of el.querySelectorAll("video")) { try { v.pause(); } catch (e) { /* gone */ } }
    el.replaceChildren();
    el.dataset.on = "";
    if (!pick || !pick.file) return false;
    el.dataset.fit = pick.fit;
    let node;
    if (pick.kind === "video") {
      if (!phaseVideos[pick.file]) { phaseFiles = null; preloadPhaseMedia(); }
      node = phaseVideos[pick.file];
      if (!node) return false;
      // loop off: an ended video keeps its last frame, which is what a
      // browser does by itself once looping is off
      node.loop = !!pick.loop;
      try { node.currentTime = 0; } catch (e) { /* not loaded yet */ }
      if (!pick.still) { const p = node.play(); if (p && p.catch) p.catch(() => {}); }
    } else {
      node = new Image();
      node.alt = "";
      node.src = "/assets/" + encodeURIComponent(pick.file);
    }
    node.onerror = () => { el.dataset.on = ""; };
    el.appendChild(node);
    el.dataset.on = "1";
    return true;
  }
  function showPhase(phase, opts) {
    const pick = pickPhase(phase, opts);
    if (phase === "processing" || phase === "error") {
      const screen = $("#s-" + phase);
      const on = paintPhase($(phase === "processing" ? "#phaseProcessing" : "#phaseError"), pick);
      screen.dataset.phase = on ? "1" : "";
      screen.dataset.phaseText = on && !pick.text ? "off" : "";
      return on;
    }
    const ov = $("#phaseOverlay");
    const on = paintPhase($("#phaseOverlayMedia"), pick);
    const text = pick && pick.text ? t("phase." + phase) : "";
    $("#phaseOverlayText").textContent = text;
    ov.dataset.phase = phase;
    ov.hidden = !(on || text);
    return !ov.hidden;
  }
  function hidePhaseOverlay() {
    paintPhase($("#phaseOverlayMedia"), null);
    $("#phaseOverlay").hidden = true;
  }

  // "after": a thank-you between a fresh result and the start screen. The
  // result timer and "Fertig" both end up here; a tap or the buzzer during
  // it goes straight on to the start.
  let afterTimer = null, afterShownAt = 0;
  // The second half of a double tap on "Fertig" lands on the thank-you that
  // the first half opened; it would skip it at once. A tap counts from here on.
  const AFTER_TAP_GRACE_MS = 700;
  const afterTapTooSoon = () => Date.now() - afterShownAt < AFTER_TAP_GRACE_MS;
  function afterPhase() {
    if (afterTimer) return afterTapTooSoon() ? undefined : endAfter();
    if (!view.fresh || view.fromGallery || app.dataset.screen !== "result") return home();
    const pick = pickPhase("after");
    if (!pick || !showPhase("after")) return home();
    clearTimeout(resultTimer);
    afterShownAt = Date.now();
    afterTimer = setTimeout(endAfter, pick.seconds * 1000);
  }
  function endAfter() {
    clearTimeout(afterTimer);
    afterTimer = null;
    home();
  }
  $("#phaseOverlay").addEventListener("click", () => { if (afterTimer && !afterTapTooSoon()) endAfter(); });

  // ── Start ───────────────────────────────────────────────
  function renderStart() {
    applyPrivacy();
    applyTheme();
    applyPreviewGain();
    const p = S.profile;
    const st = $("#s-start");
    st.dataset.layout = p.start.layout || "bottom";
    st.dataset.size = p.start.button_size || "l";
    // -1 (default) keeps the gradient; 0–80 is a flat dim, 0 shows the picture as it is.
    const dim = Number(p.start.dim ?? -1);
    st.dataset.shade = dim >= 0 ? "flat" : "";
    st.style.setProperty("--start-dim", String(Math.min(80, Math.max(0, dim)) / 100));
    // An own start picture with its headline burned in: title off, status
    // line down to a dot, and the picture filled or whole instead of drifting.
    st.dataset.title = p.start.show_title === false ? "off" : "";
    st.dataset.fit = ["cover", "contain"].includes(p.start.fit) ? p.start.fit : "drift";
    st.dataset.galleryStyle = p.start.gallery_style === "prominent" ? "prominent" : "";
    // Gallery, language and lock sized together; "l" unless m or xl is stored.
    st.dataset.cornerSize = ["m", "xl"].includes(p.start.corner_size) ? p.start.corner_size : "l";
    const eyebrow = t("start.eyebrow");      // "" = name of the event, "-" = no line
    $("#startEvent").textContent = eyebrow === "-" ? "" : (eyebrow || p.event.name || S.booth_name || "");
    const modes = $("#modes");
    modes.className = "modes n" + p.modes.length;
    modes.innerHTML = p.modes.map((m, i) => `<button class="mode" style="--i:${i}" data-mode="${esc(m)}">${iconFor(m)}<strong>${esc(t("mode." + m))}</strong><span>${esc(modeHint(m))}</span></button>`).join("");
    $("#galleryBtn").hidden = !(p.gallery_on_booth && p.start.show_gallery_button && S.gallery_count > 0);
    renderStartExtras(p.start);
    applyCustomLayout(st, p.start);
    const langs = p.languages || ["de"];
    $("#langBtn").hidden = langs.length < 2;
    const next = langs[(langs.indexOf(lang) + 1) % langs.length];
    $("#langBtn").textContent = next.toUpperCase();
    renderStartMedia();
  }

  // Anleitung, letzte Aufnahme, QR und Logo auf dem Startbildschirm. Jedes
  // Stück einzeln abschaltbar: An einer Box im Schaufenster hilft die
  // Anleitung, an einer Box im Kreis von zehn Leuten steht sie im Weg.
  // Ist die Anleitung gerade eingeblendet? Gilt nur für die Betriebsart
  // „knopf" und wird beim Zurück zum Start wieder vergessen.
  let stepsOffen = false;

  function renderStartExtras(start) {
    const st = start || {};
    // Was eingeschaltet ist, steht am Bildschirm als Merkmal — das Stylesheet
    // macht daraus Platz. Sonst läge die Anleitung auf den Knöpfen, wie man es
    // an vielen Boxen sieht.
    const schirm = $("#s-start");
    const frei = st.layout === "custom";

    // Drei Möglichkeiten: gar nicht, immer, oder erst auf Tippen. Das Letzte
    // ist für Boxen, an denen die Anleitung nur am Anfang des Abends gebraucht
    // wird und danach im Weg steht.
    const schritte = $("#startSteps");
    const zeilen = String(t("start.steps") || "").split("\n").map((z) => z.trim()).filter(Boolean);
    const modus = ["immer", "knopf"].includes(st.steps) ? st.steps : "aus";
    const moeglich = modus !== "aus" && zeilen.length > 0;
    $("#stepsBtn").hidden = !(moeglich && modus === "knopf");
    schritte.hidden = !(moeglich && (modus === "immer" || stepsOffen));
    if (moeglich) {
      const titel = String(t("start.steps_title") || "").trim();
      schritte.innerHTML = (titel ? `<li class="start-steps-title">${esc(titel)}</li>` : "")
        + zeilen.map((z) => `<li>${esc(z)}</li>`).join("");
    }

    // Die letzte Aufnahme: Sie kommt aus der Galerie der Box, nicht aus einem
    // eigenen Weg — im datensparsamen Betrieb gibt es dort nichts, und dann
    // steht hier auch nichts.
    const letzte = $("#startLast");
    const zeigen = st.show_last && !(S.privacy && S.privacy.print_only) && S.last_thumb;
    letzte.hidden = !zeigen;
    if (zeigen) letzte.querySelector("img").src = S.last_thumb + "?t=" + (S.gallery_count || 0);

    const qr = $("#startQr");
    qr.hidden = !(st.show_qr && S.gallery_url);
    if (!qr.hidden) {
      $("#startQrImg").src = "/api/qr.png?size=420&data=" + encodeURIComponent(S.gallery_url);
      qr.querySelector("span").textContent = t("start.qr_hint");
    }

    const logo = $("#startLogo");
    const datei = String(st.logo || "").trim();
    logo.hidden = !datei;
    if (datei) {
      logo.src = "/assets/" + encodeURIComponent(datei);
      logo.style.width = Math.max(4, Math.min(50, Number(st.logo_size) || 18)) + "%";
    }

    schirm.dataset.last = (!frei && !letzte.hidden) ? "1" : "";
    schirm.dataset.steps = (!frei && !schritte.hidden) ? "1" : "";
    schirm.dataset.stepsMode = modus;
    schirm.dataset.qr = (!frei && !qr.hidden) ? "1" : "";
    schirm.dataset.logo = (!frei && !logo.hidden) ? "1" : "";
  }

  // Frei platzierte Elemente (Drag-and-drop im Galerie-Admin): Werte in % der Bildschirmfläche
  // gleiche Vorgaben wie der Editor in der Galerie: Buttons nebeneinander unten
  function customDefaults(modes) {
    const n = Math.max(1, modes.length), gap = 1.6, w = (86 - gap * (n - 1)) / n;
    // Dieselben Vorgaben wie im Editor der Galerie — sonst springt alles,
    // sobald jemand dort das erste Mal etwas anfasst.
    const d = { title: { x: 7, y: 18, w: 56 }, gallery: { x: 70, y: 4, w: 12 },
                steps: { x: 7, y: 58, w: 30 }, last: { x: 2, y: 3, w: 10 },
                qr: { x: 82, y: 19, w: 14 }, logo: { x: 78, y: 86, w: 18 },
                status: { x: 72, y: 2, w: 26 } };
    modes.forEach((m, i) => { d[m] = { x: 7 + i * (w + gap), y: 64, w }; });
    return d;
  }
  function applyCustomLayout(st, start) {
    const custom = start.layout === "custom";
    const pos = Object.assign({}, custom ? customDefaults(S.profile.modes) : {}, (custom && start.positions) || {});
    const place = (el, key) => {
      const v = pos[key];
      if (!el) return;
      if (!v) { el.style.removeProperty("left"); el.style.removeProperty("top"); el.style.removeProperty("width"); el.classList.remove("is-placed"); return; }
      el.classList.add("is-placed");
      el.style.left = v.x + "%"; el.style.top = v.y + "%";
      if (v.w) el.style.width = v.w + "%";
    };
    st.querySelectorAll("#modes .mode").forEach((b) => place(b, b.dataset.mode));
    place(st.querySelector(".start-copy"), "title");
    place($("#galleryBtn"), "gallery");
    // Auch die neuen Stücke lassen sich frei setzen — sonst wäre „custom"
    // eine Wahl zwischen Freiheit und Anleitung.
    place($("#startSteps"), "steps");
    place($("#startLast"), "last");
    place($("#startQr"), "qr");
    place($("#startLogo"), "logo");
    place($("#statusline"), "status");
  }

  // A start medium: an uploaded file under /assets/, or one of the backgrounds
  // that ship with the booth ("builtin:abend.svg" -> /static/start/abend.svg).
  function startMediaUrl(name) {
    const m = /^builtin:([a-z0-9-]+)\.svg$/.exec(String(name || ""));
    return m ? "/static/start/" + m[1] + ".svg" : "/assets/" + encodeURIComponent(name);
  }

  function renderStartMedia() {
    const bg = $("#startBg");
    const media = S.start_media || [];
    clearInterval(mediaTimer);
    let idx = 0;
    const st = S.profile.start || {};
    const farbe = hex(st.bg_color, "");
    bg.style.backgroundColor = farbe;           // "" = theme.bg from the stylesheet
    const paint = () => {
      bg.classList.remove("is-live");
      if (!media.length && farbe) {             // a colour instead of the live picture
        bg.innerHTML = ""; bg.style.backgroundImage = "";
        return;
      }
      if (!media.length) {
        const u = `/api/live.jpg?t=${Date.now()}`;
        const pre = new Image();
        pre.onload = () => { bg.innerHTML = ""; bg.style.backgroundImage = cssUrl(u); bg.classList.add("is-live"); };
        pre.src = u;
        return;
      }
      const name = media[idx % media.length]; idx++;
      const url = startMediaUrl(name);
      if (/\.(mp4|webm)$/i.test(name)) {
        bg.style.backgroundImage = "";
        bg.innerHTML = `<video src="${esc(url)}" autoplay muted playsinline ${media.length === 1 ? "loop" : ""}></video>`;
        const v = bg.querySelector("video");
        if (media.length > 1) v.onended = paint;
      } else {
        bg.innerHTML = "";
        bg.style.backgroundImage = cssUrl(url);   // Bilder und animierte GIFs
      }
    };
    paint();
    const hasVideo = media.some((m) => /\.(mp4|webm)$/i.test(m));
    if (!media.length && !farbe) mediaTimer = setInterval(paint, 4000);
    else if (media.length > 1 && !hasVideo) mediaTimer = setInterval(paint, (S.profile.start.media_seconds || 8) * 1000);
  }

  // ── Bildschirmschoner ───────────────────────────────────
  // Steht die Box eine Weile ungenutzt herum, legt sich eine Diaschau über den
  // Startbildschirm: sie zieht Gäste an und zeigt, was schon entstanden ist.
  // Jede Berührung beendet sie sofort. Ist sie unsichtbar, laufen weder Timer
  // noch Abfragen — eine Box, an der niemand steht, soll die API in Ruhe lassen.
  const ATTRACT_RELOAD_MS = 300000;      // neue Aufnahmen alle fünf Minuten nachladen
  let attractTimer = null, attractTick = null, attractReload = null;
  let attractItems = [], attractIdx = 0, attractLayer = "B";
  let attractOn = false, attractSeq = 0, attractDismissed = 0;

  const attractCfg = () => (S && S.profile.attract) || {};
  const attractSecs = () => Math.max(2, Number(attractCfg().seconds_per_item) || 6) * 1000;
  const isClip = (name) => /\.(mp4|webm)$/i.test(name);

  function armAttract() {
    clearTimeout(attractTimer);
    const token = ++attractSeq;          // macht einen noch ladenden Start ungültig
    const cfg = attractCfg();
    if (attractOn || !S || S.locked || app.dataset.screen !== "start") return;
    if (cfg.enabled === false || cfg.source === "off") return;
    attractTimer = setTimeout(() => startAttract(token), Math.max(10, Number(cfg.after_seconds) || 120) * 1000);
  }

  async function startAttract(token) {
    if (attractOn || !S || S.locked || app.dataset.screen !== "start") return;
    const cfg = attractCfg();
    // „Nur Text“ läuft auf einer Fläche — oder über den gewählten Medien, wenn
    // welche da sind. Der Text liegt in beiden Fällen darüber.
    const plain = cfg.source === "blank" || (cfg.source === "text" && !(cfg.media || []).length);
    const items = plain ? [] : await attractSources();
    // Während des Ladens doch jemand da gewesen? Dann bleibt der Start aus.
    if (token !== attractSeq || attractOn || S.locked || app.dataset.screen !== "start") return;
    if (!items.length && !plain) return;    // nichts da: erst die nächste Nutzung versucht es wieder
    attractOn = true; attractItems = items; attractIdx = 0;
    applyAttractLook(cfg);
    $("#attract").hidden = false;
    clearInterval(mediaTimer);           // der Startbildschirm liegt darunter, sein Live-Bild braucht niemand
    paintAttract();
    if (items.length > 1) attractTick = setInterval(paintAttract, attractSecs());
    if (attractCfg().source === "recent") attractReload = setInterval(reloadAttract, ATTRACT_RELOAD_MS);
  }

  async function attractSources() {
    const cfg = attractCfg();
    if (cfg.source === "own" || cfg.source === "text") {
      // eigene Auswahl für den Schoner — unabhängig von den Startmedien
      const own = (cfg.media || []).map((n) => "/assets/" + encodeURIComponent(n));
      if (own.length) return own;
    }
    if (cfg.source === "recent") {
      const recent = await recentUrls();
      if (recent.length) return recent;
    }
    // Quelle "media" — oder leere/abgeschaltete Galerie: die Startmedien sind immer da
    return (S.start_media || []).map(startMediaUrl);
  }

  // Fläche, Abdunklung, Textfarbe und Lage des Hinweises kommen aus dem Profil.
  function applyAttractLook(cfg) {
    const box = $("#attract"), s = box.style;
    s.setProperty("--attract-color", cfg.color || "#000000");
    s.setProperty("--attract-text", cfg.text_color || "#ffffff");
    s.setProperty("--attract-dim", String(Math.min(80, Math.max(0, Number(cfg.dim) || 0)) / 100));
    s.setProperty("--attract-fit", cfg.fit === "contain" ? "contain" : "cover");
    s.setProperty("--attract-hint-scale", String(Math.min(250, Math.max(100, Number(cfg.hint_size) || 100)) / 100));
    box.dataset.source = cfg.source || "recent";
    // Ohne Medien dahinter wäre die Abdunklung nur ein grauer Schleier auf der Fläche.
    box.dataset.plain = (cfg.source === "blank" || (cfg.source === "text" && !(cfg.media || []).length)) ? "1" : "0";
    const style = cfg.show_hint === false ? "none" : (cfg.hint_style || "bar");
    box.dataset.hint = style;
    $("#attractHint").hidden = style === "none";
    $("#attractTitle").hidden = style !== "center" && cfg.source !== "text";
    $("#attractTitle").textContent = t("attract.title");
    $("#attractHint").textContent = t("attract.hint");
  }

  async function recentUrls() {
    try {
      const data = await api("/api/gallery?offset=0&limit=30");
      // Videos als Standbild: die Schleife soll ruhig bleiben und nichts Großes nachladen
      return (data.items || []).map((it) => (it.is_video ? it.thumb_url : it.result_url)).filter(Boolean);
    } catch (e) { return []; }           // Galerie aus oder Box beschäftigt
  }

  async function reloadAttract() {
    const urls = await recentUrls();
    if (!attractOn || !urls.length) return;
    attractItems = urls;
    // aus dem einen Bild von vorhin sind inzwischen mehrere geworden: weiterblättern
    if (!attractTick && attractItems.length > 1) attractTick = setInterval(paintAttract, attractSecs());
  }

  // attract.fit "auto": filled only when picture and screen share an
  // orientation (a portrait shot on a landscape screen is shown whole, list
  // item 32). "" leaves the fixed value of --attract-fit in charge.
  function attractFitFor(w, h) {
    if (attractCfg().fit !== "auto") return "";
    if (!w || !h) return "cover";
    return (w >= h) === (innerWidth >= innerHeight) ? "cover" : "contain";
  }

  // Zwei Ebenen, die sich abwechseln — so blendet ein Bild ins nächste über.
  function paintAttract() {
    if (!attractOn || !attractItems.length) return;   // Fläche/Text: nichts zu wechseln
    const src = attractItems[attractIdx % attractItems.length];
    attractIdx++;
    attractLayer = attractLayer === "A" ? "B" : "A";
    const next = $("#attract" + attractLayer), prev = $("#attract" + (attractLayer === "A" ? "B" : "A"));
    const swap = () => {
      if (!attractOn) return;
      next.classList.add("on"); prev.classList.remove("on");
      const old = prev.querySelector("video");
      if (old) old.pause();              // sonst dekodiert das alte Video unsichtbar weiter
    };
    if (isClip(src)) {
      next.style.backgroundImage = "";
      const muted = attractCfg().mute === false ? "" : "muted";
      next.innerHTML = `<video src="${esc(src)}" autoplay ${muted} loop playsinline></video>`;
      const vid = next.querySelector("video");
      vid.addEventListener("loadedmetadata", () => { vid.style.objectFit = attractFitFor(vid.videoWidth, vid.videoHeight); });
      swap();
    } else {
      next.innerHTML = "";
      const pre = new Image();           // erst laden, dann überblenden, sonst blitzt eine leere Fläche auf
      pre.onload = pre.onerror = () => {
        next.style.backgroundSize = attractFitFor(pre.naturalWidth, pre.naturalHeight);
        next.style.backgroundImage = cssUrl(src);
        swap();
      };
      pre.src = src;
    }
  }

  function stopAttract(rearm = true) {
    clearTimeout(attractTimer); clearInterval(attractTick); clearInterval(attractReload);
    attractTimer = attractTick = attractReload = null;
    attractSeq++;
    if (attractOn) {
      attractOn = false;
      $("#attract").hidden = true;
      for (const l of document.querySelectorAll(".attract-layer")) { l.classList.remove("on"); l.innerHTML = ""; l.style.backgroundImage = ""; l.style.backgroundSize = ""; }
      attractItems = []; attractIdx = 0;
      if (app.dataset.screen === "start") renderStartMedia();
    }
    if (rearm) armAttract();
  }

  // Die Berührung, die den Schoner wegwischt, darf keine Aufnahme starten:
  // der Klick darunter kommt erst nach dem Ausblenden an, darum die Sperrzeit.
  function dismissAttract() {
    if (!attractOn) return false;
    attractDismissed = Date.now();
    stopAttract();
    return true;
  }
  const attractJustDismissed = () => Date.now() - attractDismissed < 700;

  function home() {
    if (S && S.privacy && S.privacy.print_only && app.dataset.screen === "result") toast(t("privacy.wiped"));
    clearTimeout(resultTimer);
    // Wer mitten im Durchsehen zum Start zurückgeht, soll nicht kurz darauf
    // doch noch im Ergebnis landen.
    clearTimeout(reviewTimer); reviewGo = null;
    stepsOffen = false;                    // die Anleitung fängt jeden Gast von vorn an
    // Die Vorschaubilder der Serie dürfen nicht im Dokument stehen bleiben:
    // Sonst hängen Aufnahmen fremder Gäste noch im Speicher des nächsten.
    $("#reviewShots").innerHTML = "";
    clearTimeout(afterTimer); afterTimer = null;
    hidePhaseOverlay();
    $("#qrOverlay").hidden = true;
    $("#confirmOverlay").hidden = true;
    $("#printFailOverlay").hidden = true;
    clearInterval(printWatch);
    // Auch die Fenster, die über den Bildschirmen liegen. Bis zum 19.09.2026
    // blieben Tastatur und Vollbild stehen, wenn der Leerlauf nach Hause
    // schickte: Der nächste Gast tippte in die Tastatur des vorigen, und
    // „Weiter" lief in einen Durchgang, den es nicht mehr gab.
    $("#fullOverlay").hidden = true;
    $("#fullMedia").innerHTML = "";
    $("#mailOverlay").hidden = true;
    closeDraw(true);                       // and the strokes with it: the next guest starts blank
    if (!$("#textOverlay").hidden) {
      $("#textOverlay").hidden = true;
      const offen = gastWeiter; gastWeiter = null;
      if (offen) offen("");                // das wartende Versprechen auflösen
    }
    for (const v of document.querySelectorAll("#resultMedia video")) v.pause();
    // Whatever run was still going (a series left in the review, a greeting
    // prompt the idle timer closed) ends here: it may not go on over the start
    // screen, and the next guest must be able to start.
    lauf.abgebrochen = true;
    gate.reset();
    flow = {}; view = {};
    lang = startLang();
    applyTexts();
    applyControls();
    applyLayout();
    renderStart();
    show("start");
    startShownAt = Date.now();
  }
  // A double tap on "Fertig" (or "Zurück") sends the second tap to the start
  // screen — onto whatever tile lies under the finger. A start that soon after
  // home() is the echo of the tap that led here, not a new guest.
  let startShownAt = 0;
  const START_TAP_GRACE_MS = 500;
  const startTapTooSoon = () => Date.now() - startShownAt < START_TAP_GRACE_MS;

  // ── Vorbereitung ────────────────────────────────────────
  // ── Einwilligung je Durchgang ──────────────────────────
  // Two equal buttons, none preselected, the note underneath. Only a tap
  // chooses: the buzzer and the start keys show a hint (triggerPrimary).
  function consentOptions() { return (S && S.consent && S.consent.options) || []; }
  function askConsent(mode) {
    flow = { pendingMode: mode };
    const options = consentOptions();
    const box = $("#consentChoices");
    box.style.setProperty("--n", String(options.length));
    box.innerHTML = options.map((o) =>
      `<button class="consent-choice" data-consent="${esc(o)}"><b>${esc(t("consent." + o))}</b><small>${esc(t("consent." + o + ".hint"))}</small></button>`).join("");
    const notes = (S.consent && S.consent.note) || {};
    $("#consentNote").textContent = notes[lang] || notes.de || "";
    show("consent");
  }

  const FORMAT_MODES = ["photo", "boomerang"];
  // The card pre-selected for a boomerang: the operator's boomerang format
  // when it is offered (a guest who does not choose gets what they got
  // before), else the photo's default, else the first one offered.
  function clipDefaultFormat(p) {
    const own = (p.boomerang || {}).format;
    if (p.formats.includes(own)) return own;
    return p.formats.includes(p.default_format) ? p.default_format : p.formats[0];
  }

  // The picture of a layout card. A boomerang's sheet (boomerang.print) is
  // rendered with the boomerang's framing in the chosen format, so a 9:16
  // boomerang on a landscape sheet shows as it will print; the collage's
  // picture stands in until that one is there.
  function boomerangLayoutThumb(tp) {
    const own = ((S.layout_thumbs || {}).boomerang || {})[`${tp.id}@${flow.format}`];
    return own || tp.thumb || "";
  }
  function renderTemplateCards() {
    const boom = flow.mode === "boomerang";
    $("#templateChips").innerHTML = S.templates.map((tp) => {
      const land = tp.orientation === "landscape";
      const n = Number(tp.shots) || 1;
      const meta = (n === 1 ? t("setup.photos_one") : t("setup.photos_n", { n })) + " · " +
        t("orientation." + (land ? "landscape" : "portrait"));
      return layoutCard({ attr: "template", value: tp.id, pressed: tp.id === flow.template,
        name: shortLayoutName(tp.name), meta, thumb: boom ? boomerangLayoutThumb(tp) : (tp.thumb || ""),
        aspect: (tp.canvas && tp.canvas[0] / tp.canvas[1]) || (land ? 1.5 : 2 / 3), outline: tp });
    }).join("");
    wireLayoutCards($("#templateChips"));
  }

  // ── Frames for boomerang and video ("Rahmen", media/motion_frames.py) ──
  // The booth's offer for the mode: frames with a picture each, "Ohne
  // Rahmen", the default. With the guest's format choice on screen only the
  // frames of that format are shown; the frame's own format is the film's.
  function frameOffer(mode) {
    return ((S && S.motion_frames) || {})[mode] || { enabled: false, frames: [] };
  }
  function framesShown(mode, fmt, byFormat) {
    const o = frameOffer(mode);
    if (!o.enabled) return [];
    const list = o.frames.filter((f) => !byFormat || f.format === fmt);
    const out = list.map((f) => ({ id: f.id, name: f.name, format: f.format, window: f.window,
      canvas: f.canvas, thumb: f.thumb }));
    if (o.none || !out.length) out.push({ id: "none", name: t("setup.frame_none"), format: fmt, none: true });
    return out;
  }
  function currentFrame() {
    const id = flow.motionFrame;
    if (!id || id === "none") return null;
    return (frameOffer(flow.mode).frames || []).find((f) => f.id === id) || null;
  }
  function renderFrameCards() {
    const shown = flow.frames || [];
    $("#frameChips").innerHTML = shown.map((f) => {
      const ar = f.canvas ? f.canvas[0] / f.canvas[1] : aspectOf(f.format, 0.5625);
      return layoutCard({ attr: "frame", value: f.id, pressed: f.id === flow.motionFrame, name: f.name,
        meta: f.none ? t("setup.frame_none_hint") : t("format." + f.format), thumb: f.thumb || "", aspect: ar,
        outline: f.none ? { canvas: [ar * 1000, 1000], slots: [{ x: 0, y: 0, w: ar * 1000, h: 1000 }] }
          : { canvas: f.canvas, slots: [{ x: (f.canvas[0] - f.window[0]) / 2, y: (f.canvas[1] - f.window[1]) / 2,
              w: f.window[0], h: f.window[1] }] } });
    }).join("");
    wireLayoutCards($("#frameChips"));
  }
  // Which frames the set-up screen shows now; the pick stays when it is still among them.
  function chooseFrames(mode, byFormat) {
    const o = frameOffer(mode);
    flow.frames = framesShown(mode, flow.format, byFormat);
    const ids = flow.frames.map((f) => f.id);
    if (!ids.includes(flow.motionFrame)) flow.motionFrame = ids.includes(o.default) ? o.default : ids[0];
    return o.enabled && o.guest_choice && flow.frames.length > 1;
  }
  // The frames follow a format the guest picks: those of the new format, or none to choose.
  function framesFollowFormat() {
    if (!frameOffer(flow.mode).enabled) return;
    const show = chooseFrames(flow.mode, true);
    $("#groupFrame").hidden = !show;
    if (show) renderFrameCards();
  }

  function openSetup(mode, chosen) {
    // A second tap on the tile while its run is already starting: ignored.
    // Going on would overwrite `flow` under the running run (its uid with it).
    if (gate.busy("run")) return;
    if (S.locked) return toast(t("locked"));
    if (S.camera.state !== "ready") return toast(t(S.camera.error_code || "camera.off"));
    if (consentOptions().length >= 2 && !chosen) return askConsent(mode);
    const p = S.profile;
    flow = { mode, format: VIDEO_MODES.includes(mode) ? p[mode].format : p.default_format,
             template: S.templates[0] && S.templates[0].id, background: null, shot: 0,
             consent: chosen ? { option: chosen, lang } : null };
    // Consent is on but there is nothing to choose (no printer): the choice
    // screen is skipped, its note is not — it stands here, and the run says
    // it was shown.
    const notice = !!(S.consent && S.consent.notice_only);
    const notes = (S.consent && S.consent.note) || {};
    $("#consentNotice").hidden = !notice;
    $("#consentNotice").textContent = notice ? (notes[lang] || notes.de || "") : "";
    if (notice) flow.consent = { option: "notice", lang };
    // Jede Gruppe hat zwei Bedingungen: der Betreiber erlaubt die Wahl, und es
    // gibt überhaupt mehr als eine Möglichkeit.
    const may = p.guest_choice || {};
    const mayFormat = may.format !== false, mayTemplate = may.collage_template !== false;
    const mayBackground = may.background !== false;
    const hasScenes = S.chroma && S.chroma.enabled && S.chroma.guest_choice
                      && S.chroma.backgrounds.length > 1 && !VIDEO_MODES.includes(mode);
    // Photo and boomerang: the same format choice (guest_choice.format), the
    // same cards. boomerang.guest_choice still opens it for the boomerang
    // alone when the general switch is off. The film keeps the operator's.
    const showFormat = p.formats.length > 1 && ((mayFormat && FORMAT_MODES.includes(mode))
      || (mode === "boomerang" && !!(p.boomerang || {}).guest_choice));
    if (showFormat && mode === "boomerang") flow.format = clipDefaultFormat(p);
    // boomerang.print: the boomerang's sheet takes a collage layout, chosen
    // on the same cards (one layout: no choice).
    const sheetModes = mode === "collage" || (mode === "boomerang" && !!(p.boomerang || {}).print);
    const showTemplate = mayTemplate && sheetModes && S.templates.length > 1;
    const showBackground = mayBackground && mode === "collage" && S.backgrounds.length;
    // Frames: shown when the guest may choose and there is more than one
    // choice; one choice (or no guest choice) is taken without asking.
    flow.motionFrame = "";
    const showFrame = VIDEO_MODES.includes(mode) && chooseFrames(mode, showFormat);
    const needsChoice = showFormat || showTemplate || showBackground || hasScenes || showFrame;
    // A boomerang or film says how long it records ("Boomerang · 2 Sekunden").
    $("#setupTitle").textContent = VIDEO_MODES.includes(mode) ? `${t("mode." + mode)} · ${clipSecondsText(mode)}` : t("mode." + mode);
    $("#groupFormat").hidden = !showFormat;
    $("#s-setup .setup-scroll").dataset.pickers = String(Number(!!showFormat) + Number(!!showTemplate) + Number(!!showFrame));
    $("#groupTemplate").hidden = !showTemplate;
    $("#groupFrame").hidden = !showFrame;
    $("#groupBackground").hidden = !showBackground;
    const scenes = (S.chroma && S.chroma.enabled && S.chroma.guest_choice && !VIDEO_MODES.includes(mode))
      ? S.chroma.backgrounds : [];
    $("#groupChroma").hidden = !scenes.length;
    flow.chroma = scenes[0] || "";
    $("#disclaimer").hidden = !p.disclaimer.enabled;
    $("#goLabel").textContent = VIDEO_MODES.includes(mode) ? t("setup.go_video") : t("setup.go");
    // Formats: with a printer, the picture is the print card of that format;
    // without one it is the photo's own shape. A boomerang is no print card:
    // its cards always show the film's shape.
    const printing = !!(p.print && p.print.enabled) && mode === "photo";
    const formatThumbs = (S.layout_thumbs && S.layout_thumbs.formats) || {};
    $("#formatChips").innerHTML = p.formats.map((f) => {
      const ratio = aspectOf(f, 1);
      const sheet = printing && formatThumbs[f];
      return layoutCard({ attr: "format", value: f, pressed: f === flow.format,
        name: t("format." + f), meta: t("format." + f + ".hint"),
        thumb: sheet ? formatThumbs[f] : "", aspect: sheet ? (ratio > 1.05 ? 1.5 : 2 / 3) : ratio,
        outline: sheet ? null : { canvas: [ratio * 1000, 1000], slots: [{ x: 0, y: 0, w: ratio * 1000, h: 1000 }] } });
    }).join("");
    wireLayoutCards($("#formatChips"));
    renderTemplateCards();
    if (showFrame) renderFrameCards();
    $("#bgChips").innerHTML = `<button class="swatch" data-bg="" aria-pressed="true" style="background:#2a2a30"></button>` +
      S.backgrounds.map((bgn) => `<button class="swatch" data-bg="${esc(bgn)}" aria-pressed="false" style="background-image:${esc(cssUrl("/assets/" + encodeURIComponent(bgn)))}"></button>`).join("");
    $("#chromaChips").innerHTML = scenes.map((bg, i) => {
      const style = bg.startsWith("#") ? `background:${hex(bg, "#000000")}` : `background-image:${cssUrl("/assets/" + encodeURIComponent(bg))}`;
      return `<button class="swatch" data-scene="${esc(bg)}" aria-pressed="${i === 0}" style="${esc(style)}"></button>`;
    }).join("");
    applyFrame();
    // Ganz simpel: gibt es nichts zu wählen und keinen Hinweis, geht es direkt los.
    if (!needsChoice && !p.disclaimer.enabled && !notice) { show("setup"); return runCapture(); }
    show("setup");
  }

  // ── Layout cards: a picture of the printed sheet ───────
  // The booth renders each offered layout with its own renderer, sample
  // photos and the event's texts (layoutbilder.py). Until the picture is
  // there a shimmer stands in its place; if it does not come (a run is
  // going, an error), the sheet's outline is drawn from the template.
  function shortLayoutName(name) {
    // "Grün & Gold · quer · 3 Fotos" — photos and orientation stand below the name.
    const parts = String(name || "").split(/\s+·\s+/).filter((s) =>
      !/^\d+\s+(fotos?|photos?|shots?|aufnahmen)$/i.test(s) && !/^(quer|hoch|hochkant|landscape|portrait)$/i.test(s));
    return parts.join(" · ") || String(name || "");
  }
  function layoutOutline(tpl) {
    const [cw, ch] = ((tpl && tpl.canvas) || [1200, 1800]).map((v) => num(v, 1000));
    const slots = (tpl && tpl.slots) || [];
    const n = Math.max(1, Number(tpl && tpl.shots) || slots.length || 1);
    const r = Math.min(cw, ch) * 0.02;
    const boxes = (tpl && tpl.slot_boxes) || [];
    const rects = slots.map((s0, i) => {
      const b = boxes[i];
      const r0 = b ? { x: b[0], y: b[1], w: b[2], h: b[3], shot: s0.shot } : s0 || {};
      const s = { x: num(r0.x), y: num(r0.y), w: num(r0.w, 1), h: num(r0.h, 1), shot: r0.shot };
      const k = ((Number(s.shot) || 0) % n + n) % n;
      const fs = Math.min(s.w, s.h) * 0.32;
      return `<rect x="${num(s.x)}" y="${num(s.y)}" width="${num(s.w)}" height="${num(s.h)}" rx="${num(r)}"/>` +
        `<text x="${num(s.x + s.w / 2)}" y="${num(s.y + s.h / 2 + fs * 0.35)}" font-size="${num(fs)}">${k + 1}</text>`;
    }).join("");
    return `<svg class="lcard-outline" viewBox="0 0 ${cw} ${ch}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${rects}</svg>`;
  }
  function layoutCard(o) {
    const ar = Math.max(0.3, Math.min(3, Number(o.aspect) || 1));
    const label = [o.name, o.meta].filter(Boolean).join(", ");
    const pic = o.thumb
      ? `<span class="lcard-skel"></span><img alt="" decoding="async" src="${esc(o.thumb)}">`
      : layoutOutline(o.outline);
    return `<button class="lcard${o.thumb ? "" : " is-outline"}" data-${o.attr}="${esc(o.value)}" aria-pressed="${!!o.pressed}" aria-label="${esc(label)}" style="--ar:${ar.toFixed(4)}">` +
      `<span class="lcard-pic">${pic}<span class="lcard-outline-slot" hidden></span></span>` +
      `<span class="lcard-text"><b>${esc(o.name)}</b><small>${esc(o.meta || "")}</small></span>` +
      `<span class="lcard-check" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></span></button>`;
  }
  function wireLayoutCards(box) {
    const tplById = {};
    for (const tp of S.templates || []) tplById[tp.id] = tp;
    for (const card of box.querySelectorAll(".lcard")) {
      const img = card.querySelector("img");
      if (!img) continue;
      const fallback = () => {
        // Not there (yet): the outline now, one more try a little later.
        const slot = card.querySelector(".lcard-outline-slot");
        if (slot && slot.hidden) {
          const tpl = card.dataset.template ? tplById[card.dataset.template] : null;
          const ar = parseFloat(card.style.getPropertyValue("--ar")) || 1;
          slot.innerHTML = layoutOutline(tpl || { canvas: [ar * 1000, 1000], slots: [{ x: 0, y: 0, w: ar * 1000, h: 1000 }] });
          slot.hidden = false;
          card.classList.add("is-outline");
        }
        if (!img.dataset.retried) {
          img.dataset.retried = "1";
          setTimeout(() => { if (img.isConnected) img.src = img.src.split("&r=")[0] + "&r=1"; }, 4000);
        }
      };
      img.addEventListener("load", () => {
        card.classList.add("is-loaded");
        card.classList.remove("is-outline");
        const slot = card.querySelector(".lcard-outline-slot");
        if (slot) slot.hidden = true;
      });
      img.addEventListener("error", fallback);
      if (img.complete && img.naturalWidth) card.classList.add("is-loaded");
    }
    const picker = box.closest(".picker");
    if (picker && !picker.dataset.wired) {
      picker.dataset.wired = "1";
      box.addEventListener("scroll", () => pickerArrows(picker), { passive: true });
      if (window.ResizeObserver) new ResizeObserver(() => pickerArrows(picker)).observe(box);
    }
    requestAnimationFrame(() => {
      const on = box.querySelector('.lcard[aria-pressed="true"]');
      if (on) box.scrollLeft = centredLeft(box, on);
      pickerArrows(picker);
    });
  }
  // The row's scroll position that puts `card` in its middle.
  function centredLeft(box, card) {
    const b = box.getBoundingClientRect(), c = card.getBoundingClientRect();
    return Math.max(0, box.scrollLeft + (c.left - b.left) - (box.clientWidth - c.width) / 2);
  }
  // Arrows and soft edges only where there is more to see in that direction.
  function pickerArrows(picker) {
    if (!picker) return;
    const box = picker.querySelector(".cards");
    const max = box.scrollWidth - box.clientWidth;
    const left = box.scrollLeft > 4, right = box.scrollLeft < max - 4;
    picker.querySelector(".picker-arrow.prev").hidden = !left;
    picker.querySelector(".picker-arrow.next").hidden = !right;
    picker.dataset.more = (left ? "l" : "") + (right ? "r" : "");
  }
  function scrollPicker(btn) {
    const box = btn.closest(".picker").querySelector(".cards");
    box.scrollBy({ left: Number(btn.dataset.scroll) * box.clientWidth * 0.8, behavior: "smooth" });
  }
  // Arrow keys move the choice, but only for someone who reached the cards
  // with the keyboard (Tab, :focus-visible). A buzzer or presenter that sends
  // ArrowRight as its start key still starts the run, even after a tap on a
  // card left the focus there — the trigger keys are not touched.
  document.addEventListener("keydown", (e) => {
    if (!/^Arrow(Left|Right|Up|Down)$/.test(e.key)) return;
    const card = document.activeElement && document.activeElement.closest && document.activeElement.closest(".lcard");
    // controls.arrow_navigation (the default) does this and more: arrowKeys.
    if (arrowNavOn() || !card || app.dataset.screen !== "setup" || overlayOpen()) return;
    let visible = false;
    try { visible = card.matches(":focus-visible"); } catch (_) { visible = false; }
    if (!visible) return;
    const cards = [...card.parentElement.querySelectorAll(".lcard")];
    const step = /Left|Up/.test(e.key) ? -1 : 1;
    const next = cards[cards.indexOf(card) + step];
    e.preventDefault();
    e.stopImmediatePropagation();
    bumpTimers(true);
    if (!next) return;
    next.focus({ preventScroll: true });
    // Only the row scrolls (scrollIntoView would also shift the column it sits in).
    const box = next.parentElement;
    box.scrollTo({ left: centredLeft(box, next), behavior: "smooth" });
    next.click();
  }, true);

  // <spatial-nav> — pure, no DOM; booth/tests/test_arrow_navigation.py runs it with Node.
  // From the box `from` ([x, y, w, h]) in direction `dir` (left | right | up |
  // down): the index of the nearest box among `boxes` that lies that way, or -1.
  // "That way" means its centre lies beyond the centre of `from`. The gap
  // along the direction counts once, the offset sideways (0 while the two
  // overlap on that axis) twice, so the box straight ahead beats a nearer one
  // off to the side; the distance of the centres breaks a tie.
  function nearestInDirection(from, boxes, dir) {
    const [fx, fy, fw, fh] = from;
    const fcx = fx + fw / 2, fcy = fy + fh / 2;
    const offset = (a0, a1, b0, b1) => Math.max(0, b0 - a1, a0 - b1);
    let best = -1, bestScore = Infinity;
    (boxes || []).forEach((b, i) => {
      if (!b) return;
      const [x, y, w, h] = b;
      if (x === fx && y === fy && w === fw && h === fh) return;
      const cx = x + w / 2, cy = y + h / 2;
      let along, side;
      if (dir === "right") { if (cx <= fcx + 1) return; along = Math.max(0, x - (fx + fw)); side = offset(fy, fy + fh, y, y + h); }
      else if (dir === "left") { if (cx >= fcx - 1) return; along = Math.max(0, fx - (x + w)); side = offset(fy, fy + fh, y, y + h); }
      else if (dir === "down") { if (cy <= fcy + 1) return; along = Math.max(0, y - (fy + fh)); side = offset(fx, fx + fw, x, x + w); }
      else if (dir === "up") { if (cy >= fcy - 1) return; along = Math.max(0, fy - (y + h)); side = offset(fx, fx + fw, x, x + w); }
      else return;
      const score = along + 2 * side + 0.001 * Math.hypot(cx - fcx, cy - fcy);
      if (score < bestScore) { bestScore = score; best = i; }
    });
    return best;
  }
  // </spatial-nav>

  // <preview-geometry> — pure, no DOM; booth/tests/test_setup_preview_matches_result.py
  // runs it with Node against the server's own crop functions.
  // Which part of the live picture ends up in the result, for mode, format,
  // collage template and shot. The server's rules, said once for the kiosk:
  //   photo, gif        compose.crop_to_aspect(format), centred
  //   boomerang, video  boomerang.layout_frame: "crop" crops like a photo;
  //                     "blur" keeps the whole frame on a blurred backdrop,
  //                     unless the aspects already agree within 0.05
  //   collage           vorlagen_render.passt_ins_feld, for the slot this
  //                     shot goes into: cropped when camera and slot lie the
  //                     same way (or the fit is "beschneiden" — then lifted
  //                     to 12 % for a portrait shot in a landscape slot),
  //                     otherwise the whole picture with the sheet around it
  // Set-up and countdown both lay out the live picture with this, so the
  // guest sees the result's framing before the countdown, not only during it.
  function aspectOf(v, dflt) {
    if (typeof v === "number" && v > 0) return v;
    const m = /^\s*([\d.]+)\s*:\s*([\d.]+)\s*$/.exec(String(v || ""));
    const r = m ? Number(m[1]) / Number(m[2]) : NaN;
    return Number.isFinite(r) && r > 0 ? r : (dflt || 0.8);
  }
  function lieOf(w, h) { return w > h ? "landscape" : (h > w ? "portrait" : "square"); }
  const SLOT_FITS = ["auto", "beschneiden", "einpassen"];        // vorlagen_render.EINPASSEN
  const FOCUS_Y_PORTRAIT_IN_LANDSCAPE = 0.12;                     // vorlagen_render.FOKUS_Y_HOCH_IN_QUER
  // The slot a shot lands in: the server puts shots[slot.shot % shots] into
  // every slot; the first slot that takes this shot is the one shown.
  function slotForShot(tpl, shot) {
    const slots = (tpl && tpl.slots) || [];
    const n = Math.max(1, Number(tpl && tpl.shots) || slots.length || 1);
    const k = ((Number(shot) || 0) % n + n) % n;
    return slots.find((s) => ((Number(s.shot) || 0) % n + n) % n === k) || slots[0] || null;
  }
  function previewRule(mode, fmt, tpl, shot, profile, srcW, srcH) {
    const p = profile || {};
    const cover = (aspect, fy) => ({ aspect, fit: "cover", fx: 0.5, fy: fy == null ? 0.5 : fy, bars: "" });
    const contain = (aspect, bars) => ({ aspect, fit: "contain", fx: 0.5, fy: 0.5, bars });
    if (mode === "collage") {
      const slot = slotForShot(tpl, shot);
      if (!slot || !(slot.w > 0 && slot.h > 0)) return cover(aspectOf(tpl && tpl.slot_aspect, 0.8));
      const aspect = slot.w / slot.h;
      let fit = String(slot.fit || p.collage_fit || "auto");
      if (!SLOT_FITS.includes(fit)) fit = "auto";
      const same = lieOf(srcW, srcH) === lieOf(slot.w, slot.h);
      if (fit === "beschneiden" || (fit === "auto" && same)) {
        return cover(aspect, !same && lieOf(srcW, srcH) === "portrait" ? FOCUS_Y_PORTRAIT_IN_LANDSCAPE : 0.5);
      }
      return contain(aspect, "sheet");
    }
    const aspect = aspectOf(fmt);
    if (mode === "boomerang" || mode === "video") {
      // record_clip passes the setting as it is; a missing one means "crop" for
      // the boomerang (owner's decision 15.11.2026) and "blur" for the film
      const cfg = p[mode] || {};
      const layout = cfg.layout === undefined ? (mode === "boomerang" ? "crop" : "blur") : cfg.layout;
      if (layout === "blur" && Math.abs(srcW / srcH - aspect) >= 0.05) return contain(aspect, "blur");
    }
    return cover(aspect);
  }
  // [x, y, w, h] of the source the result keeps — crop_to_aspect's arithmetic.
  function cropRect(rule, srcW, srcH) {
    if (rule.fit !== "cover") return [0, 0, srcW, srcH];
    let nw, nh;
    if (srcW / srcH > rule.aspect) { nw = Math.round(srcH * rule.aspect); nh = srcH; } else { nw = srcW; nh = Math.round(srcW / rule.aspect); }
    return [Math.round((srcW - nw) * rule.fx), Math.round((srcH - nh) * rule.fy), nw, nh];
  }
  // In an area of areaW x areaH: the box with the result's aspect, as large as
  // fits and centred, and where the whole live picture goes so that the kept
  // part fills the box exactly (cover) or sits whole inside it (contain).
  function previewLayout(rule, srcW, srcH, areaW, areaH) {
    let bw = areaW, bh = areaW / rule.aspect;
    if (bh > areaH) { bh = areaH; bw = areaH * rule.aspect; }
    const box = [(areaW - bw) / 2, (areaH - bh) / 2, bw, bh];
    let img;
    if (rule.fit === "cover") {
      const c = cropRect(rule, srcW, srcH), s = bw / c[2];
      img = [box[0] - c[0] * s, box[1] - c[1] * s, srcW * s, srcH * s];
    } else {
      const s = Math.min(bw / srcW, bh / srcH);
      img = [box[0] + (bw - srcW * s) / 2, box[1] + (bh - srcH * s) / 2, srcW * s, srcH * s];
    }
    return { box, img };
  }
  // </preview-geometry>

  // <processing-view> — pure; booth/tests/test_processing_screen.py
  // The processing screen shows the run's last picture in the shape of the
  // result and cropped or whole like it: the same rule as the live preview.
  // No picture (size unknown): null, and the screen shows only the ring.
  function processingView(mode, fmt, tpl, shot, profile, srcW, srcH) {
    if (!(srcW > 0 && srcH > 0)) return null;
    const rule = previewRule(mode, fmt, tpl, shot, profile, srcW, srcH);
    return { aspect: rule.aspect, fit: rule.fit, fy: rule.fy };
  }
  // </processing-view>

  // Processing: the mode's words and icon at once; the picture fades in once
  // it has loaded — the last shot, or for a film the last live picture.
  function showProcessing() {
    const mode = flow.mode;
    $("#processingText").textContent = t("processing." + mode)
      || (VIDEO_MODES.includes(mode) ? t("processing.video") : t("processing.photo"));
    $("#procIcon").innerHTML = iconFor(mode) || ICONS.photo;
    const still = $("#procStill");
    still.hidden = true;
    still.classList.remove("is-ready");
    const last = flow.lastShot || null;
    const src = VIDEO_MODES.includes(mode) ? `/api/live.jpg?t=${Date.now()}` : (last && last.url);
    if (!src) return;
    const uid = flow.uid;
    const pic = new Image();
    pic.onload = () => {
      if (flow.uid !== uid || app.dataset.screen !== "processing") return;
      const [fmt, prof] = previewInput();
      const v = processingView(mode, fmt, currentTemplate(), last ? last.index : 0, prof,
        pic.naturalWidth, pic.naturalHeight);
      if (!v) return;
      still.style.setProperty("--proc-ar", String(v.aspect));
      const [bg, fg] = still.querySelectorAll("img");
      bg.src = fg.src = pic.src;
      fg.style.objectFit = v.fit;
      fg.style.objectPosition = `50% ${Math.round(v.fy * 100)}%`;
      still.hidden = false;
      requestAnimationFrame(() => requestAnimationFrame(() => still.classList.add("is-ready")));
    };
    pic.src = src;
  }

  function currentTemplate() {
    return (S.templates || []).find((x) => x.id === flow.template) || (S.templates || [])[0] || null;
  }
  // Places one live picture. `frame` gets the box (set-up, countdown in a
  // frame) or, full screen, the frame is the screen and `guide` marks the box.
  // Until the first frame of the stream has arrived its size is unknown: the
  // box is laid out for a 3:2 camera and done again on the frame's "load".
  // With a frame, the live picture shows what fills its window: the window's
  // aspect, cropped ("cover"), whatever the mode's own layout says.
  function previewInput() {
    const fr = VIDEO_MODES.includes(flow.mode) ? currentFrame() : null;
    if (!fr) return [flow.format, S.profile];
    return [`${fr.window[0]}:${fr.window[1]}`, { ...S.profile, [flow.mode]: { ...(S.profile[flow.mode] || {}), layout: "crop" } }];
  }
  function placeLive(img, frame, guide, area) {
    const nw = img.naturalWidth || 3, nh = img.naturalHeight || 2;
    const [fmt, prof] = previewInput();
    const rule = previewRule(flow.mode, fmt, currentTemplate(), flow.shot || 0, prof, nw, nh);
    const lay = previewLayout(rule, nw, nh, Math.max(1, area[0]), Math.max(1, area[1]));
    const [bx, by, bw, bh] = lay.box;
    let ox = 0, oy = 0;
    if (guide) {
      guide.style.setProperty("--gw", bw + "px");
      guide.style.setProperty("--gh", bh + "px");
      frame.style.width = frame.style.height = "";
      // Without the marking, what lies outside the result is not shown at all.
      img.style.clipPath = guide.hidden ? `inset(${by - lay.img[1]}px ${lay.img[0] + lay.img[2] - bx - bw}px ${lay.img[1] + lay.img[3] - by - bh}px ${bx - lay.img[0]}px)` : "";
    } else {
      frame.style.width = bw + "px";
      frame.style.height = bh + "px";
      img.style.clipPath = "";
      ox = bx; oy = by;
    }
    frame.dataset.bars = rule.bars;
    Object.assign(img.style, { left: (lay.img[0] - ox) + "px", top: (lay.img[1] - oy) + "px",
                               width: lay.img[2] + "px", height: lay.img[3] + "px" });
    img.dataset.laid = nw + "x" + nh;
  }
  function contentBox(el) {
    const cs = getComputedStyle(el);
    return [el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
            el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)];
  }

  function applyFrame() {
    if (!S || !flow.mode) return;
    const cd = S.profile.countdown || {};
    const full = (cd.live_view || "fullscreen") !== "frame";
    const cap = $("#s-capture");
    cap.dataset.live = full ? "fullscreen" : "frame";
    cap.dataset.liveoff = cd.live_view === "off" ? "1" : "0";   // counted without the live picture
    // Vollbild wie dslrBooth: Live-Bild füllt den Schirm, der Ausschnitt des Ergebnisses wird markiert
    const guide = $("#cropGuide");
    guide.hidden = !(full && cd.crop_guide !== false);
    // One function for both screens: set-up == countdown == result.
    placeLive($("#setupLive"), $("#setupFrame"), null, contentBox($(".setup-preview")));
    const capFrame = $("#captureFrame");
    if (full) placeLive($("#captureLive"), capFrame, guide, [capFrame.clientWidth, capFrame.clientHeight]);
    else placeLive($("#captureLive"), capFrame, null, contentBox($("#s-capture .capture")));
  }
  window.addEventListener("resize", applyFrame);
  // placeLive() works in CSS pixels of the boxes it is given. In the own
  // window those boxes can change without a "resize" of the page: a screen
  // shown after being hidden (its box was 0 x 0 when last laid out), the
  // window going full screen after the splash, a move to a monitor with
  // another scale. So the boxes themselves are watched, and the pixel ratio.
  if (window.ResizeObserver) {
    let pending = 0;
    const seen = new WeakMap();
    const ro = new ResizeObserver((entries) => {
      let changed = false;
      for (const e of entries) {
        const r = e.contentRect, key = Math.round(r.width) + "x" + Math.round(r.height);
        if (seen.get(e.target) !== key) { seen.set(e.target, key); changed = true; }
      }
      if (changed && !pending) pending = requestAnimationFrame(() => { pending = 0; applyFrame(); });
    });
    for (const sel of [".setup-preview", "#s-capture .capture", "#captureFrame"]) {
      const el = document.querySelector(sel);
      if (el) ro.observe(el);
    }
  }
  (function watchPixelRatio() {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    const again = () => { applyFrame(); watchPixelRatio(); };
    if (mq.addEventListener) mq.addEventListener("change", again, { once: true });
  })();
  // A new stream (or a camera with another live-view size) is laid out again.
  for (const id of ["#setupLive", "#captureLive"]) {
    $(id).addEventListener("load", (e) => {
      const img = e.target;
      if (img.naturalWidth && img.dataset.laid !== img.naturalWidth + "x" + img.naturalHeight) applyFrame();
    });
  }
  function press(container, attr, value) {
    for (const el of container.querySelectorAll(`[data-${attr}]`)) el.setAttribute("aria-pressed", el.dataset[attr] === value);
  }

  // ── Aufnahme ────────────────────────────────────────────
  // Cancelling during countdown and recording (countdown.cancel). Every run
  // has its own token: the cancel button marks it, and each step of the
  // series looks at it before going on. A new run gets a new token, so a late
  // answer of the cancelled run can never steer the next guest's run.
  class Abgebrochen extends Error {}
  let lauf = { abgebrochen: false };

  // <run-gate> — pure, no DOM; booth/tests/test_one_run_at_a_time.py runs it.
  // One run at a time. A run can be started from the mode tile (set-up skipped
  // when there is nothing to choose), "Los geht's", the buzzer, the start keys,
  // a key mapped to a mode, the remote trigger, "Nochmal" and a reshoot from
  // the review. On 15.11.2026 at the real booth two of them met: the GIF run
  // the tile had already started and a tap on "Los geht's" 1.2 s later — two
  // sessions, two countdowns counting over each other (5, 3, 4, 2 …), the
  // camera fought over and an error at shot 4 of 6. Every one of those ways
  // asks here first; a second start while a run is being set up or running is
  // dropped, never queued. home() opens the gate again (and ends the old run).
  function makeGate() {
    const held = new Map();
    let seq = 0;
    return {
      enter(key) { if (held.has(key)) return null; seq += 1; held.set(key, seq); return seq; },
      leave(key, token) { if (held.get(key) === token) held.delete(key); },
      busy(key) { return held.has(key); },
      reset() { held.clear(); },
    };
  }
  // The seconds a shot is counted down. The first shot of every mode gets the
  // full countdown.first; the following shots of a GIF the gap gif.interval
  // (1 s by default — by design, the GIF is a quick series), the others
  // countdown.next. A single picture taken again (review, "Nochmal") is a new
  // start for the guest: it never gets the short GIF gap.
  function shotSeconds(mode, index, cd, gif, reshoot) {
    const num = (v, d) => { const n = Number(v); return Number.isFinite(n) && n >= 0 ? n : d; };
    // countdown.first/next are 1–20 on every side; a profile saved before
    // that rule (0 allowed) must not fire without a countdown.
    const secs = (v, d) => Math.min(20, Math.max(1, num(v, d)));
    if (index <= 1) return secs((cd || {}).first, 3);
    if (mode === "gif" && !reshoot) return num((gif || {}).interval, 1);
    return secs((cd || {}).next, 2);
  }
  // The digits a countdown shows and how long each stays, in tenths. Whole
  // digits only: a GIF gap of 1.5 s used to read "1.5", "0.5". The fraction is
  // given to the first digit, so the total time is exactly the setting.
  function countdownSteps(seconds) {
    const tenths = Math.max(0, Math.round(Number(seconds || 0) * 10));
    const steps = [];
    let left = tenths;
    while (left > 0) {
      const digit = Math.ceil(left / 10);
      const dauer = left - (digit - 1) * 10;
      steps.push([digit, dauer]);
      left -= dauer;
    }
    return steps;
  }
  // A GIF is a quick series: between its shots there is only the gap
  // (gif.interval) and the shot itself — no picture shown for
  // countdown.review_seconds, no "Bitte lächeln" after the first, no
  // "between" medium. Measured at the booth PC (15.11.2026): a shot every
  // 5.6 s at an interval of 1 s; the floor is now interval + the camera's
  // own time (+ the flash's recycle time, if one is set). The strip still
  // fills shot by shot. A single picture taken again is shown as before.
  function quickSeries(mode, reshoot) {
    return mode === "gif" && !reshoot;
  }
  // The white of a shot stands until the shot's answer, but never longer than
  // this: a camera that hangs must not leave the guests staring into white.
  const WHITE_CAP_MS = 4000;
  // Resolves when `promise` settles (either way) or after `capMs`, whichever
  // comes first. Never rejects: the caller awaits the promise itself after.
  function untilSettledOr(promise, capMs, wait) {
    return Promise.race([Promise.resolve(promise).then(() => "answer", () => "answer"),
      wait(capMs).then(() => "cap")]);
  }
  // </run-gate>
  const gate = makeGate();
  function pruefeAbbruch(meiner) { if (meiner.abgebrochen) throw new Abgebrochen("cancelled"); }

  function zeigeCaptureAbbruch(retakeFlow) {
    // A retake from the result screen belongs to a finished run: that one is
    // deleted on the result screen, not thrown away here.
    $("#captureCancel").hidden = !(S.profile.countdown.cancel && !retakeFlow);
  }

  function captureAbbrechen() {
    if (lauf.abgebrochen) return;
    lauf.abgebrochen = true;
    const uid = flow.uid;
    for (const id of ["#countdown", "#countdownFull"]) { $(id).classList.remove("on"); $(id).innerHTML = ""; }
    $("#rec").classList.remove("on");
    $("#smilePic").hidden = true;
    $("#screenFlash").hidden = true;
    $("#captureCancel").hidden = true;
    shotStrip(0);
    $("#shotcount").textContent = "";
    // Not awaited: the abort waits on the booth for a shot that is still being
    // taken, the guest should not. Files go in every mode, nothing is printed.
    if (uid) api(`/api/session/${uid}/abort`, { method: "POST" }).catch(() => {});
    reviewGo = null;
    home();
  }

  // ── How long a boomerang or film records ───────────────
  // boomerang.seconds / video.seconds: shown on the mode tile and in the
  // set-up title before the start, counted down in a ring while recording.
  function clipSeconds(mode) {
    const v = Number(((S && S.profile[mode]) || {}).seconds);
    return v > 0 ? v : 2;
  }
  function clipSecondsText(mode) {
    const n = Math.round(clipSeconds(mode) * 10) / 10;
    if (n === 1) return t("clip.second");
    return t("clip.seconds", { n: n.toLocaleString(lang === "de" ? "de-DE" : "en-GB") });
  }
  function modeHint(m) {
    const hint = t("mode." + m + ".hint");
    if (!VIDEO_MODES.includes(m)) return hint;
    return hint ? `${hint} · ${clipSecondsText(m)}` : clipSecondsText(m);
  }
  // The ring empties over `seconds`; the number inside says the seconds left.
  // It stands until the booth answers the recording (the camera has stopped
  // then — the film is made afterwards), so it ends with the recording.
  // Reduced motion: no gliding, the ring moves in whole seconds.
  function recordingIndicator(seconds) {
    const rec = $("#rec"), arc = rec.querySelector(".rec-arc"), left = rec.querySelector(".rec-left");
    const calm = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const total = Math.max(0.5, Number(seconds) || 2) * 1000;
    const whole = Math.max(1, Math.ceil(total / 1000));
    const start = performance.now();
    const tick = () => {
      const used = Math.min(1, (performance.now() - start) / total);
      const rest = Math.max(0, Math.ceil((total - (performance.now() - start)) / 1000));
      left.textContent = String(rest);
      arc.style.strokeDashoffset = String(100 * (calm ? (whole - rest) / whole : used));
    };
    rec.setAttribute("aria-label", t("capture.left", { n: String(whole) }));
    rec.classList.add("on");
    tick();
    const timer = setInterval(tick, calm ? 250 : 40);
    return {
      stop() {
        clearInterval(timer);
        rec.classList.remove("on");
        arc.style.strokeDashoffset = "0";
        left.textContent = "";
      },
    };
  }

  async function countdown(seconds) {
    const meiner = lauf;
    const cd = S.profile.countdown;
    const full = cd.style === "fullscreen";
    const el = full ? $("#countdownFull") : $("#countdown");
    // Look from the profile: size m | l | xl, an own colour, number or ring.
    const display = cd.display === "ring" ? "ring" : "number";
    el.dataset.size = ["m", "xl"].includes(cd.size) ? cd.size : "";
    const farbe = hex(cd.color, "");
    el.dataset.color = farbe ? "1" : "";
    el.style.setProperty("--cd-color", farbe || "var(--accent)");
    el.dataset.display = display;
    el.innerHTML = "";
    let arc = null;
    if (display === "ring" && seconds > 0) {
      // pathLength="100": the dash offset is simply the share of time used up.
      el.innerHTML = `<svg class="cd-ring" viewBox="0 0 100 100" aria-hidden="true">`
        + `<circle class="cd-track" cx="50" cy="50" r="46"/>`
        + `<circle class="cd-arc" cx="50" cy="50" r="46" pathLength="100"/></svg><b></b>`;
      arc = el.querySelector(".cd-arc");
      void arc.getBoundingClientRect();        // start full, so the first second glides
    }
    el.classList.add("on");
    const steps = countdownSteps(seconds);
    const total = steps.reduce((a, s) => a + s[1], 0) || 1;
    let done = 0;
    for (const [n, tenths] of steps) {
      if (meiner.abgebrochen) break;
      done += tenths;
      if (arc) {
        // A new <b> each second restarts its pop; the arc loses its share.
        el.querySelector("b").outerHTML = `<b>${n}</b>`;
        arc.style.strokeDashoffset = String(100 * done / total);
      } else {
        el.innerHTML = `<b>${n}</b>`;
      }
      // countdown.sound bleibt der Schalter für den Zählton — für Datei wie Piepser
      if (S.profile.countdown.sound) playSound("countdown", () => beep(n === 1 ? 1320 : 880));
      // in tenths, so a cancelled countdown stops at once instead of beeping on
      for (let k = 0; k < tenths && !meiner.abgebrochen; k++) await sleep(100);
    }
    el.innerHTML = ""; el.classList.remove("on");
    pruefeAbbruch(meiner);
  }
  // hold = true: the white stands until flashFade() (the shot's answer); without
  // it, the short 0.6 s shimmer as before.
  function flash(hold) {
    playSound("shutter", shutterSound);
    // Steht der Bildschirm ohnehin als Licht, wäre der Weißschimmer nur ein
    // zweites Weiß über dem ersten — dann bleibt er aus.
    if (schirmblitzAn()) return;
    const f = $("#flash"); f.classList.remove("on", "hold"); void f.offsetWidth;
    f.classList.add(hold ? "hold" : "on");
  }
  function flashFade() {
    const f = $("#flash");
    if (!f.classList.contains("hold")) return;
    f.classList.remove("hold"); void f.offsetWidth; f.classList.add("on");
  }

  // ── Der Bildschirm als Blitz ────────────────────────────
  // Boxen ohne Blitzgerät haben nur das Panel. Damit das etwas nützt, muss die
  // Fläche stehen, *bevor* ausgelöst wird, und noch kurz danach: Die Kamera
  // belichtet nicht in dem Augenblick, in dem der Knopf gedrückt wird.
  function schirmblitzAn() { return !!((S && S.profile && S.profile.screen_flash) || {}).enabled; }
  const SF = () => (S && S.profile && S.profile.screen_flash) || {};
  const sfZahl = (wert, fallback, max) =>
    Math.max(0, Math.min(max, Number(wert ?? fallback) || 0));

  // The smile text stays on the white surface: the guests keep looking up,
  // and dark letters cost next to no light. A picture would, so it does not.
  function smileText() {
    const cd = (S && S.profile && S.profile.countdown) || {};
    return String(cd.smile_media || "").trim() ? "" : String(cd.smile_text || "").trim().slice(0, 40);
  }

  async function schirmblitzHoch() {
    if (!schirmblitzAn()) return;
    const sf = SF();
    const el = $("#screenFlash");
    el.textContent = smileText();
    // Heller und dunkler heißt hier: mehr oder weniger Licht. Deshalb wird die
    // Farbe zu Schwarz hin gemischt und nicht bloß durchsichtig gemacht —
    // durchsichtig ließe die dunkle Oberfläche durchscheinen und leuchtet nicht.
    const anteil = Math.max(20, Math.min(100, Number(sf.brightness ?? 100) || 100)) / 100;
    el.style.background = mix("#000000", hex(sf.color, "#ffffff"), anteil);
    el.hidden = false;
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    await sleep(sfZahl(sf.lead_ms, 250, 2000));
  }

  // Called once the shot has answered (or WHITE_CAP_MS passed). The surface
  // stood through the exposure already; hold_ms is now the least it stands
  // from the request on, not a pause added after the picture is there.
  async function schirmblitzRunter(seit) {
    if (!schirmblitzAn()) return;
    const hold = sfZahl(SF().hold_ms, 400, 2000);
    await sleep(Math.max(0, hold - (seit ? Date.now() - seit : 0)));
    $("#screenFlash").hidden = true;
  }

  // Kurz vor dem Auslösen ein Bild: „Bitte lächeln!". Ohne das schauen Gäste
  // beim letzten Zählschritt nach unten aufs Display statt in die Kamera —
  // dslrBooth macht es genauso, und man sieht den Unterschied auf dem Abzug.
  async function bitteLaecheln() {
    const cd = (S && S.profile && S.profile.countdown) || {};
    const datei = String(cd.smile_media || "").trim();
    const text = String(cd.smile_text || "").trim().slice(0, 40);   // a word or an emoji instead of a picture
    if (!datei && !text) return;
    const dauer = Math.max(200, Math.min(3000, Number(cd.smile_ms || 700)));
    const el = $("#smilePic");
    el.style.backgroundImage = datei ? `url("/assets/${encodeURIComponent(datei)}")` : "none";
    el.textContent = datei ? "" : text;
    el.hidden = false;
    await sleep(dauer);
    el.hidden = true;
  }

  // What to do for the next picture of a series — a line of text, because
  // the speaker of a booth is often off. Only if the event switched it on.
  function posenZeile(index, needed) {
    const an = !!((S.profile.countdown || {}).poses);
    if (!an || needed < 2 || index < 2) return "";
    return "  ·  " + t("pose." + (((index - 2) % 5) + 1));
  }

  async function captureShot(index, needed, retake, ersetzt, nochmal = 0) {
    const meiner = lauf;
    // A collage's shots can go into slots of different shapes: the countdown
    // shows each shot the framing of its own slot.
    flow.shot = index - 1;
    applyFrame();
    const cd = S.profile.countdown;
    const pose = posenZeile(index, needed);
    $("#shotcount").textContent = (needed > 1 ? `${index} / ${needed}` : "") + pose;
    $("#shotcount").classList.toggle("has-pose", !!pose);     // a prompt is read from two metres: large and in the middle
    // GIF: the gap between shots is its own setting, not the countdown
    await countdown(shotSeconds(flow.mode, index, cd, S.profile.gif, !!(retake || ersetzt)));
    const schnell = quickSeries(flow.mode, !!(retake || ersetzt));
    if (!(schnell && index > 1)) await bitteLaecheln();
    pruefeAbbruch(meiner);
    await schirmblitzHoch();
    // The white covers the real exposure. The shutter request takes 1.7–2.4 s
    // on the booth PC (focus, press, download); a 0.6 s white at its start was
    // over before the camera fired ("the screen turns white BEFORE the photo").
    // Now it goes up right before the request and stays until the answer —
    // at most WHITE_CAP_MS — then fades.
    const weissSeit = Date.now();
    flash(true);
    const shot = api(`/api/session/${flow.uid}/shot`, {
      method: "POST", body: { retake: !!retake, replace: ersetzt || 0 } });
    try {
      await untilSettledOr(shot, WHITE_CAP_MS, sleep);
    } finally {
      // Auch wenn die Aufnahme scheitert: Die Leuchtfläche darf nicht stehen
      // bleiben, sonst blendet die Box den Gast, bis jemand kommt.
      flashFade();
      await schirmblitzRunter(weissSeit);
    }
    let r;
    try {
      r = await shot;
    } catch (e) {
      // "No focus" refused this one shot after the booth had already pressed
      // again without autofocus; the camera is fine. The guest gets the same
      // shot once more (a hint, a new countdown) instead of the error screen
      // that ended the whole run. Once: a second refusal is the error screen.
      if (e && e.status === 503 && e.message === "camera.focus" && nochmal < 1) {
        pruefeAbbruch(meiner);
        toast(t("camera.focus"));
        return captureShot(index, needed, retake, ersetzt, nochmal + 1);
      }
      throw e;
    }
    pruefeAbbruch(meiner);
    flow.lastShot = { url: r.shot_url, index: index - 1 };
    if (!ersetzt) shotStrip(needed, index, r.shot_url);
    let review_ms = 0;
    if (!schnell) {
      const review = $("#shotReview");
      review.src = r.shot_url;
      review.classList.add("on");
      // Between two shots of a series: the operator's "one more!" for the time
      // the shot would be shown anyway — not a millisecond longer.
      const between = !ersetzt && showPhase("between", { shot: index, needed });
      review_ms = (cd.review_seconds || 1.5) * 1000;
      await sleep(review_ms);
      if (between) hidePhaseOverlay();
      review.classList.remove("on");
    }
    // Der Blitz braucht nach jeder Auslösung Zeit. Die Ansicht des eben
    // gemachten Bildes zählt dabei mit — nur was darüber hinaus fehlt, wird
    // gewartet, und auch das nur, wenn noch eine Aufnahme kommt.
    // The next shot's countdown counts as well: the flash recharges while the
    // digits run. Before, a GIF (no picture shown) waited the full recycle
    // time and then its one-second gap on top.
    const fl = S.profile.flash || {};
    if (fl.enabled && index < needed) {
      const naechster_ms = shotSeconds(flow.mode, index + 1, cd, S.profile.gif, !!(retake || ersetzt)) * 1000;
      const rest = (Number(fl.recycle_seconds) || 0) * 1000 - review_ms - naechster_ms;
      if (rest > 0) await sleep(rest);
    }
  }

  // Strip of grey boxes that fill up shot by shot — guests see how many are left
  function shotStrip(count, filled = 0, thumb = "") {
    const strip = $("#shotStrip");
    if (!count || count < 2) { strip.hidden = true; strip.innerHTML = ""; return; }
    if (strip.childElementCount !== count) {
      strip.innerHTML = Array.from({ length: count }, (_, i) => `<span class="shot-box" data-i="${i + 1}"><b>${i + 1}</b><img alt=""></span>`).join("");
    }
    strip.hidden = false;
    strip.querySelectorAll(".shot-box").forEach((el, i) => {
      el.classList.toggle("is-done", i + 1 <= filled);
      el.classList.toggle("is-active", i + 1 === filled + 1);
      if (i + 1 === filled && thumb) el.querySelector("img").src = thumb;
    });
  }

  async function runCapture(retakeFlow) {
    const token = gate.enter("run");
    if (token === null) return;            // a run is already being set up or running
    const meiner = lauf = { abgebrochen: false };
    try {
      if (!retakeFlow) {
        const sess = await api("/api/session", { method: "POST", body: { mode: flow.mode, format: flow.format, template: flow.template, background: flow.background, chroma_background: flow.chroma || "", consent: flow.consent || undefined, motion_frame: flow.motionFrame || undefined } });
        flow.uid = sess.uid; flow.needed = sess.shots_needed; flow.lastShot = null;
        // Der Gruß kommt vor den Countdown: Danach warten alle auf das Bild,
        // und niemand tippt dann noch gern.
        if (gastTextNoetig()) {
          const text = await fragGastText();
          if (text) await api(`/api/session/${flow.uid}/text`, { method: "POST", body: { text } });
        }
      }
      pruefeAbbruch(meiner);
      zeigeCaptureAbbruch(retakeFlow);
      show("capture");
      setTimeout(applyFrame, 400);
      if (VIDEO_MODES.includes(flow.mode)) {
        await countdown(S.profile.countdown.first);
        const rec = $("#rec");
        rec.querySelector("b").textContent = flow.mode === "boomerang" ? t("capture.move") : t("capture.recording");
        const ring = recordingIndicator(clipSeconds(flow.mode));
        try {
          // Answers when the camera stops; the film is made while "processing" shows.
          await api(`/api/session/${flow.uid}/clip`, { method: "POST" });
        } finally {
          ring.stop();
        }
        pruefeAbbruch(meiner);
      } else if (retakeFlow) {
        await captureShot(1, 1, true);
      } else {
        shotStrip(flow.needed);
        for (let i = 1; i <= flow.needed; i++) await captureShot(i, flow.needed, false);
        // Bei mehreren Aufnahmen hat am Ende meistens eine nicht gepasst. Statt
        // alles zu wiederholen, darf der Gast genau die eine noch einmal machen.
        if (flow.needed > 1 && S.profile.retake) await durchsehen();
      }
      pruefeAbbruch(meiner);
      $("#captureCancel").hidden = true;
      shotStrip(0);
      $("#shotcount").textContent = "";
      showPhase("processing");
      show("processing");
      showProcessing();
      const item = await api(`/api/session/${flow.uid}/finish`, { method: "POST", body: { look: "original" } });
      openResult(item, { fresh: true });
    } catch (e) {
      // Cancelled: home() has already run; whatever answer arrives late is
      // for a run that no longer exists.
      if (e instanceof Abgebrochen || meiner.abgebrochen) return;
      $("#rec").classList.remove("on");
      hidePhaseOverlay();
      $("#errorTitle").textContent = t("error.title");
      $("#errorText").textContent = humanError(e);
      showPhase("error");
      show("error");
    } finally {
      gate.leave("run", token);
    }
  }

  // ── Durchsehen ──────────────────────────────────────────
  // Zeigt die Bilder der Serie; ein Tippen macht genau dieses noch einmal.
  // Ohne Zutun geht es nach der eingestellten Zeit von allein weiter — sonst
  // steht die Box, sobald jemand wortlos weggeht.

  function zeichneReview() {
    const box = $("#reviewShots");
    box.innerHTML = Array.from({ length: flow.needed }, (_, i) => {
      const n = i + 1;
      return `<button class="review-shot" data-review="${n}">` +
             `<img src="/api/session/${esc(flow.uid)}/shot/${n}.jpg?t=${Date.now()}" alt="">` +
             `<span><b>${n}</b> ${esc(t("review.again"))}</span></button>`;
    }).join("");
  }

  function reviewTimerStart() {
    clearTimeout(reviewTimer);
    const secs = Math.max(3, Number(S.profile.review_seconds || 12));
    const bar = $("#reviewTimer");
    bar.style.transition = "none"; bar.style.transform = "scaleX(1)"; void bar.offsetWidth;
    bar.style.transition = `transform ${secs}s linear`; bar.style.transform = "scaleX(0)";
    reviewTimer = setTimeout(() => reviewFertig(), secs * 1000);
  }

  function reviewFertig() {
    clearTimeout(reviewTimer);
    $("#reviewShots").innerHTML = "";
    const go = reviewGo; reviewGo = null;
    if (go) go();
  }

  // Abbrechen heißt hier wirklich abbrechen: alle Aufnahmen der Serie sind
  // danach weg, gedruckt oder verschickt wurde noch nichts.
  function reviewAbbrechen() {
    clearTimeout(reviewTimer);
    confirm(async () => {
      const uid = flow.uid;
      reviewGo = null;
      try { await api(`/api/session/${uid}/abort`, { method: "POST" }); } catch (e) { /* weg ist weg */ }
      home();
    }, "review.cancel_confirm", "review.cancel_yes", "common.back", true);
  }

  function durchsehen() {
    zeichneReview();
    show("review");
    reviewTimerStart();
    return new Promise((fertig) => { reviewGo = fertig; });
  }

  // Ein einzelnes Bild noch einmal: zurück in die Aufnahme, danach wieder hierher.
  async function reviewNochmal(n) {
    // A reshoot belongs to the series under review; a second tap on another
    // picture while the first one is being taken again is dropped.
    if (app.dataset.screen !== "review") return;
    const token = gate.enter("reshoot");
    if (token === null) return;
    try { await reviewNochmalLauf(n); } finally { gate.leave("reshoot", token); }
  }
  async function reviewNochmalLauf(n) {
    clearTimeout(reviewTimer);
    const meiner = lauf = { abgebrochen: false };
    zeigeCaptureAbbruch(false);
    show("capture");
    setTimeout(applyFrame, 400);
    $("#shotcount").textContent = `${n} / ${flow.needed}`;
    try {
      await captureShot(n, flow.needed, false, n);
    } catch (e) {
      if (e instanceof Abgebrochen || meiner.abgebrochen) return;
      $("#errorTitle").textContent = t("error.title");
      $("#errorText").textContent = humanError(e);
      showPhase("error");
      show("error");
      reviewGo = null;
      return;
    }
    $("#shotcount").textContent = "";
    zeichneReview();
    show("review");
    reviewTimerStart();
  }

  // ── Ergebnis & Detail ───────────────────────────────────
  function openResult(item, { fresh = false, fromGallery = false } = {}) {
    view = { ...item, look: "original", copies: 1, fresh, fromGallery };
    const media = $("#resultMedia");
    const bust = `t=${Date.now()}`;
    if (item.is_video) {
      // A control like the picture: a tap, or Enter after the arrows, opens it large.
      media.innerHTML = `<video src="${esc(item.result_url)}?${bust}" poster="${esc(item.thumb_url)}?${bust}" autoplay loop muted playsinline data-action="full" role="button" tabindex="0"></video>`;
      const vid = media.querySelector("video");
      vid.addEventListener("loadedmetadata", () => { if (vid.videoWidth && vid.videoHeight) vid.style.setProperty("--film-ar", `${vid.videoWidth}/${vid.videoHeight}`); }, { once: true });
    } else {
      const src = (fresh && item.mode !== "gif") ? `/api/session/${encodeURIComponent(item.uid)}/preview.jpg?look=original&size=1600&${bust}` : `${item.result_url}?${bust}`;
      media.innerHTML = `<div class="result-skel" aria-hidden="true"></div><img src="${esc(src)}" alt="" class="is-loading" data-action="full" role="button" tabindex="0">`;
      const img = media.querySelector("img");
      const done = () => { img.classList.remove("is-loading"); media.querySelector(".result-skel")?.remove(); };
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    }
    // Zu einer Collage kann ein GIF gehören. Dann bewegt sich das Bild beim
    // Öffnen — dafür hat der Gast getippt — und ein Umschalter führt zurück
    // zur Fassung, die auch gedruckt wird.
    // Bei einer Collage heißt die Wahl Bild oder Filmchen, bei einem GIF-Lauf
    // GIF oder Boomerang. Dasselbe Bedienelement, zwei Beschriftungen.
    const boom = item.mode === "gif";
    const wahl = !!(item.anim_url && !item.is_video)
      && (!boom || ((S.profile.gif || {}).boomerang || {}).guest_choice !== false);
    $("#animToggle").hidden = !wahl;
    if (wahl) {
      $("#animToggle").querySelector('[data-anim="0"]').textContent =
        t(boom ? "gallery.gif" : "gallery.still");
      $("#animToggle").querySelector('[data-anim="1"]').textContent =
        t(boom ? "gallery.boomerang" : "gallery.animated");
    }
    if (item.anim_url && !item.is_video) {
      view.stillUrl = `${item.result_url}?${bust}`;
      view.animUrl = `${item.anim_url}?${bust}`;
      setAnimated(true);
    }
    const mayLook = !S.profile.guest_choice || S.profile.guest_choice.look !== false;
    const looksOn = mayLook && fresh && !item.is_video && S.looks.length > 1;
    $("#looks").hidden = !looksOn;
    if (looksOn) {
      // No inline onload: the kiosk's CSP allows no inline handlers (R2-U1).
      $("#looks").innerHTML = S.looks.map((l) => `<button class="look" data-look="${esc(l.id)}" aria-pressed="${l.id === "original"}"><img loading="lazy" src="/api/session/${esc(encodeURIComponent(item.uid))}/preview.jpg?look=${esc(encodeURIComponent(l.id))}&size=260" alt=""><span>${esc(lookLabel(l))}</span></button>`).join("");
      for (const img of $("#looks").querySelectorAll("img")) {
        img.addEventListener("load", () => img.parentNode.classList.add("is-ready"), { once: true });
      }
    }
    $("#share").hidden = !item.qr_url;
    if (item.qr_url) $("#qr").src = item.qr_url;
    // No internet: two steps instead of the gallery code — join the booth's
    // Wi-Fi, then load the photo from the booth itself.
    const local = item.local_share;
    $("#wifiSteps").hidden = !local;
    if (local) {
      $("#share").hidden = true;
      $("#wifiQr").src = local.wifi_qr;
      $("#photoQr").src = local.photo_qr;
      $("#wifiSsid").textContent = t("result.wifi_ssid", { ssid: local.ssid });
    }
    updatePrintBox(item.print);
    const privacyOn = !!(S.privacy && S.privacy.print_only) || !!item.print_only;
    $("#privacyResult").hidden = !privacyOn;
    $("#mailBtn").hidden = privacyOn || !(S.mail && S.mail.enabled);
    // Only a fresh photo or collage, and only when the booth offers it (the
    // key "draw" is missing from the item when drawing is switched off).
    $("#drawBtn").hidden = !drawOffered(item, fresh, fromGallery);
    $("#retakeBtn").hidden = !(fresh && S.profile.retake && (flow.mode === "photo"));
    $("#deleteBtn").hidden = !(fresh && S.profile.allow_delete);
    $("#doneBtn").querySelector("span").textContent = fromGallery ? t("common.back") : t("result.done");
    show("result");
    // nur bei frischen Aufnahmen: beim Blättern durch die Galerie wäre der Ton lästig
    if (fresh) playSound("done");
    if (fromGallery) { clearTimeout(resultTimer); $("#timerBar").style.transform = "scaleX(0)"; } else startResultTimer();
  }

  function setAnimated(on) {
    const img = $("#resultMedia").querySelector("img");
    if (!img || !view.animUrl) return;
    img.src = on ? view.animUrl : view.stillUrl;
    view.animated = on;
    document.querySelectorAll("#animToggle button").forEach(
      (b) => b.setAttribute("aria-pressed", String((b.dataset.anim === "1") === on)));
  }

  // Which button carries the accent. While nothing has been printed, the
  // print is what the guest came for; afterwards "Fertig" leads again.
  function printLead(on) {
    const p = $("#printBtn"), d = $("#doneBtn");
    p.classList.toggle("btn-primary", !!on); p.classList.toggle("btn-secondary", !on);
    d.classList.toggle("btn-primary", !on); d.classList.toggle("btn-secondary", !!on);
  }
  function updatePrintBox(pr) {
    const box = $("#printBox"), note = $("#printNote");
    const enabled = pr && pr.enabled;
    box.hidden = !enabled;
    note.textContent = "";
    printLead(enabled && !view.printedOnce && !(pr.max_now < 1) && !pr.error_code);
    if (!enabled) return;
    view.copies = Math.max(1, Math.min(view.copies || 1, pr.max_now || 1));
    $("#copies").textContent = view.copies;
    // can_queue: paper out, but the profile queues prints until it is refilled.
    $("#printBtn").disabled = pr.max_now < 1 || (["printer.offline", "printer.error"].includes(pr.error_code) && !pr.can_queue);
    if (pr.error_code) note.textContent = t(pr.error_code);
    else if (pr.max_now < 1) note.textContent = t("print.limit");
    else {
      const left = [pr.session_left, pr.event_left].filter((x) => x !== null && x !== undefined);
      if (left.length) {
        const n = Math.min(...left);
        note.textContent = t(n === 1 ? "print.left_one" : "print.left", { n });
      }
    }
    // Wartezeit dazusagen, wenn schon jemand vor einem dran ist. Ein
    // Sublimationsdrucker braucht knapp eine Minute je Bild — wer das weiß,
    // stellt sich nicht ratlos daneben.
    if (pr.wait_seconds > 60 && !pr.error_code) {
      const minutes = Math.ceil(pr.wait_seconds / 60);
      note.textContent = (note.textContent ? note.textContent + " · " : "") + t("print.wait", { n: minutes });
    }
    view.print = pr;
  }

  function startResultTimer() {
    clearTimeout(resultTimer);
    const secs = S.profile.result_seconds || 45;
    const bar = $("#timerBar");
    bar.style.transition = "none"; bar.style.transform = "scaleX(1)"; void bar.offsetWidth;
    bar.style.transition = `transform ${secs}s linear`; bar.style.transform = "scaleX(0)";
    resultTimer = setTimeout(afterPhase, secs * 1000);
  }

  async function chooseLook(look) {
    if (look === view.look) return;
    view.look = look;
    press($("#looks"), "look", look);
    const img = $("#resultMedia img");
    img.style.opacity = ".5";
    img.onload = () => (img.style.opacity = "1");
    img.src = `/api/session/${view.uid}/preview.jpg?look=${look}&size=1600&t=${Date.now()}`;
    try { const item = await api(`/api/session/${view.uid}/finish`, { method: "POST", body: { look } }); updatePrintBox(item.print); }
    catch (e) { toast(humanError(e)); }
  }

  // ── E-Mail mit Bildschirmtastatur ───────────────────────
  // Der Fotobox-Rechner hat nur einen Touchscreen — also eine eigene Tastatur,
  // die auf E-Mail-Adressen zugeschnitten ist (@ und .de sind eigene Tasten).
  const KB_ROWS = [
    "1234567890",
    "qwertzuiopü",
    "asdfghjklöä",
    "yxcvbnm-_",
  ];
  const KB_QUICK = ["@", ".", ".de", ".com", "gmail.com", "web.de", "gmx.de"];

  function buildKeyboard(id = "keyboard", schnell = KB_QUICK) {
    const kb = $("#" + id);
    if (kb.dataset.ready) return;
    const rows = KB_ROWS.map((row) => `<div class="kb-row">` +
      [...row].map((k) => `<button class="kb-key" data-key="${esc(k)}">${esc(k)}</button>`).join("") + `</div>`);
    rows.push(`<div class="kb-row kb-row--quick">` +
      schnell.map((k) => `<button class="kb-key kb-key--wide" data-key="${esc(k)}">${esc(k === " " ? "␣" : k)}</button>`).join("") +
      `<button class="kb-key kb-key--wide" data-key="__back">⌫</button></div>`);
    kb.innerHTML = rows.join("");
    kb.dataset.ready = "1";
  }

  function openMail() {
    if (!(S.mail && S.mail.enabled)) return toast(t("mail.disabled"));
    buildKeyboard();
    mailAddress = "";
    $("#mailField").textContent = "";
    $("#mailField").dataset.placeholder = t("mail.placeholder");
    $("#mailOverlay").hidden = false;
    clearTimeout(resultTimer);                 // niemand soll beim Tippen rausfliegen
  }

  function closeMail() {
    $("#mailOverlay").hidden = true;
    if (!view.fromGallery) startResultTimer();
  }

  // The keyboard has small letters only. A greeting is mostly names, so a
  // letter that starts a word is set as a capital — no shift key to explain.
  function grossAmWortanfang(text, key) {
    if (key.length !== 1 || key === key.toUpperCase()) return key;
    return (!text || /[\s\-&·♥!]$/.test(text)) ? key.toUpperCase() : key;
  }

  function typeKey(key) {
    // Zwei Fenster teilen sich dieselbe Tastatur. Welches Feld gemeint ist,
    // entscheidet das offene Fenster — nicht die Taste.
    if (!$("#textOverlay").hidden) {
      const grenze = Math.max(1, Math.min(120, Number((S.profile.guest_text || {}).max_len) || 40));
      if (key === "__back") gastText = gastText.slice(0, -1);
      else if (gastText.length < grenze) gastText += grossAmWortanfang(gastText, key);
      $("#textField").textContent = gastText;
      return;
    }
    if (key === "__back") mailAddress = mailAddress.slice(0, -1);
    else if (mailAddress.length < 120) mailAddress += key;
    $("#mailField").textContent = mailAddress;
  }

  // ── Gruß vom Gast ───────────────────────────────────────
  let gastText = "", gastWeiter = null;

  function gastTextNoetig() {
    const cfg = (S && S.profile && S.profile.guest_text) || {};
    const modi = cfg.modes || ["photo", "collage"];
    return !!cfg.enabled && modi.includes(flow.mode);
  }

  function fragGastText() {
    buildKeyboard("textKeyboard", [" ", "&", "!", "·", "♥"]);
    gastText = "";
    $("#textField").textContent = "";
    const cfg = (S.profile.guest_text || {});
    $("#textOverlay").querySelector('[data-action="text-skip"]').hidden = !!cfg.required;
    $("#textOverlay").hidden = false;
    return new Promise((fertig) => { gastWeiter = fertig; });
  }

  async function gastTextFertig(uebernehmen) {
    const cfg = (S.profile.guest_text || {});
    if (uebernehmen && cfg.required && !gastText.trim()) return toast(t("guesttext.needed"));
    $("#textOverlay").hidden = true;
    const weiter = gastWeiter; gastWeiter = null;
    if (weiter) weiter(uebernehmen ? gastText.trim() : "");
  }

  // ── Drawing on the picture ──────────────────────────────
  // After the shot, not before: the guest draws on the picture they see. What
  // leaves the screen is a list of strokes, never a bitmap — the booth paints
  // them onto the full-size picture (booth/boothcore/drawing.py), and print,
  // screen and gallery get that version.

  // <draw-helpers> — pure, no DOM; booth/tests/test_guest_drawing.py runs them with Node.
  const DRAW_WIDTHS = [0.006, 0.012, 0.024];      // share of the picture's width: fine, pen, marker
  // drawing.py refuses anything above these; the kiosk stays below on its own.
  const DRAW_LIMITS = { strokes: 120, points: 1500, total: 12000 };
  const DRAW_MIN_STEP = 0.002;                     // closer points add bytes, not shape
  function drawNorm(x, y, rect) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || !(rect.width > 0) || !(rect.height > 0)) return null;
    // A finger that slides off the picture keeps drawing along its edge.
    const c = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
    return [c((x - rect.left) / rect.width), c((y - rect.top) / rect.height)];
  }
  function drawAddPoint(points, p, minStep) {
    if (!p) return false;
    const last = points[points.length - 1];
    if (last && Math.hypot(p[0] - last[0], p[1] - last[1]) < minStep) return false;
    points.push(p);
    return true;
  }
  // Quadratic curves through the midpoints: each point is the control, the
  // middle to the next point the end. drawing.segments() does the same, so the
  // print shows the line the guest saw.
  function drawSmooth(points) {
    if (!points.length) return [];
    const out = [["M", points[0][0], points[0][1]]];
    for (let i = 1; i < points.length - 1; i++) {
      const [cx, cy] = points[i], [nx, ny] = points[i + 1];
      out.push(["Q", cx, cy, (cx + nx) / 2, (cy + ny) / 2]);
    }
    if (points.length > 1) out.push(["L", points[points.length - 1][0], points[points.length - 1][1]]);
    return out;
  }
  function drawPayload(strokes, limits) {
    const out = [];
    let total = 0;
    for (const s of strokes) {
      if (out.length >= limits.strokes) break;
      const room = Math.min(limits.points, limits.total - total);
      if (room <= 0) break;
      const points = s.points.slice(0, room);
      if (!points.length) continue;
      total += points.length;
      out.push({ color: s.color, width: s.width, points });
    }
    return out;
  }
  // </draw-helpers>

  let drawStrokes = [], drawCurrent = null, drawPointer = null, drawBusy = false, drawFrameReq = 0;
  let drawColor = "", drawWidth = 1;
  const drawCanvas = $("#drawCanvas");

  function drawOffered(item, fresh, fromGallery) {
    return !!(item && item.draw && fresh && !fromGallery && !item.is_video);
  }

  function openDraw() {
    if (!drawOffered(view, view.fresh, view.fromGallery) || drawBusy) return;
    const colors = view.draw.colors || [];
    if (!colors.includes(drawColor)) drawColor = colors[0] || "#ffffff";
    $("#drawColors").innerHTML = colors.map((c) =>
      `<button class="draw-color" data-draw-color="${esc(c)}" aria-pressed="${c === drawColor}" ` +
      `aria-label="${esc(t("draw.color"))} ${esc(c)}" style="--c:${hex(c, "#ffffff")}"><i></i></button>`).join("");
    pressDraw();
    // Drawing again starts from what was accepted last time, on the clean picture.
    drawStrokes = (view.drawStrokes || []).map((s) => ({ ...s, points: s.points.slice() }));
    const img = $("#drawImg");
    img.onload = layoutDraw;
    img.src = `/api/session/${encodeURIComponent(view.uid)}/preview.jpg?look=${encodeURIComponent(view.look || "original")}` +
      `&size=1600&draw=0&t=${Date.now()}`;
    $("#drawOverlay").hidden = false;
    layoutDraw();
    // The result timer keeps running: a guest who walks away mid-drawing is
    // sent home as from the result screen. Only a finger on the canvas holds it.
    if (!view.fromGallery) startResultTimer();
  }

  function pressDraw() {
    for (const el of document.querySelectorAll("#drawColors [data-draw-color]")) {
      el.setAttribute("aria-pressed", String(el.dataset.drawColor === drawColor));
    }
    for (const el of document.querySelectorAll("#drawWidths [data-draw-width]")) {
      el.setAttribute("aria-pressed", String(Number(el.dataset.drawWidth) === drawWidth));
    }
  }

  function layoutDraw() {
    const img = $("#drawImg"), frame = $("#drawFrame");
    if ($("#drawOverlay").hidden || !img.naturalWidth) return;
    const box = $("#drawStage").getBoundingClientRect();
    const k = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
    const w = Math.max(1, Math.floor(img.naturalWidth * k)), h = Math.max(1, Math.floor(img.naturalHeight * k));
    frame.style.width = w + "px";
    frame.style.height = h + "px";
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    drawCanvas.width = Math.round(w * dpr);
    drawCanvas.height = Math.round(h * dpr);
    paintDraw();
  }
  window.addEventListener("resize", () => { if (!$("#drawOverlay").hidden) layoutDraw(); });

  function paintDraw() {
    drawFrameReq = 0;
    const ctx = drawCanvas.getContext("2d"), W = drawCanvas.width, H = drawCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const s of drawStrokes) {
      const segs = drawSmooth(s.points);
      if (!segs.length) continue;
      const lw = Math.max(1, s.width * W);
      ctx.strokeStyle = ctx.fillStyle = s.color;
      if (segs.length === 1) {               // a tap is a dot
        ctx.beginPath();
        ctx.arc(segs[0][1] * W, segs[0][2] * H, lw / 2, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }
      ctx.lineWidth = lw;
      ctx.beginPath();
      for (const g of segs) {
        if (g[0] === "M") ctx.moveTo(g[1] * W, g[2] * H);
        else if (g[0] === "Q") ctx.quadraticCurveTo(g[1] * W, g[2] * H, g[3] * W, g[4] * H);
        else ctx.lineTo(g[1] * W, g[2] * H);
      }
      ctx.stroke();
    }
    $("#drawUndo").disabled = !drawStrokes.length;
    $("#drawClear").disabled = !drawStrokes.length;
  }
  const schedulePaint = () => { if (!drawFrameReq) drawFrameReq = requestAnimationFrame(paintDraw); };

  // One finger draws. A second finger (a palm, a neighbour) is ignored until
  // the first lifts — otherwise the line would jump between them.
  drawCanvas.addEventListener("pointerdown", (e) => {
    if (drawPointer !== null || drawBusy || $("#drawOverlay").hidden) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const total = drawStrokes.reduce((n, s) => n + s.points.length, 0);
    if (drawStrokes.length >= DRAW_LIMITS.strokes || total >= DRAW_LIMITS.total) return;
    const p = drawNorm(e.clientX, e.clientY, drawCanvas.getBoundingClientRect());
    if (!p) return;
    e.preventDefault();
    drawPointer = e.pointerId;
    try { drawCanvas.setPointerCapture(e.pointerId); } catch (err) { /* the stroke works without */ }
    clearTimeout(resultTimer);               // nobody is sent home mid-stroke
    drawCurrent = { color: drawColor, width: DRAW_WIDTHS[drawWidth] || DRAW_WIDTHS[1], points: [p] };
    drawStrokes.push(drawCurrent);
    schedulePaint();
  });
  drawCanvas.addEventListener("pointermove", (e) => {
    if (e.pointerId !== drawPointer || !drawCurrent) return;
    e.preventDefault();
    const rect = drawCanvas.getBoundingClientRect();
    const events = (e.getCoalescedEvents && e.getCoalescedEvents()) || [];
    for (const ev of (events.length ? events : [e])) {
      if (drawCurrent.points.length >= DRAW_LIMITS.points) break;
      drawAddPoint(drawCurrent.points, drawNorm(ev.clientX, ev.clientY, rect), DRAW_MIN_STEP);
    }
    schedulePaint();
  });
  function drawLift(e) {
    if (e.pointerId !== drawPointer) return;
    drawPointer = null;
    drawCurrent = null;
    // The finger is off: from here the result timer counts again.
    if (!$("#drawOverlay").hidden && app.dataset.screen === "result" && !view.fromGallery) startResultTimer();
  }
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) drawCanvas.addEventListener(name, drawLift);

  function drawUndo() {
    if (drawPointer !== null) return;
    drawStrokes.pop();
    schedulePaint();
  }
  function drawClearAll() {
    if (drawPointer !== null) return;
    drawStrokes = [];
    schedulePaint();
  }

  // forget: the run is over (home). Nothing of this guest's drawing stays in
  // the page for the next one.
  function closeDraw(forget = false) {
    $("#drawOverlay").hidden = true;
    drawPointer = null;
    drawCurrent = null;
    drawStrokes = [];
    if (drawFrameReq) cancelAnimationFrame(drawFrameReq);
    drawFrameReq = 0;
    drawCanvas.getContext("2d").clearRect(0, 0, drawCanvas.width, drawCanvas.height);
    $("#drawImg").removeAttribute("src");
    if (!forget && app.dataset.screen === "result" && !view.fromGallery) startResultTimer();
  }

  // The look thumbnails are rendered by the server with the drawing on; after a
  // drawing they still show the old picture. A new stamp makes them reload.
  function refreshLookThumbs() {
    const stamp = Date.now();
    document.querySelectorAll("#looks .look img").forEach((img) => {
      img.src = img.getAttribute("src").replace(/&t=\d+$/, "") + `&t=${stamp}`;
    });
  }

  async function drawDone() {
    if (drawBusy || drawPointer !== null) return;
    const strokes = drawPayload(drawStrokes, DRAW_LIMITS);
    if (!strokes.length && !(view.drawStrokes || []).length) return closeDraw();
    const uid = view.uid;
    drawBusy = true;
    $("#drawDone").disabled = true;
    try {
      const item = await api(`/api/session/${uid}/draw`, { method: "POST", body: { strokes } });
      if (view.uid !== uid) return;            // the run ended while the booth was painting
      view.drawStrokes = strokes;
      const src = `${item.result_url}?t=${Date.now()}`;
      if (view.animUrl) { view.stillUrl = src; setAnimated(false); }    // show the drawn still
      else { const img = $("#resultMedia img"); if (img) img.src = src; }
      updatePrintBox(item.print);
      refreshLookThumbs();
      closeDraw();
    } catch (e) {
      toast(t("draw.failed"));
    } finally {
      drawBusy = false;
      $("#drawDone").disabled = false;
    }
  }

  async function sendMail() {
    const address = mailAddress.trim();
    if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(address)) return toast(t("mail.invalid"));
    try {
      await api(`/api/session/${view.uid}/mail`, { method: "POST", body: { address } });
      closeMail();
      toast(S.sync && S.sync.online === false ? t("mail.queued") : t("mail.sent"));
    } catch (e) { toast(humanError(e)); }
  }

  async function doPrint() {
    const btn = $("#printBtn");
    // The button holds itself while a print is asked for and 5 s after; the
    // print key and the number keys call here directly and must obey the
    // same hold, or "3" pressed twice spends six sheets.
    if (printHold.uid === view.uid && Date.now() < printHold.until) return;
    const token = gate.enter("print");
    if (token === null) return;
    btn.disabled = true;
    try {
      const r = await api(`/api/print/${view.uid}`, {
        method: "POST", body: { copies: view.copies, from_gallery: !!view.fromGallery } });
      playSound("print");
      if (r.deferred) {
        // Queued until paper is refilled: say so and do not watch for a print
        // that cannot come yet ("stuck" would read as a failure).
        updatePrintBox(r);
        view.printedOnce = true;
        printLead(false);
        toast(t("print.deferred"));
        $("#printNote").textContent = t("print.deferred");
        printHold = { uid: view.uid, until: Date.now() + 5000 };
        return;
      }
      toast(r.pending > r.queued ? t("print.queued_n", { n: r.pending - r.queued }) : t("print.queued"));
      updatePrintBox(r);
      // A print takes most of a minute and the toast is gone after three
      // seconds: say so in a line that stays, and hold the button briefly so
      // a second tap does not spend a second sheet by accident.
      const uid = view.uid, again = !btn.disabled;
      view.printedOnce = true;
      printLead(false);
      $("#printNote").textContent = t("print.running");
      btn.disabled = true;
      printHold = { uid, until: Date.now() + 5000 };
      setTimeout(() => { if (view.uid === uid && again) btn.disabled = false; }, 5000);
      watchPrint(view.uid);
    } catch (e) {
      btn.disabled = false;
      if (e.status === 503 && /^printer\./.test(e.message)) return printStuck(!!view.print_only);
      toast(humanError(e));
    } finally {
      gate.leave("print", token);
    }
  }
  let printHold = { uid: null, until: 0 };

  // Where the print stands. Asked every 1.5 s while this run is on screen, for
  // at most three minutes. A takeover is told once, calmly; "no printer can"
  // tells a gallery run that the photo is saved, and asks the guest of a
  // "Nur drucken" run what to do — the photo is nowhere else.
  let printWatch = null;
  function watchPrint(uid) {
    clearInterval(printWatch);
    const until = Date.now() + 180000, told = {};
    printWatch = setInterval(async () => {
      if (view.uid !== uid || app.dataset.screen !== "result" || Date.now() > until) return clearInterval(printWatch);
      let st;
      try { st = await api(`/api/print/${uid}/status`); } catch (e) { return; }
      if (st.moved_to && !told.moved) {
        told.moved = true;
        toast(st.moved_to === 2 ? t("print.moved") : t("print.moved_n", { n: st.moved_to }));
      }
      if (st.state === "printed") { $("#printNote").textContent = t("print.done"); return clearInterval(printWatch); }
      if (st.state === "stuck" && !told.stuck) {
        told.stuck = true;
        clearInterval(printWatch);
        printStuck(st.print_only);
      }
    }, 1500);
  }
  // No printer can print: refused right away (all printers known to be down)
  // or failed on every printer. Same words either way.
  function printStuck(printOnly) {
    if (!printOnly) { $("#printNote").textContent = t("print.failed_saved"); return toast(t("print.failed_saved")); }
    clearTimeout(resultTimer);           // the guest decides; nothing runs off meanwhile
    $("#printFailOverlay").hidden = false;
  }
  async function printFailChoice(choice) {
    $("#printFailOverlay").hidden = true;
    try {
      const r = await api(`/api/session/${view.uid}/keep`, { method: "POST", body: { choice } });
      if (choice === "discard") return home();
      view.print_only = false;
      $("#privacyResult").hidden = true;
      $("#share").hidden = !r.qr_url;
      if (r.qr_url) $("#qr").src = r.qr_url;
      $("#printNote").textContent = t("print.kept");
      toast(t("print.kept"));
      startResultTimer();
    } catch (e) { toast(humanError(e)); home(); }
  }

  // Die Rückfrage dient inzwischen zwei Zwecken: eine fertige Aufnahme löschen
  // und eine begonnene Serie wegwerfen. Frage und Ja-Knopf kommen deshalb mit.
  function confirm(cb, frage = "result.delete_confirm", ja = "result.delete",
                   nein = "common.cancel", gefahr = false) {
    confirmCb = cb;
    const ov = $("#confirmOverlay");
    ov.querySelector("h2").textContent = t(frage);
    const jaBtn = ov.querySelector('[data-action="confirm-yes"]');
    jaBtn.querySelector("span").textContent = t(ja);
    // Die Akzentfarbe gehört dem, was der Gast eigentlich will. Wegwerfen ist
    // das nie — dieser Knopf trägt deshalb die Farbe des Rückzugs.
    jaBtn.classList.toggle("danger", !!gefahr);
    ov.querySelector('[data-action="confirm-no"] span').textContent = t(nein);
    ov.hidden = false;
  }

  async function doDelete() {
    try { await api(`/api/session/${view.uid}`, { method: "DELETE" }); } catch (e) {}
    home();
  }

  // Groß ansehen — an der Box das Einzige, was mit einem fremden Bild geht.
  function openFull() {
    if (!view.uid) return;
    const bust = `t=${Date.now()}`;
    const media = $("#fullMedia");
    if (view.is_video) {
      media.innerHTML = `<video src="${esc(view.result_url)}?${bust}" autoplay loop muted playsinline controls></video>`;
    } else {
      const src = view.animated && view.animUrl ? view.animUrl : `${view.result_url}?${bust}`;
      media.innerHTML = `<img src="${esc(src)}" alt="" data-action="full-close">`;
    }
    $("#fullOverlay").hidden = false;
    clearTimeout(resultTimer);      // wer schaut, soll nicht hinausgeworfen werden
  }

  function closeFull() {
    $("#fullOverlay").hidden = true;
    $("#fullMedia").innerHTML = "";
    if (!view.fromGallery) startResultTimer();
  }

  function openQr(which) {
    // With the two Wi-Fi steps each code opens large on its own: the network
    // with its name (and password, for phones whose camera cannot join), or
    // the link to the booth.
    const local = view.local_share;
    let data = view.share_url, caption = (view.share_url || "").replace(/^https?:\/\//, "");
    if (local && which === "wifi") {
      data = local.wifi;
      caption = t("result.wifi_ssid", { ssid: local.ssid }) +
        (local.open ? "" : " · " + t("result.wifi_password", { password: local.password }));
    } else if (local && which === "photo") {
      data = local.url;
      caption = local.url.replace(/^https?:\/\//, "");
    }
    if (!data) return;
    const step = local && which === "wifi" ? "result.wifi_join" : local && which === "photo" ? "result.wifi_photo" : "result.scan";
    $("#qrTitle").textContent = t(step);
    $("#qrBig").src = `/api/qr.png?data=${encodeURIComponent(data)}&size=900`;
    $("#qrUrl").textContent = caption;
    $("#qrOverlay").hidden = false;
    // Evening's counters: how often guests needed the big code. A number, nothing else.
    api("/api/count/qr_big", { method: "POST" }).catch(() => {});
  }

  // An operator message in the interface's language: the booth sends a text
  // key with its values; the German sentence is the fallback for a message
  // without a key (from a plug-in, or an older booth version).
  function meldungText(m) {
    if (!m.code) return m;
    const werte = m.werte || {};
    const titel = t(`${m.code}.title`, werte), text = t(`${m.code}.text`, werte);
    return { ...m, titel: titel === `${m.code}.title` ? m.titel : titel,
             text: (text === `${m.code}.text` ? m.text : text).trim() };
  }

  // ── Galerie an der Box ──────────────────────────────────
  async function openGallery() {
    show("gallery");
    const grid = $("#galleryGrid");
    grid.innerHTML = `<div class="gallery-skel"></div>`.repeat(8);
    try {
      const data = await api("/api/gallery?limit=120");
      $("#galleryCount").textContent = data.total;
      grid.innerHTML = data.items.length ? data.items.map((it, i) => `<button class="tile" style="--i:${Math.min(i, 12)}" data-item="${esc(it.uid)}"><img loading="lazy" src="${esc(it.thumb_url)}" alt="">${(it.is_animated ?? it.is_video) ? `<span class="tile-play">${ICONS.play}</span>` : ""}</button>`).join("")
        : `<p class="gallery-empty">${esc(t("gallery.empty"))}</p>`;
      view.galleryItems = Object.fromEntries(data.items.map((it) => [it.uid, it]));
    } catch (e) { grid.innerHTML = `<p class="gallery-empty">${esc(humanError(e))}</p>`; }
  }

  // ── Betreiber an der Box: Menü, PIN, Sperre ─────────────
  // Der Hotspot (Ecke aus layout.corner) führte früher direkt in die Verwaltung. Jetzt kommt
  // ein Menü: sperren oder Verwaltung. Beides erst nach PIN — sonst könnte
  // jeder Gast, der die Ecke findet, die Box abschalten.
  let hsTimer = null, pinBuf = "", pinThen = null;
  const hs = $("#adminHotspot");
  hs.setAttribute("aria-label", t("common.admin"));
  const hotspotSeconds = () => Number(((S && S.profile.access) || {}).hotspot_seconds) || 3;
  hs.addEventListener("pointerdown", () => {
    hsTimer = setTimeout(openBoothMenu, hotspotSeconds() * 1000);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => hs.addEventListener(ev, () => clearTimeout(hsTimer)));
  // access.lock_icon: the visible lock opens at once — nobody guesses a long press with a mouse.
  hs.addEventListener("click", () => {
    if (((S && S.profile.access) || {}).lock_icon) { clearTimeout(hsTimer); openBoothMenu(); }
  });

  function openBoothMenu() {
    stopAttract(false);
    const locked = !!(S && S.locked);
    $("#menuLockLabel").textContent = t(locked ? "menu.unlock" : "menu.lock");
    $("#boothMenu").hidden = false;
  }

  function askPin(title, done, hint) {
    pinBuf = ""; pinThen = done;
    $("#pinTitle").textContent = title;
    $("#pinOut").textContent = "";
    $("#pinHint").textContent = hint || t("pin.hint");
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "OK"];
    $("#pinpad").innerHTML = keys.map((k) =>
      `<button class="pinkey${k === "OK" ? " is-go" : ""}" data-pin="${k}">${k}</button>`).join("");
    $("#pinOverlay").hidden = false;
  }

  // The PIN from a keyboard: digits, Enter, Backspace. Captured before the
  // start keys, so Enter confirms the PIN instead of starting a run.
  document.addEventListener("keydown", (e) => {
    if ($("#pinOverlay").hidden) return;
    if (/^[0-9]$/.test(e.key)) { e.preventDefault(); return pinKey(e.key); }
    if (e.key === "Enter") { e.preventDefault(); return pinKey("OK"); }
    if (e.key === "Backspace") { e.preventDefault(); return pinKey("C"); }
  }, true);

  function pinKey(k) {
    if (k === "C") pinBuf = "";
    else if (k === "OK") return pinThen && pinThen(pinBuf);
    else if (pinBuf.length < 8) pinBuf += k;
    $("#pinOut").textContent = "•".repeat(pinBuf.length);
  }

  // The PIN brake answers 429 with Retry-After; say how long, not just "wrong".
  function pinError(e) {
    if (e.status === 429) {
      const s = e.retryAfter || 60;
      return t("pin.wait", { n: s < 120 ? `${s} s` : `${Math.ceil(s / 60)} min` });
    }
    return e.status === 401 ? t("pin.wrong") : (e.message || t("error.title"));
  }

  // "PIN ändern" from the operator menu: the current PIN, the new one twice,
  // all on the same pad. The booth checks the current PIN behind the brake and
  // the new one against its rules (/api/pin/change); the pad only explains them.
  function changePin() {
    askPin(t("pin.current"), (current) => {
      if (!current) return;
      const askNew = (hint) => askPin(t("pin.new"), (first) => {
        if (first.length < 4) { pinBuf = ""; $("#pinOut").textContent = ""; $("#pinHint").textContent = t("pin.new_hint"); return; }
        askPin(t("pin.repeat"), async (second) => {
          if (second !== first) return askNew(t("pin.mismatch"));
          try {
            await api("/api/pin/change", { method: "POST", body: { current_pin: current, new_pin: first } });
            $("#pinOverlay").hidden = true;
            toast(t("pin.changed"));
          } catch (e) {
            // A weak new PIN (400) asks for another one and says why. A wrong
            // current PIN (401) or the brake (429) ends the dialog with the
            // reason: nothing was changed.
            if (e.status === 400) return askNew(e.message || t("pin.new_hint"));
            pinThen = null;
            $("#pinOverlay").hidden = true;
            toast(pinError(e));
          }
        }, t("pin.new_hint"));
      }, hint || t("pin.new_hint"));
      askNew();
    });
  }

  async function setLocked(locked, pin) {
    try {
      await api("/api/lock", { method: "POST", body: { pin, locked } });
      $("#pinOverlay").hidden = true;
      await refresh();
      toast(t(locked ? "menu.lock" : "menu.unlock"));
    } catch (e) {
      pinBuf = "";
      $("#pinOut").textContent = "";
      $("#pinHint").textContent = pinError(e);
    }
  }

  // ── Ereignisse ──────────────────────────────────────────
  app.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const el = e.target.closest('[data-action]:not(button)');
    if (!el) return;
    e.preventDefault();
    el.click();
  });
  app.addEventListener("click", (e) => {
    if (attractJustDismissed()) return;
    // Nicht nur Schaltflächen: Das Ergebnisbild selbst ist ein Bedienelement
    // („groß ansehen"), und dafür wäre ein Knopf um das Bild herum im Weg.
    const b = e.target.closest("button,[data-action]");
    if (!b) return;
    if (b.dataset.pin) return pinKey(b.dataset.pin);
    if (b.dataset.mode) return startTapTooSoon() ? undefined : openSetup(b.dataset.mode);
    if (b.dataset.consent) return openSetup(flow.pendingMode, b.dataset.consent);
    if (b.dataset.format) { flow.format = b.dataset.format; press($("#formatChips"), "format", flow.format); if (flow.mode === "boomerang" && !$("#groupTemplate").hidden) renderTemplateCards(); framesFollowFormat(); return applyFrame(); }
    if (b.dataset.frame) { flow.motionFrame = b.dataset.frame; press($("#frameChips"), "frame", flow.motionFrame); return applyFrame(); }
    if (b.dataset.template) { flow.template = b.dataset.template; press($("#templateChips"), "template", flow.template); return applyFrame(); }
    if (b.dataset.scroll) return scrollPicker(b);
    if (b.dataset.bg !== undefined) { flow.background = b.dataset.bg || null; return press($("#bgChips"), "bg", b.dataset.bg); }
    if (b.dataset.scene !== undefined) { flow.chroma = b.dataset.scene; return press($("#chromaChips"), "scene", b.dataset.scene); }
    if (b.dataset.key !== undefined) return typeKey(b.dataset.key);
    if (b.dataset.look) return chooseLook(b.dataset.look);
    if (b.dataset.drawColor) { drawColor = b.dataset.drawColor; return pressDraw(); }
    if (b.dataset.drawWidth !== undefined) { drawWidth = Number(b.dataset.drawWidth) || 0; return pressDraw(); }
    if (b.dataset.review) return reviewNochmal(Number(b.dataset.review));
    if (b.dataset.item) { const it = view.galleryItems[b.dataset.item]; const items = view.galleryItems; openResult(it, { fromGallery: true }); view.galleryItems = items; return; }
    switch (b.dataset.action) {
      case "home": return home();
      case "go": {
        // Zwei Tipper in derselben Sekunde legten zwei Durchgänge an; der erste
        // blieb mit Ordner und Zähler verwaist zurück. triggerPrimary hat diese
        // Sperre seit jeher, der Touch-Weg nicht.
        const jetzt = Date.now();
        if (jetzt - lastTrigger < 800) return;
        lastTrigger = jetzt;
        return runCapture();
      }
      case "done": return view.fromGallery ? openGallery() : afterPhase();
      case "print": return doPrint();
      case "retake": return runCapture(true);
      case "steps":
        stepsOffen = !stepsOffen;
        renderStartExtras(S.profile.start);
        return;
      case "review-ok": return reviewFertig();
      case "review-cancel": return reviewAbbrechen();
      case "capture-cancel": return captureAbbrechen();
      case "delete": return confirm(doDelete, "result.delete_confirm", "result.delete", "common.cancel", true);
      case "confirm-yes": $("#confirmOverlay").hidden = true; return confirmCb && confirmCb();
      // Nach einem „Nein" darf die Serie nicht stehen bleiben — die Zeitschaltung
      // des Durchsehens läuft weiter, als wäre nichts gewesen.
      case "confirm-no":
        $("#confirmOverlay").hidden = true;
        if (app.dataset.screen === "review") reviewTimerStart();
        return;
      case "qr-full": return openQr(b.dataset.qr);
      case "pf-gallery": return printFailChoice("gallery");
      case "pf-discard": return printFailChoice("discard");
      case "full": return openFull();
      case "full-close": return closeFull();
      case "anim-on": return setAnimated(true);
      case "anim-off": return setAnimated(false);
      case "text-ok": return gastTextFertig(true);
      case "text-skip": return gastTextFertig(false);
      case "mail": return openMail();
      case "draw": return openDraw();
      case "draw-undo": return drawUndo();
      case "draw-clear": return drawClearAll();
      case "draw-cancel": return closeDraw();
      case "draw-done": return drawDone();
      case "mail-close": return closeMail();
      case "mail-send": return sendMail();
      case "qr-close": $("#qrOverlay").hidden = true; return;
      case "pin-close": $("#pinOverlay").hidden = true; pinThen = null; return;
      case "menu-close": $("#boothMenu").hidden = true; return;
      case "menu-admin":
        $("#boothMenu").hidden = true;
        return askPin(t("pin.title"), async (pin) => {
          // Die Verwaltung prüft die PIN selbst; hier nur vorab, damit der
          // Betreiber den Ziffernblock nicht zweimal sieht.
          try {
            const signIn = await api("/api/admin/login", { method: "POST", body: { pin } });
            // The sign-in travels, never the PIN.
            sessionStorage.setItem("booth-admin", JSON.stringify({ token: signIn.token, must_change_pin: !!signIn.must_change_pin }));
            location.href = "/admin";
          } catch (e) {
            pinBuf = ""; $("#pinOut").textContent = ""; $("#pinHint").textContent = pinError(e);
          }
        });
      case "menu-pin":
        $("#boothMenu").hidden = true;
        return changePin();
      case "menu-lock": {
        const want = !(S && S.locked);
        $("#boothMenu").hidden = true;
        return askPin(t(want ? "menu.lock" : "menu.unlock"), (pin) => setLocked(want, pin));
      }
      case "gallery": return openGallery();
      case "lang": { const langs = S.profile.languages; lang = langs[(langs.indexOf(lang) + 1) % langs.length]; applyTexts(); renderStatus(); return renderStart(); }
      case "copies-plus": view.copies = Math.min(view.copies + 1, (view.print && view.print.max_now) || 1); $("#copies").textContent = view.copies; return;
      case "copies-minus": view.copies = Math.max(1, view.copies - 1); $("#copies").textContent = view.copies; return;
    }
  });
  $("#unlockBtn").addEventListener("click", () => askPin(t("menu.unlock"), (pin) => setLocked(false, pin)));
  $("#qrOverlay").addEventListener("click", (e) => { if (!e.target.closest("button")) $("#qrOverlay").hidden = true; });
  $("#s-start").addEventListener("click", (e) => {
    if (attractJustDismissed() || startTapTooSoon() || e.target.closest("button")) return;
    if (S && S.profile.modes.length) openSetup(S.profile.modes[0]);
  });

  // ── Auslöser: Buzzer, Tastatur, Fußschalter ─────────────
  //
  // Ein Buzzer ist fast immer eine ausgeschlachtete Maus (linke Taste) oder
  // eine Tastatur mit genau einer Taste (F13, Leertaste, Bild-ab). Beides
  // landet hier als ganz normales Browser-Ereignis — kein Treiber nötig.
  //
  // Der Auslöser drückt immer die Hauptschaltfläche des Bildschirms, auf dem
  // der Gast gerade steht. Das ist die einzige Regel, die ein Gast am Buzzer
  // versteht, und sie stimmt auf jedem Bildschirm.
  const CONTROLS = () => (S && S.profile && S.profile.controls) || {};
  function applyLayout() {
    // Prozent statt Pixel: Die Box läuft auf 1080p, auf 4K und auf dem alten
    // 1280×800-Panel im Gehäuse. Ein Rand von 3 % sitzt überall gleich.
    const L = (S && S.profile && S.profile.layout) || {};
    const root = document.documentElement.style;
    const pct = (v, max = 20) => Math.max(0, Math.min(max, Number(v) || 0)) + "%";
    root.setProperty("--safe-top", pct(L.safe_top));
    root.setProperty("--safe-right", pct(L.safe_right));
    root.setProperty("--safe-bottom", pct(L.safe_bottom));
    root.setProperty("--safe-left", pct(L.safe_left));
    const scale = Math.max(70, Math.min(140, Number(L.scale) || 100)) / 100;
    root.setProperty("--ui-scale", String(scale));
    app.dataset.corner = ["top-left", "top-right", "bottom-left", "bottom-right"]
      .includes(L.corner) ? L.corner : "top-right";
    app.dataset.gallerySize = ["l", "xl"].includes(L.gallery_size) ? L.gallery_size : "";
    app.dataset.backSize = ["l", "xl"].includes(L.back_size) ? L.back_size : "";
    const access = S.profile.access || {};
    const hs = $("#adminHotspot");
    hs.dataset.icon = access.lock_icon ? "1" : "";
    let guide = $("#safeGuide");
    if (L.guides) {
      if (!guide) {
        guide = document.createElement("div");
        guide.id = "safeGuide";
        document.body.appendChild(guide);
      }
      guide.dataset.label = "Sichtbarer Bereich — einstellbar in der Verwaltung unter Bildschirm";
    } else if (guide) {
      guide.remove();
    }
  }
  // A hidden pointer makes the booth unusable with a mouse. It stays hidden
  // for touch and for a buzzer wired to a mouse button, and shows up for a
  // few seconds whenever a real mouse moves.
  let mouseAwake = false, mouseTimer = null;
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse" || (!e.movementX && !e.movementY)) return;
    clearTimeout(mouseTimer);
    mouseTimer = setTimeout(() => { mouseAwake = false; applyControls(); }, 4000);
    if (!mouseAwake) { mouseAwake = true; applyControls(); }
  }, { passive: true });
  function applyControls() {
    const c = CONTROLS();
    document.body.classList.toggle("no-cursor", c.hide_cursor !== false && !mouseAwake);
    document.body.classList.toggle("no-touch", c.touch === false);
  }
  let lastTrigger = 0;

  function erlaubterModus(id) {
    // Ein Modus, den der Betreiber abgeschaltet hat, wird auch von einem
    // Knopf nicht wieder eingeschaltet.
    const modes = (S && S.profile && S.profile.modes) || [];
    return modes.includes(id) ? id : (modes[0] || "");
  }

  function startModus() {
    const gewuenscht = String(CONTROLS().start_mode || "");
    return gewuenscht ? erlaubterModus(gewuenscht) : ((S && S.profile.modes[0]) || "");
  }

  // ── Bedienung ohne Touch ────────────────────────────────
  //
  // Eine Box im Gehäuse hat manchmal keinen Touchscreen, sondern zwei oder
  // drei Arcade-Knöpfe. „trigger" allein reicht dafür nicht: Auf dem
  // Vorbereitungsbildschirm gibt es etwas zu wählen. Deshalb zwei Aufgaben
  // mehr — weiterschalten und auswählen — und eine Marke, die man aus zwei
  // Metern sieht.
  let fokusIndex = -1;

  function fokusFelder() {
    const schirm = document.querySelector(`#s-${app.dataset.screen}`);
    if (!schirm) return [];
    return [...schirm.querySelectorAll("button,[data-action]")].filter((el) => {
      if (el.hidden || el.disabled) return false;
      if (el.closest("[hidden]")) return false;
      const r = el.getBoundingClientRect();
      return r.width > 8 && r.height > 8;      // nur, was wirklich dasteht
    });
  }

  function fokusZeigen() {
    document.querySelectorAll(".is-fokus").forEach((el) => el.classList.remove("is-fokus"));
    const felder = fokusFelder();
    if (!felder.length || fokusIndex < 0) return;
    const el = felder[Math.min(fokusIndex, felder.length - 1)];
    el.classList.add("is-fokus");
    try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e) { /* egal */ }
  }

  function fokusWeiter(schritt = 1) {
    const felder = fokusFelder();
    if (!felder.length) return;
    fokusIndex = fokusIndex < 0 ? 0 : (fokusIndex + schritt + felder.length) % felder.length;
    fokusZeigen();
  }

  function fokusWaehlen() {
    const felder = fokusFelder();
    if (!felder.length) return false;
    if (fokusIndex < 0) { fokusWeiter(0); return true; }
    felder[Math.min(fokusIndex, felder.length - 1)].click();
    return true;
  }

  function fokusZuruecksetzen() {
    fokusIndex = -1;
    navFocus = null; navByArrows = false;
    document.querySelectorAll(".is-fokus").forEach((el) => el.classList.remove("is-fokus"));
  }

  // ── Arrow keys (controls.arrow_navigation, on by default) ──
  //
  // The owner: "das ganze Menü muss auch per Pfeiltasten bedienbar sein".
  // The arrows move a visible focus (the same ring as the button control
  // above) to the nearest control that way (nearestInDirection). A row of
  // choices counts as one stop for up/down (its chosen card); inside it
  // left/right choose at once, so the live preview follows. Enter/Space press
  // only what the arrows reached in the last NAV_FORGET_MS; anything else —
  // a buzzer that sends Enter, a focus the screen set — stays the trigger.
  // The screen's main action is pressed through the trigger too, with its
  // bounce guard. Escape and Backspace go back. On, the arrows are never
  // start keys, whatever keys_start says; off, nothing here runs.
  const arrowNavOn = () => CONTROLS().arrow_navigation !== false;
  const ARROWS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  const CHOICE_ROWS = "#formatChips, #templateChips, #bgChips, #chromaChips, #looks, #animToggle";
  const NAV_FORGET_MS = 15000;

  function openOverlayEl() {
    return [...document.querySelectorAll(".overlay")].find((el) => !el.hidden) || null;
  }
  function navVisible(el) {
    if (!el || el.hidden || el.disabled || el.closest("[hidden]")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  }
  function navScope() {
    return openOverlayEl() || document.querySelector(`#s-${app.dataset.screen}`);
  }
  // What the arrows can reach: the controls of the open window, else of the
  // screen; a row of choices only by its chosen item (or its first).
  function navTargets(skipRow) {
    const scope = navScope();
    if (!scope) return [];
    const all = [...scope.querySelectorAll("button, [data-action], input, [role=button][tabindex]")]
      .filter((el) => navVisible(el) && !el.matches(".picker-arrow") && !el.closest("#adminHotspot"));
    const out = [], rows = new Set();
    for (const el of all) {
      const row = el.closest(CHOICE_ROWS);
      if (!row) { out.push(el); continue; }
      if (row === skipRow || rows.has(row)) continue;
      rows.add(row);
      const items = [...row.querySelectorAll("button")].filter(navVisible);
      out.push(items.find((b) => b.getAttribute("aria-pressed") === "true") || items[0]);
    }
    return out.filter(Boolean);
  }
  // Where the focus goes when a screen opens: its main action, or the first choice.
  function entryTarget(screen) {
    const s = screen || app.dataset.screen;
    const pick = (sel) => [...document.querySelectorAll(sel)].find(navVisible) || null;
    if (s === "start") return pick(`#s-start [data-mode="${CSS.escape(startModus())}"]`) || pick("#s-start [data-mode]");
    if (s === "setup") return pick("#s-setup [data-action=go]");
    if (s === "result") return pick("#doneBtn");
    if (s === "review") return pick("#s-review [data-action=review-ok]");
    if (s === "gallery") return pick("#galleryGrid .tile") || pick("#s-gallery [data-action=home]");
    if (s === "error") return pick("#s-error [data-action=home]");
    if (s === "consent") return pick("#consentChoices .consent-choice");
    return null;
  }
  function isMainAction(el) {
    return !openOverlayEl() && el === entryTarget() && ["start", "setup", "result", "review"].includes(app.dataset.screen);
  }
  function markNav(el, byArrows) {
    document.querySelectorAll(".is-fokus").forEach((x) => x.classList.remove("is-fokus"));
    if (!el) return;
    el.classList.add("is-fokus");
    try { el.focus({ preventScroll: true }); } catch (e) { /* egal */ }
    const row = el.closest(".cards, .looks");
    if (row) row.scrollTo({ left: centredLeft(row, el), behavior: "smooth" });
    else { try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e) { /* egal */ } }
    navFocus = el; navByArrows = !!byArrows; navAt = Date.now();
    const felder = fokusFelder();
    if (felder.includes(el)) fokusIndex = felder.indexOf(el);    // "next"/"select" go on from here
  }
  // After a screen change: the entry focus, drawn only for a keyboard guest.
  function navEnter() {
    if (!S || !arrowNavOn() || !tastaturModus) return;
    setTimeout(() => {
      if (openOverlayEl()) return;
      const el = entryTarget();
      if (el) markNav(el, false);
    }, 0);
  }
  // The focus the arrows go on from: theirs, else the page's (a window that
  // just opened put it on its first control), as long as it is in view.
  function navCurrent() {
    const scope = navScope();
    const ok = (el) => el && scope && scope.contains(el) && navVisible(el) && el !== scope;
    if (ok(navFocus) && document.contains(navFocus)) return navFocus;
    return ok(document.activeElement) ? document.activeElement : null;
  }
  // The big view of a picture from the booth's gallery: left/right show the
  // one before / after it, in the gallery's order.
  function galleryStep(dir) {
    const keys = Object.keys(view.galleryItems || {});
    const i = keys.indexOf(view.uid);
    const next = keys[i + dir];
    if (i < 0 || !next) return;
    const items = view.galleryItems;
    openResult(items[next], { fromGallery: true });
    view.galleryItems = items;
    openFull();
    markNav($("#fullOverlay .overlay-close"), true);
  }
  function navMove(dir) {
    const sc = app.dataset.screen;
    if ((sc === "capture" || sc === "processing") && !openOverlayEl()) return;
    if (!$("#fullOverlay").hidden && view.fromGallery && (dir === "left" || dir === "right")) {
      return galleryStep(dir === "left" ? -1 : 1);
    }
    const cur = navCurrent();
    if (!cur) { markNav(openOverlayEl() ? navTargets()[0] : entryTarget(), true); return; }
    const row = cur.closest(CHOICE_ROWS);
    if (row && (dir === "left" || dir === "right")) {
      const items = [...row.querySelectorAll("button")].filter(navVisible);
      const next = items[items.indexOf(cur) + (dir === "left" ? -1 : 1)];
      if (next) { markNav(next, true); next.click(); markNav(next, true); }
      return;
    }
    const targets = navTargets(row).filter((el) => el !== cur);
    const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
    const i = nearestInDirection(rect(cur), targets.map(rect), dir);
    if (i >= 0) markNav(targets[i], true);
    else markNav(cur, true);                  // the edge: the focus stays, and stays drawn
  }
  function navActivate() {
    if (!navByArrows || Date.now() - navAt > NAV_FORGET_MS) return false;
    const el = navCurrent();
    if (!el || el !== navFocus) return false;
    if (isMainAction(el)) return false;       // the trigger presses it, with its bounce guard
    el.click();
    return true;
  }
  function arrowKeys(e) {
    if (!S || !arrowNavOn() || !$("#pinOverlay").hidden || attractOn) return;
    if ((CONTROLS().keys_actions || {})[e.code]) return;      // an own task for this key wins
    const t0 = e.target;
    if (t0 && (t0.matches && t0.matches("input, textarea, select, [contenteditable]"))) return;
    const dir = ARROWS[e.key];
    if (dir) {
      e.preventDefault(); e.stopImmediatePropagation();
      tastaturModus = true;
      bumpTimers(true);
      navMove(dir);
      return;
    }
    if (e.code === "Enter" || e.code === "NumpadEnter" || e.code === "Space") {
      if (navActivate()) { e.preventDefault(); e.stopImmediatePropagation(); bumpTimers(true); }
      return;
    }
    if (e.key === "Backspace") {
      e.preventDefault(); e.stopImmediatePropagation();
      bumpTimers(true);
      zurueckTaste();
    }
  }

  function triggerPrimary() {
    // Prellschutz: mechanische Buzzer schließen zwei- bis dreimal. Ohne diese
    // Sperre startet ein Druck zwei Durchgänge hintereinander.
    const now = Date.now();
    const gap = Number(CONTROLS().repeat_ms ?? 1200);
    if (now - lastTrigger < gap) return;
    lastTrigger = now;
    const sc = app.dataset.screen;
    // Steht irgendein Fenster offen, gehört der Druck ihm — nicht dem
    // Bildschirm dahinter. Die Maus-Variante prüfte das schon vollständig
    // (overlayOpen), der Buzzer nur drei von sechs Fenstern: Wer seine
    // E-Mail-Adresse tippte und dabei den Buzzer hörte, stand hinterher auf
    // dem Startbildschirm, mit der Tastatur darüber und der Adresse weg.
    if (overlayOpen()) return;
    if (sc === "start" && S && S.profile.modes.length) return openSetup(startModus());
    if (sc === "setup") return runCapture();
    if (sc === "consent") return toast(t("consent.use_screen"));   // a choice is made by tapping, never by the buzzer
    if (sc === "review") return reviewFertig();     // Buzzer heißt hier „passt, weiter"
    if (sc === "result") return view.fromGallery ? openGallery() : afterPhase();
    if (sc === "gallery" || sc === "error") return home();
  }

  // Ein Knopf, eine Aufgabe. „trigger" ist der Auslöser von oben; alles andere
  // führt geradewegs irgendwohin — aber nur dort, wo es etwas zu tun gibt:
  // Während die Kamera läuft oder gerechnet wird, tut kein Knopf etwas. Das ist
  // Absicht, sonst steht ein Gast mitten im Countdown plötzlich woanders.
  function fuehreAus(aktion) {
    const a = String(aktion || "off");
    if (a === "off") return;
    bumpTimers(true);
    if (a === "trigger") return triggerPrimary();
    if (a === "next") return fokusWeiter(1);
    if (a === "prev") return fokusWeiter(-1);
    if (a === "select") {
      // Solange nichts markiert ist, markiert der erste Druck — sonst löst ein
      // Knopf etwas aus, das der Gast noch gar nicht gesehen hat.
      if (!fokusWaehlen()) triggerPrimary();
      return;
    }
    const sc = app.dataset.screen;
    if (sc === "capture" || sc === "processing" || sc === "review") return;
    if (!$("#pinOverlay").hidden || !$("#boothMenu").hidden || !$("#confirmOverlay").hidden) return;
    if (a === "back") return home();
    if (a === "print") {
      if (sc === "result" && !$("#printBox").hidden) doPrint();
      return;
    }
    if (a === "gallery") {
      if (S && S.profile.gallery_on_booth !== false) openGallery();
      return;
    }
    if (a.startsWith("mode:")) {
      const modus = erlaubterModus(a.slice(5));
      if (!modus) return;
      // Prellschutz gilt auch hier: Ein zweiter Druck auf denselben Knopf darf
      // keinen zweiten Durchgang anfangen.
      const now = Date.now();
      if (now - lastTrigger < Number(CONTROLS().repeat_ms ?? 1200)) return;
      lastTrigger = now;
      openSetup(modus);
    }
  }

  // Die Fenster gehen an einem Dutzend Stellen auf und zu — mal per
  // `hidden = false`, mal ueber eine der closeX()-Funktionen. Der Fokus
  // hinterher einzeln nachzuziehen haette genau so lange gehalten, bis
  // jemand die dreizehnte Stelle schreibt. Deshalb an einer Stelle: wer
  // `hidden` an einem .overlay anfasst, loest das hier aus.
  (function fokusFuehrung() {
    let vorher = null;
    const beobachter = new MutationObserver((aenderungen) => {
      for (const a of aenderungen) {
        const el = a.target;
        if (!el.classList || !el.classList.contains("overlay")) continue;
        if (!el.hidden) {
          if (el.contains(document.activeElement)) continue;
          if (!vorher) vorher = document.activeElement;
          const ziel = el.querySelector(
            "input:not([disabled]), button:not([disabled]):not(.overlay-close), .overlay-close");
          if (ziel) ziel.focus({ preventScroll: true });
          else { el.tabIndex = -1; el.focus({ preventScroll: true }); }
        } else if (!overlayOpen()) {
          if (vorher && vorher.focus && document.contains(vorher)) {
            vorher.focus({ preventScroll: true });
          }
          vorher = null;
        }
      }
    });
    document.querySelectorAll(".overlay").forEach((el) =>
      beobachter.observe(el, { attributes: true, attributeFilter: ["hidden"] }));
  })();

  // Tab bleibt im offenen Fenster. `aria-modal="true"` behauptet das
  // ohnehin; ohne Klammer stimmte die Behauptung nicht.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const fenster = Array.from(document.querySelectorAll(".overlay")).find((el) => !el.hidden);
    if (!fenster) return;
    const ziele = Array.from(fenster.querySelectorAll(
      "input:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])"))
      .filter((el) => el.offsetParent !== null);
    if (!ziele.length) return;
    const erstes = ziele[0], letztes = ziele[ziele.length - 1];
    if (e.shiftKey && document.activeElement === erstes) { e.preventDefault(); letztes.focus(); }
    else if (!e.shiftKey && document.activeElement === letztes) { e.preventDefault(); erstes.focus(); }
    else if (!fenster.contains(document.activeElement)) { e.preventDefault(); erstes.focus(); }
  });

  function overlayOpen() {
    // #textOverlay gehört dazu. Ohne ihn war der Escape-Zweig für die
    // Gruß-Tastatur unerreichbar — aufgefallen bei der Durchsicht am 20.09.2026,
    // einen Tag nachdem ich diesen Zweig selbst geschrieben hatte.
    return ["#pinOverlay", "#boothMenu", "#confirmOverlay", "#qrOverlay", "#mailOverlay",
            "#fullOverlay", "#textOverlay", "#printFailOverlay", "#drawOverlay"]
      .some((id) => { const el = $(id); return el && !el.hidden; });
  }

  document.addEventListener("keydown", (e) => {
    if (dismissAttract()) { e.preventDefault(); return; }   // jede Taste weckt auf
    bumpTimers(true);
    const c = CONTROLS();
    const sc = app.dataset.screen;
    // With arrow navigation on (the default) the arrows move the focus
    // (arrowKeys) and are never start keys, whatever keys_start says.
    const start = (c.keys_start || ["Space", "Enter", "NumpadEnter", "PageDown", "ArrowRight", "F13"])
      .filter((k) => !(arrowNavOn() && /^Arrow/.test(k)));
    const back = c.keys_back || ["Escape"];
    const print = c.keys_print || ["KeyP"];
    // Eigene Zuordnung schlägt alles andere: Wer eine Taste einträgt, bestimmt
    // selbst, was sie tut.
    const eigene = (c.keys_actions || {})[e.code];
    if (eigene) {
      e.preventDefault();
      fuehreAus(eigene);
      return;
    }
    // Steht ein Fenster offen, schließt die Zurück-Taste erst das — sonst
    // landet man vom Vollbild aus unversehens auf dem Startbildschirm.
    if (back.includes(e.code) && overlayOpen()) {
      e.preventDefault();
      return zurueckTaste();
    }
    if (start.includes(e.code)) {
      e.preventDefault();
      triggerPrimary();
    } else if (back.includes(e.code) && sc !== "capture" && sc !== "processing") {
      zurueckTaste();
    } else if (print.includes(e.code) && sc === "result" && !$("#printBox").hidden) {
      doPrint();
    } else if (sc === "result" && !$("#printBox").hidden && /^(Digit|Numpad)[1-9]$/.test(e.code)
               && (S.profile.print || {}).number_keys !== false) {
      // Nachdruck per Ziffer: Der Betreiber braucht am Tresen schnell drei
      // Kopien und soll dafür nicht dreimal auf Plus tippen müssen.
      e.preventDefault();
      const n = Number(e.code.slice(-1));
      view.copies = Math.min(n, (view.print && view.print.max_now) || n);
      $("#copies").textContent = view.copies;
      doPrint();
    }
  });

  // The back key (keys_back; Backspace too with arrow navigation): an open
  // window closes first, otherwise back to the start - never while the
  // camera runs or the result is made.
  function zurueckTaste() {
    if (overlayOpen()) {
      if (!$("#fullOverlay").hidden) return closeFull();
      if (!$("#mailOverlay").hidden) return closeMail();
      if (!$("#textOverlay").hidden) return gastTextFertig(false);
      if (!$("#drawOverlay").hidden) return closeDraw();
      if (!$("#qrOverlay").hidden) { $("#qrOverlay").hidden = true; return; }
      return;                              // PIN und Menü schließen sich selbst
    }
    const sc = app.dataset.screen;
    if (sc !== "capture" && sc !== "processing") home();
  }
  document.addEventListener("keydown", arrowKeys, true);

  // Maus-Buzzer: Der Zeiger steht irgendwo — wo, weiß niemand. Deshalb zählt
  // nicht, worauf geklickt wurde, sondern nur, dass geklickt wurde. Der Klick
  // wird geschluckt, damit er nicht zusätzlich eine Schaltfläche unter dem
  // Zeiger drückt. Solange ein Fenster offen ist (PIN, Menü), bleibt die Maus
  // eine Maus — sonst käme der Betreiber nicht mehr in die Verwaltung.
  //
  // Touch and buzzer act alike — on purpose, a guest who cannot reach the
  // buzzer taps the screen instead. With the buzzer on (controls.mouse_start):
  //   * a press ON a control does what the control does, finger or mouse —
  //     except a real mouse press with mouse_swallow on, which stays the
  //     buzzer (the pointer sits anywhere, nobody aimed it);
  //   * a press on free space (background, live picture) triggers, like the
  //     buzzer — for a finger only on start and setup, where a tap on the
  //     background already meant "go". Elsewhere (result with "delete",
  //     review, gallery) a finger that misses a button does nothing;
  //   * one press counts once. A touch screen reports every tap a second time
  //     as a left click (compatibility mousedown/click). Until 11.11.2026 that
  //     echo counted as the buzzer: a finger on "Zurück" in setup started a
  //     recording (measured in headless Chrome). The echo is recognised and
  //     only the click of the tap is used.
  // With the buzzer off nothing here applies.
  const TOUCH_ECHO_MS = 800;     // compatibility events follow the touch within this time
  let lastTouchAt = -Infinity;
  let swallowClickUntil = 0;     // the click after a buzzer press belongs to the buzzer
  const noteTouch = (e) => { if (e.pointerType !== "mouse") lastTouchAt = performance.now(); };
  document.addEventListener("pointerdown", noteTouch, true);
  document.addEventListener("pointerup", noteTouch, true);
  document.addEventListener("touchstart", () => { lastTouchAt = performance.now(); }, { capture: true, passive: true });
  document.addEventListener("touchend", () => { lastTouchAt = performance.now(); }, { capture: true, passive: true });
  function fromTouch(e) {
    // Chromium says so directly; the time window covers every other browser.
    if (e.sourceCapabilities && e.sourceCapabilities.firesTouchEvents) return true;
    return performance.now() - lastTouchAt < TOUCH_ECHO_MS;
  }
  const isControl = (el) => !!(el && el.closest && el.closest("button,[data-action],input,a,[role='button']"));

  // <press-rule> — pure, no DOM; booth/tests/test_touch_and_buzzer.py runs it.
  const FREE_TAP_SCREENS = ["start", "setup"];
  function pressRule(p) {
    const none = { act: "none" };
    const want = String(p.mouseStart || "off");
    if (p.overlay) return none;
    if (p.touch) {
      if (want === "off" || p.onControl || !FREE_TAP_SCREENS.includes(p.screen)) return none;
      return { act: "trigger" };
    }
    // Zuerst die Knöpfe mit eigener Aufgabe: Ein Buzzer mit zwei Tasten hängt
    // als Maus am Rechner, und die rechte soll dann nicht dasselbe tun wie die
    // linke. Deshalb schlägt die eigene Zuordnung den allgemeinen Auslöser.
    const own = String((p.button === 2 && p.mouseRight) || (p.button === 1 && p.mouseMiddle) || "off");
    const hit = want !== "off" && (want === "any" || (want === "left" && p.button === 0)
      || (want === "right" && p.button === 2) || (want === "middle" && p.button === 1));
    if (own === "off" && !hit) return none;
    if (p.swallow === false && p.onControl && own === "off") return none;   // the control's own press
    return { act: own !== "off" ? own : "trigger" };
  }
  // </press-rule>

  document.addEventListener("mousedown", (e) => {
    if (fromTouch(e)) return;    // the echo of a finger: its click decides, see below
    const c = CONTROLS();
    const r = pressRule({ touch: false, button: e.button, mouseStart: c.mouse_start,
      mouseRight: c.mouse_right, mouseMiddle: c.mouse_middle, swallow: c.mouse_swallow,
      onControl: isControl(e.target), screen: app.dataset.screen, overlay: overlayOpen() });
    if (r.act === "none") return;
    e.preventDefault();
    e.stopPropagation();
    swallowClickUntil = performance.now() + 1000;
    if (dismissAttract()) return;
    fuehreAus(r.act);
  }, true);   // Erfassungsphase: vor allen Schaltflächen

  document.addEventListener("click", (e) => {
    if (fromTouch(e)) {
      if (attractJustDismissed()) return;
      const r = pressRule({ touch: true, mouseStart: CONTROLS().mouse_start,
        onControl: isControl(e.target), screen: app.dataset.screen, overlay: overlayOpen() });
      if (r.act === "none") return;            // the tap belongs to its button
      e.preventDefault();
      e.stopPropagation();                     // and only to the trigger: never both
      fuehreAus(r.act);
      return;
    }
    if (performance.now() < swallowClickUntil) {
      swallowClickUntil = 0;
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  (async function boot() {
    while (!S) { await refresh(); if (!S) await sleep(1000); }
    lang = startLang();
    if (SHOW_KEYS) {
      const bar = document.createElement("div");
      bar.id = "keysbar";
      bar.textContent = "Schlüsselmodus — jeder Text zeigt seinen Schlüssel. Ohne ?keys=1 neu laden für die normale Ansicht.";
      document.body.appendChild(bar);
    }
    applyTexts();
    renderStart();
    show("start");
    setInterval(refresh, 4000);
  })();
})();
