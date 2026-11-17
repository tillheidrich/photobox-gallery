/* Script of vorlagen.html, the booth's templates page (script-src 'self', R2-U1).

   Owner, 16.11.2026: "Saving a template works somehow, but then I cannot get
   back out of that window. I also cannot quickly mark which templates should
   be visible." So:
   * one bar at the top that sticks and always holds the way back: to the
     admin from the list, to the list from the editor ("Zurück zur Liste",
     "Speichern und zur Liste"); never under the on-screen keyboard;
   * every layout the box knows as a tile with a switch "sichtbar /
     ausgeblendet"; switches change nothing until "Sichtbarkeit speichern"
     (one floating save bar, as on the admin page); bulk actions and filters;
   * who decides is said in words: the box, the gallery, or the event (then
     the switches are locked until the box holds its settings, "festhalten");
   * deleting asks twice; built-ins and templates from the gallery are only hidden.
   Text goes into the page with textContent only. */
(() => {
  "use strict";
  const LANG = (document.documentElement.lang || "de").slice(0, 2) === "en" ? "en" : "de";
  const TX = {
    de: {
      back_admin: "← Verwaltung", back_list: "← Zurück zur Liste", title: "Vorlagen",
      title_new: "Neue Vorlage", title_edit: "Vorlage bearbeiten", save_back: "Speichern und zur Liste",
      leave_unsaved: "Nicht gespeichert – nochmal tippen zum Verwerfen", vis_unsaved: "Sichtbarkeit nicht gespeichert – nochmal tippen zum Verwerfen",
      saved_tpl: "„{name}“ gespeichert.", saved_stay: "Gespeichert. „Zurück zur Liste“ bringt dich zu allen Vorlagen.",
      deleted: "„{name}“ gelöscht.", not_saved: "Nicht gespeichert: {err}", failed: "Ging nicht: {err}",
      visible: "Sichtbar", hidden: "Ausgeblendet", locked_tile: "Vom Event vorgegeben",
      shots_1: "1 Bild", shots_n: "{n} Bilder", portrait: "Hochformat", landscape: "Querformat",
      own: "Eigene Vorlage", from_gallery: "Aus der Galerie", builtin: "Mitgeliefert", plain: "Mitgeliefert · schlicht",
      edit: "Bearbeiten", del: "Löschen", del_sure: "Wirklich löschen?", copy: "Als eigene kopieren",
      gallery_delete: "Kommt aus der Galerie und käme beim nächsten Abgleich zurück. Dort löschen, hier nur ausblenden.",
      count: "{v} von {n} sichtbar", count_filter: " · {m} gezeigt",
      changes: "{n} Änderung(en) nicht gespeichert · danach {v} sichtbar", none_left: "Mindestens ein Layout muss sichtbar bleiben.",
      no_own: "Es gibt noch keine eigene Vorlage.", vis_saved: "Gespeichert. Die Gäste sehen jetzt {v} Layouts.",
      empty_filter: "Kein Layout passt zu diesem Filter.",
      src_box: "Diese Box arbeitet ohne Galerie. Was du hier einschaltest, sehen die Gäste.",
      src_gallery: "Diese Box hängt an einer Galerie. Eine Änderung hier gilt sofort; die Galerie ersetzt sie, sobald sie dieser Box ein neues Profil schickt (wenn dort an der Box oder am Event etwas geändert wird).",
      src_gallery_held: "Diese Box hält ihre Einstellungen fest: Die Galerie ändert hier nichts, auch nicht die Layouts, bis das Festhalten gelöst wird.",
      src_event: "Die Layouts gibt das Event „{event}“ vor (Galerie → Event → Layouts für dieses Event). Die Schalter sind gesperrt, damit eine Änderung hier nicht beim nächsten Abgleich verschwindet.",
      src_event_held: "Das Event „{event}“ gibt eigentlich die Layouts vor. Diese Box hält ihre Einstellungen fest, deshalb gilt, was du hier wählst.",
      hold: "Einstellungen dieser Box festhalten", hold_sure: "Dann übernimmt die Box nichts mehr aus der Galerie – nochmal tippen",
      unhold: "Festhalten lösen", unhold_sure: "Die Galerie setzt dann ihre Einstellungen – nochmal tippen",
      hold_on: "Festgehalten. Die Galerie ändert an dieser Box nichts.", hold_off: "Gelöst. Die Galerie setzt beim nächsten Abgleich ihre Einstellungen.",
      server_note: "Was du hier baust, wandert beim nächsten Sync in die Galerie und von dort auf die anderen Boxen.",
      packing: "Wird gepackt …", done: "Fertig: {file}", pick_file: "Erst eine Datei wählen.", reading: "Wird gelesen …",
      imported: "{t} Vorlage(n), {m} Bild(er) übernommen.", copy_name: "{name} (eigene Fassung)", wrong: "falsch",
      frames_src: "Der Film läuft im Fenster des Rahmens, Titel und Datum des Events stehen darin. Ein Schalter gilt sofort.",
      frames_src_event: "Die Rahmen gibt das Event „{event}“ vor (Galerie → Event → Rahmen für Boomerang & Video). Die Schalter sind gesperrt.",
      frames_src_event_held: "Das Event „{event}“ gibt eigentlich die Rahmen vor. Diese Box hält ihre Einstellungen fest, deshalb gilt, was du hier wählst.",
      frames_boomerang: "Boomerang", frames_video: "Video", frames_on: "Rahmen an", frames_off: "ohne Rahmen",
      frames_none: "Ohne Rahmen", frames_none_hint: "Der Film ohne Rahmen", frames_saved: "Rahmen gespeichert.",
    },
    en: {
      back_admin: "← Admin", back_list: "← Back to the list", title: "Templates",
      title_new: "New template", title_edit: "Edit template", save_back: "Save and back to the list",
      leave_unsaved: "Not saved – tap again to discard", vis_unsaved: "Visibility not saved – tap again to discard",
      saved_tpl: "“{name}” saved.", saved_stay: "Saved. “Back to the list” takes you to all templates.",
      deleted: "“{name}” deleted.", not_saved: "Not saved: {err}", failed: "Did not work: {err}",
      visible: "Visible", hidden: "Hidden", locked_tile: "Set by the event",
      shots_1: "1 photo", shots_n: "{n} photos", portrait: "Portrait", landscape: "Landscape",
      own: "Own template", from_gallery: "From the gallery", builtin: "Built-in", plain: "Built-in · plain",
      edit: "Edit", del: "Delete", del_sure: "Really delete?", copy: "Copy as own template",
      gallery_delete: "Comes from the gallery and would return at the next sync. Delete it there, only hide it here.",
      count: "{v} of {n} visible", count_filter: " · {m} shown",
      changes: "{n} change(s) not saved · then {v} visible", none_left: "At least one layout has to stay visible.",
      no_own: "There is no own template yet.", vis_saved: "Saved. Guests now see {v} layouts.",
      empty_filter: "No layout matches this filter.",
      src_box: "This booth works without a gallery. What you switch on here is what guests see.",
      src_gallery: "This booth is connected to a gallery. A change here applies at once; the gallery replaces it as soon as it sends this booth a new profile.",
      src_gallery_held: "This booth holds its settings: the gallery changes nothing here, layouts included, until the hold is released.",
      src_event: "The event “{event}” sets the layouts (gallery → event → layouts for this event). The switches are locked so a change here does not vanish at the next sync.",
      src_event_held: "The event “{event}” would set the layouts. This booth holds its settings, so your choice here applies.",
      hold: "Hold this booth's settings", hold_sure: "The booth then takes nothing from the gallery – tap again",
      unhold: "Release the hold", unhold_sure: "The gallery then sets its settings – tap again",
      hold_on: "Held. The gallery changes nothing on this booth.", hold_off: "Released. The gallery sets its settings at the next sync.",
      server_note: "What you build here goes to the gallery at the next sync and from there to the other booths.",
      packing: "Packing …", done: "Done: {file}", pick_file: "Choose a file first.", reading: "Reading …",
      imported: "{t} template(s), {m} picture(s) taken over.", copy_name: "{name} (own version)", wrong: "wrong",
      frames_src: "The film plays in the frame's window, with the event's title and date. A switch applies at once.",
      frames_src_event: "The event “{event}” sets the frames (gallery → event → frames for boomerang & video). The switches are locked.",
      frames_src_event_held: "The event “{event}” would set the frames. This booth holds its settings, so your choice here applies.",
      frames_boomerang: "Boomerang", frames_video: "Video", frames_on: "frames on", frames_off: "no frames",
      frames_none: "No frame", frames_none_hint: "The film without a frame", frames_saved: "Frames saved.",
    },
  };
  const t = (key, vars) => {
    let s = (TX[LANG] && TX[LANG][key]) || TX.de[key] || key;
    for (const [k, v] of Object.entries(vars || {})) s = s.split("{" + k + "}").join(String(v));
    return s;
  };
  window.pbVorlagenTexte = TX;          // the test compares both languages

  let token = "", pin = "", daten = null;
  let wahl = new Map();                 // layout key -> wanted visibility (unsaved until "speichern")
  const filter = { art: "", bilder: "", lage: "" };
  let modus = "liste", editor = null, editorBasis = "", offeneDatei = "";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

  // ── sign-in ──
  [1, 2, 3, 4, 5, 6, 7, 8, 9, "C", 0, "OK"].forEach((k) => {
    const b = el("button", "", String(k)); b.type = "button";
    b.onclick = () => {
      if (k === "C") pin = ""; else if (k === "OK") return login(); else if (pin.length < 8) pin += k;
      $("pinOut").textContent = "•".repeat(pin.length);
    };
    $("pad").appendChild(b);
  });
  async function call(pfad, opts = {}) {
    const r = await fetch(pfad, { ...opts, headers: { "Content-Type": "application/json", "X-Admin-Token": token } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  async function login() {
    try { token = (await call("/api/admin/login", { method: "POST", body: JSON.stringify({ pin }) })).token; }
    catch (e) { pin = ""; $("pinOut").textContent = /^Zu viele/.test(e.message) ? e.message : t("wrong"); return; }
    $("pin").hidden = true; $("main").hidden = false;
    zeigeListe();
    laden();
  }

  // ── messages and two-step buttons ──
  let notizUhr = null;
  function notiz(text, ton) {
    const n = $("barNote");
    n.textContent = text || ""; n.dataset.tone = ton || "";
    clearTimeout(notizUhr);
    if (text && ton !== "bad") notizUhr = setTimeout(() => { n.textContent = ""; n.dataset.tone = ""; }, 9000);
  }
  // The first tap arms the button and says what the second does; nothing
  // native (confirm) blocks the page.
  function zweimal(knopf, frage) {
    if (knopf.dataset.armed === "1") { entschaerfe(knopf); return true; }
    knopf.dataset.armed = "1";
    knopf.dataset.alt = knopf.textContent;
    knopf.textContent = frage;
    knopf._uhr = setTimeout(() => entschaerfe(knopf), 4500);
    return false;
  }
  function entschaerfe(knopf) {
    clearTimeout(knopf._uhr);
    if (knopf.dataset.armed === "1") { knopf.textContent = knopf.dataset.alt; knopf.dataset.armed = ""; }
  }

  // ── the layouts and their switches ──
  async function laden() {
    daten = await call("/api/admin/templates");
    wahl = new Map(daten.layouts.map((l) => [l.key, !!l.visible]));
    const wahlListe = $("kopieren");
    wahlListe.replaceChildren(...daten.builtin.map((tpl) => { const o = el("option", "", tpl.name); o.value = tpl.id; return o; }));
    zeichneQuelle();
    zeichneKacheln();
    ladeRahmen().catch((e) => notiz(t("failed", { err: e.message || e }), "bad"));
  }

  // ── Frames for boomerang and video: one row per mode, a switch saves at once ──
  let rahmen = null;
  async function ladeRahmen() {
    rahmen = await call("/api/admin/frames");
    zeichneRahmen();
  }
  function zeichneRahmen() {
    const karte = $("rahmenKarte");
    karte.hidden = false;
    const v = rahmen.visibility, zu = !!v.locked;
    $("rahmenQuelle").textContent = v.source === "event"
      ? t(v.hold ? "frames_src_event_held" : "frames_src_event", { event: v.event || "?" }) : t("frames_src");
    $("rahmenModi").replaceChildren(...["boomerang", "video"].map((modus) => {
      const m = rahmen.modes[modus];
      const teil = el("section", "rahmen-modus");
      teil.appendChild(el("h3", "", t("frames_" + modus) + " · " + (m.enabled ? t("frames_on") : t("frames_off"))));
      const reihe = el("div", "kacheln");
      const kacheln = m.frames.map((f) => rahmenKachel(modus, f, f.visible && m.enabled, zu));
      kacheln.push(rahmenKachel(modus, { id: "none", name: t("frames_none"), format: "", kind: "none" },
        !m.enabled || m.none, zu));
      reihe.replaceChildren(...kacheln);
      teil.appendChild(reihe);
      return teil;
    }));
  }
  function rahmenKachel(modus, f, an, zu) {
    const k = el("article", "rkachel");
    k.dataset.sichtbar = an ? "1" : "0";
    const bild = el("div", "bild");
    const b = el("div", "blatt");
    const c = f.canvas || [1080, 1920], skala = Math.min(BLATT_W / c[0], BLATT_H / c[1]);
    b.style.width = Math.round(c[0] * skala) + "px";
    b.style.height = Math.round(c[1] * skala) + "px";
    if (f.picture) { b.dataset.url = f.picture; beobachter ? beobachter.observe(b) : anstellen(b, false); }
    bild.appendChild(b);
    k.appendChild(bild);
    const text = el("div");
    text.appendChild(el("b", "", f.name));
    text.appendChild(el("small", "", f.kind === "none" ? t("frames_none_hint")
      : [f.format, f.kind === "own" ? t("own") : t("builtin")].join(" · ")));
    k.appendChild(text);
    if (zu) k.appendChild(el("span", "gesperrt", t("locked_tile")));
    const schalter = el("button", "schalter");
    schalter.type = "button";
    schalter.setAttribute("aria-pressed", String(an));
    schalter.appendChild(el("i", "", an ? "✓" : ""));
    schalter.appendChild(el("span", "", t(an ? "visible" : "hidden")));
    schalter.disabled = zu;
    schalter.onclick = () => rahmenUmschalten(modus, f.id, !an);
    k.appendChild(schalter);
    return k;
  }
  async function rahmenUmschalten(modus, id, an) {
    const m = rahmen.modes[modus];
    let ids = m.enabled ? m.frames.filter((f) => f.visible).map((f) => f.id) : [];
    let none = !m.enabled || m.none;
    if (id === "none") none = an;
    else ids = an ? ids.concat([id]) : ids.filter((x) => x !== id);
    try {
      const d = await call("/api/admin/frames/visibility", { method: "POST", body: JSON.stringify({ mode: modus, ids, none }) });
      rahmen = { modes: d.modes, visibility: d.visibility };
      zeichneRahmen();
      notiz(t("frames_saved") + (d.warning ? " " + d.warning : ""), d.warning ? "bad" : "ok");
    } catch (e) { notiz(t("not_saved", { err: e.message || e }), "bad"); }
  }
  const offen = () => daten.layouts.filter((l) => wahl.get(l.key) !== !!l.visible);
  const sichtbarNachher = () => daten.layouts.filter((l) => wahl.get(l.key)).length;
  const gesperrt = () => !!(daten && daten.visibility.locked);

  function zeichneQuelle() {
    const v = daten.visibility, q = $("quelle");
    q.dataset.source = v.source;
    const text = v.source === "box" ? t("src_box")
      : v.source === "event" ? t(v.hold ? "src_event_held" : "src_event", { event: v.event || "?" })
      : t(v.hold ? "src_gallery_held" : "src_gallery");
    const kids = [el("p", "", text)];
    if (v.server) {
      const k = el("button", "btn", v.hold ? t("unhold") : t("hold")); k.type = "button";
      k.onclick = async () => {
        if (!zweimal(k, v.hold ? t("unhold_sure") : t("hold_sure"))) return;
        try {
          await call("/api/admin/profile/hold", { method: "POST", body: JSON.stringify({ hold: !v.hold }) });
          await laden();
          notiz(v.hold ? t("hold_off") : t("hold_on"), "ok");
        } catch (e) { notiz(t("failed", { err: e.message || e }), "bad"); }
      };
      kids.push(k);
    }
    q.replaceChildren(...kids);
    $("sammel").querySelectorAll("button").forEach((b) => { b.disabled = gesperrt(); });
  }

  function passt(l) {
    if (filter.art && l.kind !== filter.art) return false;
    if (filter.bilder && (filter.bilder === "4" ? l.shots < 4 : l.shots !== Number(filter.bilder))) return false;
    if (filter.lage && l.orientation !== filter.lage) return false;
    return true;
  }

  function zeichneKacheln() {
    const gezeigt = daten.layouts.filter(passt);
    const sichtbar = daten.layouts.filter((l) => l.visible).length;
    $("zaehler").textContent = t("count", { v: sichtbar, n: daten.layouts.length })
      + (gezeigt.length !== daten.layouts.length ? t("count_filter", { m: gezeigt.length }) : "");
    const liste = $("kacheln");
    if (!gezeigt.length) { liste.replaceChildren(el("p", "leer", t("empty_filter"))); zeichneSpeicherleiste(); return; }
    liste.replaceChildren(...gezeigt.map(kachel));
    zeichneSpeicherleiste();
  }

  function kachel(l) {
    const k = el("article", "kachel");
    k.dataset.key = l.key;
    const an = wahl.get(l.key);
    k.dataset.sichtbar = an ? "1" : "0";
    k.dataset.geaendert = an !== !!l.visible ? "1" : "0";
    const bild = el("div", "bild");
    bild.appendChild(blatt(l));
    k.appendChild(bild);
    const text = el("div");
    text.appendChild(el("b", "", l.name));
    text.appendChild(el("small", "", [l.shots === 1 ? t("shots_1") : t("shots_n", { n: l.shots }),
      t(l.orientation === "landscape" ? "landscape" : "portrait"), papier(l.paper)].join(" · ")));
    text.appendChild(el("span", "herkunft", l.kind === "builtin" ? t(l.plain ? "plain" : "builtin")
      : l.origin === "gallery" ? t("from_gallery") : t("own")));
    k.appendChild(text);
    // Locked by the event: said once above the switch, not inside it -
    // "Vom Event vorgegeben · Sichtbar" broke onto two lines in the button.
    if (gesperrt()) k.appendChild(el("span", "gesperrt", t("locked_tile")));
    const schalter = el("button", "schalter");
    schalter.type = "button";
    schalter.setAttribute("aria-pressed", String(an));
    schalter.appendChild(el("i", "", an ? "✓" : ""));
    schalter.appendChild(el("span", "", t(an ? "visible" : "hidden")));
    if (gesperrt()) schalter.title = t("locked_tile");
    schalter.disabled = gesperrt();
    schalter.onclick = () => { wahl.set(l.key, !wahl.get(l.key)); k.replaceWith(kachel(l)); zeichneSpeicherleiste(); };
    k.appendChild(schalter);
    const mehr = el("div", "mehr");
    if (l.kind === "own") {
      const b = el("button", "btn", t("edit")); b.type = "button";
      b.onclick = () => oeffne(l.template, l.file);
      mehr.appendChild(b);
      if (l.deletable) {
        const d = el("button", "btn", t("del")); d.type = "button";
        d.onclick = async () => {
          if (!zweimal(d, t("del_sure"))) return;
          try {
            await call("/api/admin/templates/delete", { method: "POST", body: JSON.stringify({ file: l.file }) });
            await laden();
            notiz(t("deleted", { name: l.name }), "ok");
          } catch (e) { notiz(t("failed", { err: e.message || e }), "bad"); }
        };
        mehr.appendChild(d);
      } else {
        mehr.appendChild(el("p", "", t("gallery_delete")));
      }
    } else {
      const c = el("button", "btn", t("copy")); c.type = "button";
      c.onclick = () => kopiere(l.template);
      mehr.appendChild(c);
    }
    k.appendChild(mehr);
    return k;
  }
  const papier = (p) => String(p || "").replace("x", "×").replace("-quer", "");

  // ── Pictures of the tiles ──
  // ~45 layouts took seconds, two pictures at a time and all at once on open
  // (17.11.2026). Now: a placeholder of the sheet's exact size first (the
  // picture takes its place without moving anything), pictures only for
  // tiles near the screen, four at a time, and an address with the booth's
  // cache key (template content, style, frame, texts, day): the browser
  // keeps the answer, so the second open shows them at once, and the booth
  // has them on disk under that key. While a guest's run is going the booth
  // renders nothing (503); the tile keeps its placeholder and asks again.
  const BLATT_W = 220, BLATT_H = 210, PARALLEL = 4;
  function blatt(l) {
    const canvas = (l.template && l.template.canvas) || [1200, 1800];
    const skala = Math.min(BLATT_W / canvas[0], BLATT_H / canvas[1]);
    const w = Math.round(canvas[0] * skala), h = Math.round(canvas[1] * skala);
    const b = el("div", "blatt");
    b.style.width = w + "px";
    b.style.height = h + "px";
    const platz = el("div", "platzhalter");
    ((l.template && l.template.slots) || []).forEach((s) => {
      const f = el("i");
      f.style.cssText = "left:" + s.x * skala + "px;top:" + s.y * skala + "px;width:" + s.w * skala +
        "px;height:" + s.h * skala + "px";
      platz.appendChild(f);
    });
    b.appendChild(platz);
    const url = bildAdresse(l);
    if (bilder.has(url)) zeige(b, bilder.get(url));
    else { b.dataset.url = url; beobachter ? beobachter.observe(b) : anstellen(b, false); }
    return b;
  }
  const bildAdresse = (l) => l.thumb || l.picture || ("/api/admin/templates/picture?" +
    (l.kind === "own" ? "kind=own&id=" + encodeURIComponent(l.file) : "kind=builtin&id=" + encodeURIComponent(l.id)));
  function zeige(b, src) {
    const img = el("img"); img.alt = "";
    img.onload = () => { b.dataset.fertig = "1"; };
    img.src = src;
    b.appendChild(img);
  }
  // Kept for the page's life (blob URLs of the answers), so a filter or a
  // switch draws the tile again without asking.
  const bilder = new Map(), unterwegs = new Map(), warteschlange = [];
  let laufend = 0;
  const beobachter = "IntersectionObserver" in window ? new IntersectionObserver((eintraege) => {
    for (const e of eintraege) {
      if (!e.isIntersecting) continue;
      beobachter.unobserve(e.target);
      anstellen(e.target, true);
    }
  }, { rootMargin: "400px 0px" }) : null;
  function anstellen(b, vorn) {
    const url = b.dataset.url;
    if (!url) return;
    if (bilder.has(url)) return zeige(b, bilder.get(url));
    const dann = (u) => { if (u && b.isConnected) zeige(b, u); };
    if (unterwegs.has(url)) { unterwegs.get(url).then(dann); return; }
    const versprochen = new Promise((fertig) => {
      const auftrag = () => holeBild(url).then((u) => { if (u) bilder.set(url, u); unterwegs.delete(url); fertig(u); });
      vorn ? warteschlange.unshift(auftrag) : warteschlange.push(auftrag);
    });
    unterwegs.set(url, versprochen);
    versprochen.then(dann);
    weiter();
  }
  function weiter() {
    while (laufend < PARALLEL && warteschlange.length) {
      laufend++;
      warteschlange.shift()().finally(() => { laufend--; weiter(); });
    }
  }
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  async function holeBild(url) {
    // The kiosk's own thumbnails need no token; the admin pictures do.
    const kopf = url.startsWith("/api/admin/") ? { "X-Admin-Token": token } : {};
    for (let versuch = 0; versuch < 60; versuch++) {
      try {
        const r = await fetch(url, { headers: kopf });
        if (r.ok) return URL.createObjectURL(await r.blob());
        if (r.status !== 503) return "";
        // A guest's run is going: the booth renders nothing now. Wait, ask again.
        await pause(Math.max(2, Number(r.headers.get("Retry-After")) || 5) * 1000);
      } catch (e) { return ""; }
    }
    return "";
  }

  // ── saving the switches: one bar, one button ──
  function zeichneSpeicherleiste(text, ton) {
    const n = offen().length;
    const leiste = $("savebar");
    leiste.hidden = modus !== "liste" || (!n && !text);
    const info = $("saveInfo");
    info.textContent = text || t("changes", { n, v: sichtbarNachher() });
    info.dataset.tone = ton || "";
    $("sichtbarSpeichern").disabled = !n;
    $("verwerfen").hidden = !n;
  }
  async function sichtbarkeitSpeichern() {
    if (!sichtbarNachher()) return zeichneSpeicherleiste(t("none_left"), "bad");
    const an = daten.layouts.filter((l) => wahl.get(l.key));
    const knopf = $("sichtbarSpeichern");
    knopf.disabled = true;
    try {
      const d = await call("/api/admin/templates/visibility", { method: "POST", body: JSON.stringify({
        templates: an.filter((l) => l.kind === "own").map((l) => l.file),
        builtins: an.filter((l) => l.kind === "builtin").map((l) => l.id) }) });
      daten = { ...daten, layouts: d.layouts, visibility: d.visibility };
      wahl = new Map(daten.layouts.map((l) => [l.key, !!l.visible]));
      zeichneQuelle();
      zeichneKacheln();
      const v = daten.layouts.filter((l) => l.visible).length;
      notiz(t("vis_saved", { v }) + (d.warning ? " " + d.warning : ""), d.warning ? "bad" : "ok");
    } catch (e) {
      zeichneSpeicherleiste(t("not_saved", { err: e.message || e }), "bad");
      knopf.disabled = false;
    }
  }
  $("sichtbarSpeichern").onclick = sichtbarkeitSpeichern;
  $("verwerfen").onclick = () => { wahl = new Map(daten.layouts.map((l) => [l.key, !!l.visible])); zeichneKacheln(); };

  // Bulk actions work on the tiles the filter shows; none of them may leave zero.
  $("sammel").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sammel]");
    if (!b || gesperrt()) return;
    const art = b.dataset.sammel;
    const vorher = new Map(wahl);
    const gezeigt = daten.layouts.filter(passt);
    if (art === "alle") gezeigt.forEach((l) => wahl.set(l.key, true));
    if (art === "keine") gezeigt.forEach((l) => wahl.set(l.key, false));
    if (art === "eigene") {
      if (!daten.layouts.some((l) => l.kind === "own")) return zeichneSpeicherleiste(t("no_own"), "bad");
      daten.layouts.forEach((l) => wahl.set(l.key, l.kind === "own"));
    }
    if (!sichtbarNachher()) { wahl = vorher; zeichneKacheln(); return zeichneSpeicherleiste(t("none_left"), "bad"); }
    zeichneKacheln();
  });
  $("filter").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    const gruppe = c.closest("[data-filter]");
    filter[gruppe.dataset.filter] = c.dataset.wert;
    gruppe.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
    zeichneKacheln();
  });

  // ── list and editor: two states of one page, the bar knows the way back ──
  function zeigeListe() {
    modus = "liste";
    entschaerfe($("zurueck"));          // first: disarming puts the old label back
    notiz("");
    $("liste").hidden = false; $("editorSeite").hidden = true;
    $("zurueck").textContent = t("back_admin");
    $("titel").textContent = t("title");
    $("neu").hidden = false; $("fertig").hidden = true;
    if (daten) zeichneSpeicherleiste();
  }
  function zeigeEditor(titel) {
    modus = "editor";
    entschaerfe($("zurueck"));
    notiz("");
    $("liste").hidden = true; $("editorSeite").hidden = false; $("savebar").hidden = true;
    $("zurueck").textContent = t("back_list");
    $("titel").textContent = titel;
    $("neu").hidden = true; $("fertig").hidden = false;
    window.scrollTo(0, 0);
  }
  const ungespeichert = () => !!(editor && JSON.stringify(editor.vorlage()) !== editorBasis);
  function merkeBasis() { editorBasis = editor ? JSON.stringify(editor.vorlage()) : ""; }

  function oeffne(vorlage, datei) {
    offeneDatei = datei || "";
    zeigeEditor(datei ? t("title_edit") : t("title_new"));
    editor = window.pbVorlagenEditor.start({
      wurzel: $("editor"),
      vorlage: vorlage,
      speichern: (v) => call("/api/admin/templates", { method: "POST", body: JSON.stringify(v) })
        .then((d) => {
          offeneDatei = d.asset.file;
          // The editor takes the checked template over once this resolves.
          setTimeout(merkeBasis, 0);
          notiz(t("saved_stay"), "ok");
          return d;
        }, (e) => { notiz(t("not_saved", { err: e.message || e }), "bad"); throw e; }),
      loeschen: datei ? () => call("/api/admin/templates/delete", { method: "POST", body: JSON.stringify({ file: offeneDatei }) })
        .then(() => zurListe(t("deleted", { name: (editor && editor.vorlage().name) || datei }))) : null,
    });
    merkeBasis();
  }
  function kopiere(quelle) {
    const kopie = JSON.parse(JSON.stringify(quelle));
    kopie.id = quelle.id + "-eigen";
    kopie.name = t("copy_name", { name: quelle.name });
    delete kopie.plain;
    oeffne(kopie, "");
  }
  async function zurListe(text, ton) {
    $("editor").replaceChildren();
    editor = null; editorBasis = ""; offeneDatei = "";
    zeigeListe();
    try { await laden(); } catch (e) { notiz(t("failed", { err: e.message || e }), "bad"); return; }
    if (text) notiz(text, ton || "ok");
  }

  // "Zurück": from the editor to the list, from the list to the admin. What is
  // not saved costs a second tap, never a native dialog.
  $("zurueck").onclick = () => {
    const k = $("zurueck");
    if (modus === "editor") {
      if (ungespeichert() && !zweimal(k, t("leave_unsaved"))) return;
      return zurListe();
    }
    if (daten && offen().length && !zweimal(k, t("vis_unsaved"))) return;
    location.href = "/admin";
  };
  // "Speichern und zur Liste": the editor's template as it stands, then back.
  $("fertig").onclick = async () => {
    if (!editor) return zurListe();
    const v = editor.vorlage();
    const knopf = $("fertig");
    knopf.disabled = true;
    try {
      const d = await call("/api/admin/templates", { method: "POST", body: JSON.stringify(v) });
      await zurListe(t("saved_tpl", { name: (d.template && d.template.name) || v.name || "" })
        + ((d.hinweise || []).length ? " " + d.hinweise.join(" ") : ""));
    } catch (e) {
      notiz(t("not_saved", { err: e.message || e }), "bad");
    } finally { knopf.disabled = false; }
  };
  $("neu").onclick = $("neu2").onclick = () => oeffne(undefined, "");
  $("kopierenLos").onclick = () => {
    const quelle = daten && daten.builtin.find((x) => x.id === $("kopieren").value);
    if (quelle) kopiere(quelle);
  };

  // ── take along and bring in (.abzug) ──
  // A download does not go through call(): a file comes back, not JSON. The
  // sign-in key has to go with it all the same, so fetch and offer it here.
  async function holePaket(frage) {
    const hinweis = $("paketHinweis");
    hinweis.textContent = t("packing");
    try {
      const r = await fetch("/api/admin/templates/export?" + (frage || ""), { headers: { "X-Admin-Token": token } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const blob = await r.blob();
      const name = (r.headers.get("Content-Disposition") || "").split("filename=")[1];
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (name || "vorlagen.abzug").replace(/"/g, "");
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      hinweis.textContent = t("done", { file: a.download });
    } catch (e) {
      hinweis.textContent = t("failed", { err: e && e.message ? e.message : e });
    }
  }
  async function paketEinlesen() {
    const hinweis = $("paketHinweis");
    const datei = $("paketDatei").files[0];
    if (!datei) { hinweis.textContent = t("pick_file"); return; }
    hinweis.textContent = t("reading");
    const form = new FormData();
    form.append("paket", datei);
    try {
      const r = await fetch("/api/admin/templates/import", { method: "POST", body: form, headers: { "X-Admin-Token": token } });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || r.statusText);
      hinweis.textContent = t("imported", { t: d.vorlagen.length, m: d.medien.length })
        + (d.hinweise && d.hinweise.length ? " — " + d.hinweise.slice(0, 3).join(" · ") : "");
      $("paketDatei").value = "";
      laden();
    } catch (e) {
      hinweis.textContent = t("failed", { err: e && e.message ? e.message : e });
    }
  }
  $("ausgeben").onclick = () => holePaket("");
  $("einlesen").onclick = paketEinlesen;
})();
