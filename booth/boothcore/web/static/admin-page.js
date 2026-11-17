/* Script of admin.html, moved out of the page so its Content-Security-Policy
   can say script-src 'self' (R2-U1). */
(() => {
  let token = "", pin = "";
  const $ = (id) => document.getElementById(id);
  const pad = $("pad");
  function pinTaste(k) {
    if (k === "C") pin = ""; else if (k === "OK") return login(); else if (pin.length < 8) pin += k;
    $("pinOut").textContent = "•".repeat(pin.length);
  }
  [1,2,3,4,5,6,7,8,9,"C",0,"OK"].forEach((k) => {
    const b = document.createElement("button"); b.textContent = k;
    b.onclick = () => pinTaste(k);
    pad.appendChild(b);
  });
  // The same from a keyboard, while the PIN screen is the one in front.
  document.addEventListener("keydown", (e) => {
    if ($("pin").hidden || getComputedStyle($("pin")).display === "none") return;
    if (/^[0-9]$/.test(e.key)) return pinTaste(Number(e.key));
    if (e.key === "Enter") return pinTaste("OK");
    if (e.key === "Backspace" || e.key === "Escape") return pinTaste("C");
  });
  async function call(path, opts = {}) {
    // Bei einem Datei-Upload (FormData) darf der Inhaltstyp NICHT gesetzt
    // werden: Den schreibt der Browser samt Grenzmarke selbst, und eine von
    // Hand gesetzte Zeile macht daraus einen Fehler ohne Fehlermeldung.
    const kopf = { "X-Admin-Token": token };
    if (!(opts.body instanceof FormData)) kopf["Content-Type"] = "application/json";
    const r = await fetch(path, { ...opts, headers: kopf });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.result || r.statusText);
    return data;
  }
  // A factory-PIN login only opens this step: type the new PIN, then once more.
  // The server checks the rules again (weak_pin_reason); the text here only explains them.
  function newPin() {
    return new Promise((done) => {
      let first = "", entry = "";
      const hint = $("newpinHint").textContent;
      const show = () => { $("newpinOut").textContent = "•".repeat(entry.length); };
      const reset = (msg) => {
        first = ""; entry = ""; show();
        $("newpinTitle").textContent = "Eigene PIN festlegen";
        $("newpinHint").textContent = msg || hint;
      };
      [1,2,3,4,5,6,7,8,9,"C",0,"OK"].forEach((k) => {
        const b = document.createElement("button"); b.textContent = k;
        b.onclick = async () => {
          if (k === "C") { entry = ""; show(); return; }
          if (k !== "OK") { if (entry.length < 12) entry += k; show(); return; }
          if (!first) {
            if (entry.length < 4) { $("newpinHint").textContent = "Mindestens 4 Ziffern, bitte."; return; }
            first = entry; entry = ""; show();
            $("newpinTitle").textContent = "Neue PIN noch einmal";
            $("newpinHint").textContent = "Zur Bestätigung dieselbe PIN noch einmal eingeben.";
            return;
          }
          if (entry !== first) return reset("Die beiden Eingaben waren verschieden. Bitte von vorn.");
          try {
            await call("/api/admin/pin", { method: "POST", body: JSON.stringify({ new_pin: first }) });
          } catch (e) { return reset(e.message); }
          $("newpin").hidden = true;
          done();
        };
        $("padNew").appendChild(b);
      });
      $("pin").hidden = true; $("newpin").hidden = false;
    });
  }
  async function login() {
    let res;
    try { res = await call("/api/admin/login", { method: "POST", body: JSON.stringify({ pin }) }); token = res.token; }
    catch (e) { pin = ""; $("pinOut").textContent = /^Zu viele/.test(e.message) ? e.message : "falsch"; return; }
    await enter(res);
  }
  // One chip per card, built from the headings: a new card shows up by itself.
  // A card may carry data-jump for a shorter chip than its heading.
  const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
  function jumpBar() {
    $("jump").innerHTML = "";
    document.querySelectorAll("#main .card > h2").forEach((h, i) => {
      const card = h.parentElement;
      if (!card.id) card.id = "abschnitt-" + i;
      const a = document.createElement("a");
      a.href = "#" + card.id; a.textContent = (card.dataset.jump || h.textContent).trim();
      a.addEventListener("click", (e) => { e.preventDefault(); card.scrollIntoView({ behavior: smooth(), block: "start" }); });
      $("jump").appendChild(a);
    });
  }
  // "Nach oben" appears once the chips are out of sight and takes the way back to them.
  const toTop = $("toTop");
  if (toTop) {
    const show = () => { toTop.hidden = $("main").hidden || window.scrollY < 500; };
    window.addEventListener("scroll", show, { passive: true });
    toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: smooth() }));
  }
  async function enter(res) {
    if (res.must_change_pin) await newPin();
    $("pin").hidden = true; $("main").hidden = false;
    jumpBar();
    // Reihenfolge ist hier keine Geschmacksfrage: load() setzt PROFILE, und
    // medienLaden() füllt daraus die Auswahlfelder. Ohne das await lief beides
    // nebeneinander — mal gewann das eine, mal das andere, und die Töne
    // standen auf „eingebauter Ton", obwohl im Profil etwas anderes stand.
    await load();
    jumpBar();                                    // "Alle Einstellungen" got its heading only now
    medienLaden();
    einrichtungLaden();
    setInterval(status, 4000);
    warteschlangen(); setInterval(warteschlangen, 8000);
    usbStand(); setInterval(usbStand, 3000);
  }
  // Coming from the booth's own menu the PIN was just checked there: take
  // over that sign-in instead of asking a second time. It is used once.
  (async () => {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem("booth-admin") || "null"); sessionStorage.removeItem("booth-admin"); } catch (e) {}
    if (!saved || !saved.token || saved.must_change_pin) return;   // a factory PIN goes through the pad
    token = saved.token;
    try { await call("/api/admin/usb"); } catch (e) { token = ""; return; }
    await enter(saved);
  })();

  // ── USB stick: one switch, two buttons, one line that says how it stands ──
  const gb = (n) => (n / 1e9).toFixed(1).replace(".", ",") + " GB";
  const uhr = (t) => new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  async function usbStand() {
    let d;
    try { d = await call("/api/admin/usb"); } catch (e) { return; }
    const job = d.job, laeuft = job && job.state === "running", teile = [];
    const ziel = d.target;
    teile.push(ziel ? `Stick: ${ziel.label}${ziel.free ? " · " + gb(ziel.free) + " frei" : ""}` : "Kein USB-Stick gefunden");
    if (laeuft) teile.push(`sichert gerade Datei ${job.files_done} von ${job.files_total} – Stick stecken lassen`);
    else if (d.auto.error) teile.push("Fehler: " + d.auto.error);
    else if (d.auto.last_ok) teile.push(`zuletzt gesichert ${uhr(d.auto.last_ok)} – Stick kann abgezogen werden`);
    else teile.push("noch nichts gesichert");
    teile.push(`${d.current.runs} Durchgänge in dieser Veranstaltung, ${d.all_runs} insgesamt`);
    $("usbStand").textContent = teile.join(" · ");
    $("usbStand").style.color = d.auto.error && !laeuft ? "var(--accent)" : "";
    const fremd = ziel && ziel.trusted === false;
    $("usbFreigeben").hidden = !fremd;
    $("usbFreigeben").dataset.drive = fremd ? ziel.path : "";
    $("usbJetzt").disabled = $("usbAlle").disabled = laeuft || !ziel || fremd;
  }
  $("usbFreigeben").addEventListener("click", async () => {
    const drive = $("usbFreigeben").dataset.drive;
    if (!drive || !confirm("Diesen USB-Stick für Sicherungen freigeben? Danach landen die Bilder der Gäste darauf.")) return;
    try { await call("/api/admin/usb/trust", { method: "POST", body: JSON.stringify({ drive }) }); }
    catch (e) { $("usbStand").textContent = e.message; return; }
    usbStand();
  });
  async function usbSichern(scope) {
    try { await call("/api/admin/usb/backup", { method: "POST", body: JSON.stringify({ scope }) }); }
    catch (e) { $("usbStand").textContent = e.message; return; }
    usbStand();
  }
  $("usbJetzt").addEventListener("click", () => usbSichern("event"));
  $("usbAlle").addEventListener("click", () => usbSichern("all"));
  $("usb_enabled").addEventListener("change", async (e) => {
    try { await call("/api/admin/usb/auto", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) }); }
    catch (err) { $("usbStand").textContent = err.message; }
    usbStand();
  });

  async function status() {
    // With the token: from the network /api/state answers only an admin.
    const s = await call("/api/state");
    const rows = [
      ["Kamera", `${s.camera.backend || "–"} · ${s.camera.state}${s.camera.error ? " · " + s.camera.error : ""}`],
      ["Aufnahmen", `${s.camera.captures} · Fehler ${s.camera.errors} · Neuverbindungen ${s.camera.reconnects}`],
      ["Drucker", (s.printer.stations && s.printer.stations.length > 1 ? s.printer.stations.map((x) => `${x.slot}: ${x.state}${x.pending ? " (" + x.pending + ")" : ""}`).join(" · ") : `${s.printer.backend} · ${s.printer.state}`) + ` · gedruckt ${s.printer.printed}${s.printer.last_error ? " · " + s.printer.last_error : ""}`],
      ["Papier", vorrat(s.printer.stations || [])],
      ["Wartende Drucke", `${(s.printer.spool || {}).queued || 0} in der Reihe · ${s.printer.held || 0} warten auf Papier` + (s.printer.deferred ? ` · davon ${s.printer.deferred} vorgemerkt, als Papier fehlte` : "")],
      ["Kamera-Akku", s.camera.battery === "ac" ? "Netzteil" : (s.camera.battery === null || s.camera.battery === undefined ? "unbekannt" : `${s.camera.battery} %`)],
      ["Sync", `${s.sync.online ? "online" : "offline"} · offen ${s.sync.pending} · erledigt ${s.sync.done} · fehlgeschlagen ${s.sync.failed}`],
      ["Event", s.profile.event.name || "kein Event zugewiesen"],
      ["Profil", "Version " + s.profile.version],
      ["Software", s.version],
    ];
    $("status").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  }
  // Counted, not reported: the words say so, because the number can be off.
  function vorrat(stations) {
    const teile = stations.map((x) => {
      const v = x.sheets || {};
      const vor = stations.length > 1 ? `${x.slot}: ` : "";
      if (v.fach && v.farbband) return `${vor}Fach ${v.fach.rest} von ${v.fach.gesamt} · Farbband ${v.farbband.rest} von ${v.farbband.gesamt} (gezählt)`;
      if (v.quelle === "gezählt") return `${vor}${v.rest} von ${v.gesamt} Blatt (gezählt)`;
      if (v.quelle === "gemeldet") return `${vor}${v.prozent} % (vom Drucker gemeldet)`;
      return `${vor}unbekannt`;
    });
    return teile.length ? teile.join(" · ") : "kein Drucker";
  }
  function camFields() {
    const k = $("cam_backend").value;
    document.querySelectorAll("[data-cam]").forEach((el) => { el.hidden = !el.dataset.cam.split(" ").includes(k) && !(k === "auto" && el.dataset.cam === "edsdk"); });
  }
  $("cam_backend").addEventListener("change", camFields);

  // Die Kameraliste kommt von der Box (camera/models.py): Marke wählen, den
  // Rest setzt sie — und sagt dazu, was an dem Modell zu beachten ist.
  let KAMERAS = [];
  function camModelle() {
    const wahl = $("cam_modell");
    wahl.innerHTML = '<option value="">— auswählen —</option>' + KAMERAS.map((k) =>
      `<option value="${k.id}">${esc((k.marke ? k.marke + " " : "") + k.name)}</option>`).join("");
    wahl.onchange = () => {
      const k = KAMERAS.find((x) => x.id === wahl.value);
      if (!k) { $("cam_hinweis").textContent = ""; return; }
      $("cam_backend").value = k.backend;
      camFields();
      $("cam_hinweis").textContent = k.hinweis + (k.liveview ? "" : " Live-Bild: eingeschränkt.");
    };
  }
  let SYS_PRINTERS = [];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function addPrinter(p = {}) {
    const row = document.createElement("div");
    row.className = "printer-row";
    const kinds = [["auto","automatisch"],["windows","Windows-Treiber"],["cups","CUPS"],["ipp","IPP / AirPrint"],["hotfolder","Hotfolder"],["folder","nur Datei"]];
    row.innerHTML = `
      <div class="row">
        <label>Bezeichnung<input data-k="label" value="${esc(p.label)}" placeholder="z. B. DNP links"></label>
        <label>Anschluss<select data-k="backend">${kinds.map(([v, t]) => `<option value="${v}" ${v === (p.backend || "auto") ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      </div>
      <div class="row">
        <label data-for="name">Drucker<select data-k="name"><option value="">Standarddrucker</option>${SYS_PRINTERS.map((n) => `<option ${n === p.name ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        <label data-for="uri">Adresse<input data-k="uri" value="${esc(p.uri)}" placeholder="ipp://192.168.1.40/ipp/print"></label>
        <label data-for="folder">Ordner<input data-k="folder" value="${esc(p.folder)}" placeholder="D:\\HotFolder\\6x4"></label>
        <label>Druckt<select data-k="only"><option value="any">alle Formate</option><option value="portrait" ${p.only === "portrait" ? "selected" : ""}>nur Hochformat</option><option value="landscape" ${p.only === "landscape" ? "selected" : ""}>nur Querformat</option></select></label>
      </div>
      <div class="row">
        <label>Modell<select data-k="model">
          <option value="">unbekannt — Blattzahl selbst eintragen</option>
          ${MODELLE.map((m) => `<option value="${m.id}" ${m.id === (p.model || "") ? "selected" : ""}>${esc((m.marke ? m.marke + " " : "") + m.name)}</option>`).join("")}
        </select></label>
        <label>Papier<select data-k="medium"></select></label>
        <label>Blatt je Kassette<input type="number" data-k="sheets_total" min="0" max="5000" value="${p.sheets_total || ""}" placeholder="aus dem Modell"></label>
      </div>
      <div class="row">
        <label>Blatt im Papierfach<input type="number" data-k="tray_total" min="0" max="5000" value="${p.tray_total || ""}" placeholder="aus dem Modell"></label>
        <label>Drucke je Farbband<input type="number" data-k="ribbon_total" min="0" max="5000" value="${p.ribbon_total || ""}" placeholder="aus dem Modell"></label>
      </div>
      <p class="hint" style="margin:0 0 10px">Nur für Drucker mit Papierfach und Farbband (SELPHY: Vorgabe 18 und 36, am Gerät noch zu bestätigen). Sind beide gesetzt, zählt die Box beides getrennt und meldet, was zuerst nachgelegt werden muss. Gezählt, nicht vom Drucker gemeldet — die Zahl kann abweichen.</p>
      <div class="row">
        <label class="check"><input type="checkbox" data-k="follow_jobs" ${p.follow_jobs === false ? "" : "checked"}> Windows: Druckauftrag verfolgen, bis er die Warteschlange verlassen hat</label>
      </div>
      <p class="hint" style="margin:0 0 10px">Aus = wie früher: zählt als gedruckt, sobald Windows den Auftrag angenommen hat. Nur ausschalten, wenn Drucke fälschlich als gescheitert gemeldet werden.</p>
      <p class="hint" data-modellhinweis style="margin:0 0 10px"></p>
      <div class="row">
        <label>Helligkeit<input type="number" data-k="brightness" min="-50" max="50" step="1" value="${Number(p.brightness) || 0}"></label>
        <label>Kontrast<input type="number" data-k="contrast" min="-50" max="50" step="1" value="${Number(p.contrast) || 0}"></label>
        <label>Sättigung<input type="number" data-k="saturation" min="-50" max="50" step="1" value="${Number(p.saturation) || 0}"></label>
      </div>
      <p class="hint" style="margin:0 0 10px">Druckkorrektur nur für diesen Drucker, −50 bis +50, 0 = unverändert. Gilt ab dem nächsten Druck, nicht für die gespeicherten Bilder. Druckt er zu dunkel: Helligkeit +10, dann Kalibrierungsseite drucken.</p>
      <div class="printer-actions">
        <label class="check"><input type="checkbox" data-k="enabled" ${p.enabled === false ? "" : "checked"}> aktiv</label>
        <button type="button" data-remove>Entfernen</button>
      </div>`;
    const sync = () => {
      const k = row.querySelector('[data-k="backend"]').value;
      row.querySelector('[data-for="name"]').hidden = !["auto", "windows", "cups"].includes(k);
      row.querySelector('[data-for="uri"]').hidden = k !== "ipp";
      row.querySelector('[data-for="folder"]').hidden = k !== "hotfolder";
    };
    // Papier und Hinweis hängen am Modell: Ein SELPHY kann genau ein Format,
    // ein DS620 fünf. Was das Gerät nicht kann, soll gar nicht erst dastehen.
    const modellSync = () => {
      const m = MODELLE.find((x) => x.id === row.querySelector('[data-k="model"]').value);
      const wahl = row.querySelector('[data-k="medium"]');
      const vorher = p.medium || wahl.value;
      wahl.innerHTML = (m ? m.medien : []).map((med) =>
        `<option value="${med.id}" ${med.id === vorher ? "selected" : ""}>${esc(med.name)}${med.sheets ? ` · ${med.sheets} Blatt` : ""}</option>`).join("")
        || '<option value="">—</option>';
      const hinweis = row.querySelector("[data-modellhinweis]");
      hinweis.textContent = m
        ? (m.hinweis || "") + (m.meldet === "supply" ? " Dieser Drucker meldet seinen Vorrat selbst."
            : m.meldet === "status" ? " Meldet Zustand und Papierfehler, aber keine Blattzahl — die zählt die Box mit."
            : " Meldet nichts — die Box zählt mit, was sie gedruckt hat.")
          + (m.profil ? ` Druckprofil: ${m.profil.text}. Vorlagen-Formate: ${(m.profil.papiere || []).join(", ") || "keine"}.` : "")
        : "Ohne Modell zählt die Box nur, was du bei „Blatt je Kassette“ einträgst. Druckprofil wie SELPHY (10×15, randlos).";
    };
    row.querySelector('[data-k="model"]').onchange = modellSync;
    modellSync();
    row.querySelector('[data-k="backend"]').onchange = sync;
    row.querySelector("[data-remove]").onclick = () => row.remove();
    sync();
    $("printers").appendChild(row);
  }
  // Auf Zuruf nachsehen, was dranhängt. Die Box füllt dabei nur Lücken —
  // eingetragene Werte sind Entscheidungen und bleiben stehen.
  async function druckerErkennen() {
    const hinweis = $("detectHint");
    hinweis.textContent = "Suche läuft …";
    try {
      const d = await call("/api/admin/printers/detect", { method: "POST" });
      const gefunden = (d.printer && d.printer.drucker) || [];
      const modellName = (id) => {
        const m = MODELLE.find((x) => x.id === id);
        return m ? ((m.marke ? m.marke + " " : "") + m.name) : "";
      };
      const p = d.printer || {};
      const teile = [];
      if (gefunden.length) teile.push(`Gefunden: ${gefunden.join(", ")}`);
      if (p.model) teile.push(`erkannt als ${modellName(p.model)}`);
      else if (p.name) teile.push("Modell nicht erkannt — bitte von Hand wählen");
      if (p.medium) teile.push(`Papier: ${p.medium}`);
      else if (p.papier) teile.push(`Papier laut Treiber: ${p.papier}`);
      hinweis.textContent = teile.join(" · ") || "Kein Drucker gefunden.";
      // Die Anzeige neu aufbauen, damit das Erkannte auch im Formular steht.
      if (d.local) await load();          // damit das Erkannte auch im Formular steht
    } catch (e) {
      hinweis.textContent = "Suche fehlgeschlagen: " + (e && e.message ? e.message : e);
    }
  }

  // Die Modellkunde kommt von der Box selbst (printing/models.py) — eine Liste
  // im Browser wäre die zweite Wahrheit.
  let MODELLE = [];
  function readPrinters() {
    return [...document.querySelectorAll(".printer-row")].map((row) => {
      const o = {};
      row.querySelectorAll("[data-k]").forEach((el) => { o[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value.trim(); });
      return o;
    });
  }
  $("addPrinter").onclick = () => addPrinter({ backend: "windows" });
  $("detectPrinter").onclick = druckerErkennen;
  // ── Einrichtung & Test ──────────────────────────────────
  const AMPEL = { ok: "●", fehlt: "●", unklar: "●" };
  const AMPELFARBE = { ok: "#2e9e5b", fehlt: "#d04545", unklar: "#c98a14" };
  const punkt = (z) => '<span style="color:' + (AMPELFARBE[z] || "#888") + '">' + (AMPEL[z] || "●") + "</span>";

  async function einrichtungLaden() {
    let d;
    try { d = await call("/api/admin/einrichtung"); } catch (e) { return; }
    $("einrichtungStand").innerHTML =
      "<dt>Gesamt</dt><dd>" + punkt(d.gesamt) + " " + (
        d.gesamt === "ok" ? "Bereit." :
        d.gesamt === "fehlt" ? "Es fehlt noch etwas." :
        "Läuft vermutlich — aber nicht nachgewiesen.") + "</dd>"
      + "<dt>Kamera</dt><dd>" + punkt(d.kamera.stand) + " <b>" + esc(d.kamera.weg_name) + "</b>"
        + (d.kamera.automatisch ? ' <span class="hint">(automatisch gewählt)</span>' : "")
        + "<br>" + esc(d.kamera.text) + "</dd>"
      + "<dt>Drucker</dt><dd>" + punkt(d.drucker.stand) + " " + esc(d.drucker.text) + "</dd>"
      + "<dt>System</dt><dd>" + esc(d.system) + " · Python " + esc(d.python) + "</dd>";
    $("einrichtungOffen").innerHTML = d.offen.length
      ? "<p><b>Nächster Handgriff:</b></p><ul>" + d.offen.map((t) => "<li>" + esc(t) + "</li>").join("") + "</ul>"
      : "";
    // Nur zeigen, was hier auch gebraucht wird.
    $("edsdkBlock").hidden = !(d.kamera.weg === "edsdk" && d.kamera.stand !== "ok");
    $("linuxBlock").hidden = d.kamera.weg !== "gphoto2";
    if (d.kamera.weg === "gphoto2") {
      $("linuxText").textContent = d.kamera.text;
      $("linuxFrei").disabled = !(d.kamera.blockierer || []).length;
    }
    linuxEinstellungen();
  }

  async function linuxEinstellungen() {
    let d;
    try { d = await call("/api/admin/kamera/linux"); } catch (e) { return; }
    $("kameraEinstellungen").innerHTML = Object.entries(d.einstellungen || {})
      .map(([marke, text]) => "<dt>" + esc(marke[0].toUpperCase() + marke.slice(1)) + "</dt><dd>"
           + esc(text) + "</dd>").join("");
  }

  $("linuxFrei").onclick = async () => {
    $("linuxFrei").disabled = true;
    $("linuxNote").textContent = "Wird freigegeben …";
    try {
      const r = await call("/api/admin/kamera/linux/freigeben", { method: "POST" });
      $("linuxNote").textContent = (r.ok ? "Freigegeben. " : "Es hängt noch etwas: " + r.noch_da.join(", ") + ". ")
        + r.hinweis;
      einrichtungLaden();
    } catch (e) { $("linuxNote").textContent = "Nicht freigegeben: " + e.message; }
  };

  $("edsdkUpload").onclick = async () => {
    const f = $("edsdkDatei").files[0];
    if (!f) { $("edsdkLog").textContent = "Erst das ZIP von Canon auswählen."; return; }
    $("edsdkUpload").disabled = true;
    $("edsdkLog").textContent = "Wird ausgepackt und geprüft …";
    try {
      const fd = new FormData();
      fd.append("datei", f);
      const r = await call("/api/admin/einrichtung/edsdk", { method: "POST", body: fd });
      $("edsdkLog").textContent = (r.ok ? "Eingerichtet: " + r.pfad + "\n" : (r.hinweis + "\n"))
        + (r.zeilen || []).join("\n");
      einrichtungLaden();
    } catch (e) { $("edsdkLog").textContent = "Nicht eingerichtet: " + e.message; }
    $("edsdkUpload").disabled = false;
  };

  // ── Medien & Töne ───────────────────────────────────────
  const MB = (n) => (n / 1048576).toFixed(n < 1048576 ? 2 : 1) + " MB";
  const ARTZEICHEN = { bild: "Bild", video: "Video", ton: "Ton" };

  async function medienLaden() {
    let d;
    try { d = await call("/api/admin/assets"); } catch (e) { return; }
    MEDIEN = d.assets;
    STARTBILDER = d.start_builtins || [];
    medienFelderFuellen();
    const ziel = $("medienListe");
    if (!d.assets.length) {
      ziel.innerHTML = '<p class="hint">Noch nichts eingelagert.</p>';
      return;
    }
    ziel.innerHTML = d.assets.map((a) => {
      // Was in Gebrauch ist, sagt das — und lässt sich nicht löschen.
      const benutzt = a.verwendet_in.length
        ? '<span class="hint">' + a.verwendet_in.map(esc).join(" · ") + "</span>"
        : '<span class="hint">wird nirgends verwendet</span>';
      const knopf = a.verwendet_in.length
        ? '<button class="btn-secondary" disabled title="Erst dort abwählen">In Gebrauch</button>'
        : '<button class="btn-secondary" data-loesch="' + encodeURIComponent(a.name) + '">Löschen</button>';
      const vorschau = a.art === "bild"
        ? '<img src="/assets/' + encodeURIComponent(a.name) + '" alt="" style="height:44px;width:44px;object-fit:cover;border-radius:8px">'
        : a.art === "ton"
          ? '<audio controls preload="none" src="/assets/' + encodeURIComponent(a.name) + '" style="height:36px"></audio>'
          : "";
      return '<div class="row" style="align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid rgba(128,128,128,.2)">'
        + vorschau
        + '<div style="flex:1;min-width:0"><b>' + esc(a.name) + "</b> <span class=\"hint\">"
        + ARTZEICHEN[a.art] + " · " + MB(a.bytes) + "</span><br>" + benutzt + "</div>"
        + knopf + "</div>";
    }).join("");
    ziel.querySelectorAll("[data-loesch]").forEach((btn) => {
      btn.onclick = async () => {
        const name = decodeURIComponent(btn.dataset.loesch);
        btn.disabled = true;
        try {
          await call("/api/admin/assets/" + encodeURIComponent(name), { method: "DELETE" });
          $("medienNote").textContent = name + " gelöscht.";
          medienLaden();
        } catch (e) {
          $("medienNote").textContent = "Nicht gelöscht: " + e.message;
          btn.disabled = false;
        }
      };
    });
  }

  // ── Queues ───────────────────────────────────────────
  // Prints that are not out yet and uploads that gave up, each with time,
  // state and reason. Retry is one tap; discarding asks once more on the same
  // button (it turns red for six seconds), so a slipped finger costs nothing.
  const Q_STATE = { queued: "wartet auf einen Drucker", printing: "druckt gerade", held: "angehalten" };
  const zeit = (t) => t ? new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";
  function scharf(btn, label, tun) {
    if (btn.dataset.armed === "1") { btn.dataset.armed = ""; return tun(); }
    const vorher = btn.textContent;
    btn.dataset.armed = "1"; btn.textContent = label;
    setTimeout(() => { if (btn.dataset.armed === "1") { btn.dataset.armed = ""; btn.textContent = vorher; } }, 6000);
  }
  async function qAktion(pfad, body) {
    try { const r = await call(pfad, { method: "POST", body: JSON.stringify(body) }); $("qNote").textContent = r.result || "Erledigt."; }
    catch (e) { $("qNote").textContent = "Nicht erledigt: " + e.message; }
    warteschlangen();
  }
  function qZeile(titel, unter, grund, knoepfe) {
    const row = document.createElement("div"); row.className = "queue-row";
    const text = document.createElement("div");
    const b = document.createElement("b"); b.textContent = titel;
    const sm = document.createElement("small"); sm.textContent = unter;
    text.append(b, sm);
    if (grund) { const w = document.createElement("small"); w.className = "why"; w.textContent = grund; text.appendChild(w); }
    const box = document.createElement("div"); box.className = "btns";
    for (const [label, cls, fn] of knoepfe) {
      const k = document.createElement("button"); k.type = "button"; k.className = cls; k.textContent = label;
      k.addEventListener("click", () => fn(k));
      box.appendChild(k);
    }
    row.append(text, box);
    return row;
  }
  const ZIEL_NAME = { gallery: "Galerie", picdrop: "Zweites Ziel", "zweites-ziel": "Zweites Ziel", folder: "Zweite Ablage", usb: "USB-Stick" };
  async function warteschlangen() {
    let d;
    try { d = await call("/api/admin/queues"); } catch (e) { return; }
    const drucke = d.prints || [], uploads = d.failed_uploads || [];
    $("qPrintCount").textContent = drucke.length ? "· " + drucke.length : "· keine";
    $("qPrints").replaceChildren(...drucke.map((j) => qZeile(
      "Druck " + (j.run || "#" + j.id) + " · " + j.copies + "×",
      zeit(j.created) + " · " + (Q_STATE[j.state] || j.state) + (j.slot ? " · Drucker " + j.slot : ""),
      j.reason,
      [["Verwerfen", "btn-secondary", (k) => scharf(k, "Wirklich verwerfen?", () =>
        qAktion("/api/admin/queues/prints/discard", { ids: [j.id] }))]])));
    $("qPrintsAll").hidden = drucke.length < 2;
    const gruende = (d.failed_reasons || []).slice(0, 3).map((g) => g.count + "× " + g.reason).join(", ");
    $("qUploadCount").textContent = uploads.length ? "· " + uploads.length + (gruende ? " — " + gruende : "") : "· keine";
    // Why, in plain words (photobox_sync/reasons.py); the raw error only on
    // hover, for whoever reads logs.
    $("qUploads").replaceChildren(...uploads.map((f) => {
      const row = qZeile(
        f.file, zeit(f.created) + " · Ziel " + f.target + (f.attempts ? " · " + f.attempts + " Versuche" : ""),
        f.reason ? "Grund: " + f.reason : f.error,
        [["Erneut", "btn-secondary", () => qAktion("/api/admin/queues/uploads/retry", { ids: [f.id] })],
         ["Verwerfen", "btn-secondary", (k) => scharf(k, "Wirklich verwerfen?", () =>
           qAktion("/api/admin/queues/uploads/discard", { ids: [f.id] }))]]);
      if (f.error) row.title = f.error;
      return row;
    }));
    $("qUploadsAll").hidden = uploads.length < 2;
    $("qUploadsRetry").hidden = uploads.length < 2;
    // Sent / waiting / failed per target, as the queue remembers it (30 days).
    const ziele = Object.entries(d.targets || {});
    $("qTargets").replaceChildren(...(ziele.length ? ziele.map(([name, z]) => {
      const p = document.createElement("div");
      p.textContent = (ZIEL_NAME[name] || name) + ": " + (z.done || 0) + " gesendet · " + (z.pending || 0)
        + " offen · " + (z.failed || 0) + " fehlgeschlagen";
      return p;
    }) : [document.createTextNode("Noch nichts in der Warteschlange.")]));
  }
  $("qPrintsAll").addEventListener("click", (e) => scharf(e.currentTarget, "Wirklich alle Drucke verwerfen?",
    () => qAktion("/api/admin/queues/prints/discard", { confirm: "all" })));
  $("qUploadsAll").addEventListener("click", (e) => scharf(e.currentTarget, "Wirklich alle verwerfen?",
    () => qAktion("/api/admin/queues/uploads/discard", { confirm: "all" })));
  $("qUploadsRetry").addEventListener("click", () => qAktion("/api/admin/queues/uploads/retry", {}));

  // Was in der Ablage liegt — gemerkt, damit die Auswahlfelder es kennen.
  let MEDIEN = [];
  let STARTBILDER = [];
  // The start pictures as tiles: built-in backgrounds first, then the booth's
  // own pictures and videos. A tap selects or deselects; the order of the taps
  // is the order they take turns in. A chosen file that is missing stays as a
  // tile marked "fehlt", so saving does not drop it silently.
  let startWahl = [];
  const startUrl = (n) => {
    const b = STARTBILDER.find((x) => x.name === n);
    return b ? b.url : "/assets/" + encodeURIComponent(n);
  };
  function startKacheln() {
    const eigene = MEDIEN.filter((m) => m.art === "bild" || m.art === "video").map((m) => ({ name: m.name, label: m.name, art: m.art }));
    const alle = [...STARTBILDER.map((b) => ({ ...b, art: "bild" })), ...eigene];
    for (const n of startWahl) if (!alle.some((a) => a.name === n)) alle.push({ name: n, label: n + " — fehlt", art: "fehlt" });
    const box = $("startTiles");
    box.replaceChildren(...alle.map((a) => {
      const t = document.createElement("button");
      t.type = "button"; t.className = "start-tile"; t.dataset.name = a.name;
      t.setAttribute("aria-pressed", String(startWahl.includes(a.name)));
      if (a.art === "video") {
        const v = document.createElement("video");
        v.src = startUrl(a.name); v.muted = true; v.preload = "metadata";
        t.appendChild(v);
      } else if (a.art !== "fehlt") t.style.backgroundImage = 'url("' + startUrl(a.name).replace(/"/g, "%22") + '")';
      const b = document.createElement("b"); b.textContent = a.label;
      t.appendChild(b);
      return t;
    }));
    startVorschau();
  }
  function startVorschau() {
    const p = $("startPreview"), erstes = startWahl[0];
    p.dataset.fit = $("start_fit").value;
    p.dataset.dim = $("start_dim").value;
    p.querySelector("i").style.opacity = $("start_dim").value === "-1" ? "" : String((+$("start_dim").value || 0) / 100);
    p.style.backgroundImage = erstes && !/\.(mp4|webm)$/i.test(erstes) ? 'url("' + startUrl(erstes).replace(/"/g, "%22") + '")' : "";
    p.querySelector("span").textContent = !erstes ? "Kein Startbild: unscharfes Live-Bild"
      : startWahl.length > 1 ? "Vorschau · " + startWahl.length + " Bilder wechseln sich ab" : "Vorschau";
  }
  $("startTiles").addEventListener("click", (e) => {
    const t = e.target.closest(".start-tile");
    if (!t) return;
    const n = t.dataset.name;
    startWahl = startWahl.includes(n) ? startWahl.filter((x) => x !== n) : [...startWahl, n];
    startKacheln();
  });
  $("start_fit").addEventListener("change", startVorschau);
  $("start_dim").addEventListener("change", startVorschau);
  $("startKeins").addEventListener("click", () => { startWahl = []; startKacheln(); });

  function medienOptionen(select, arten, aktiv, leerText) {
    // Ein eingestellter Name, dessen Datei fehlt, MUSS als Auswahl erhalten
    // bleiben. Sonst fällt er beim Speichern still unter den Tisch, und der
    // Betreiber sucht abends, warum sein Ton weg ist.
    const vorhanden = MEDIEN.filter((m) => arten.includes(m.art)).map((m) => m.name);
    const fehlend = aktiv.filter((n) => n && !vorhanden.includes(n));
    const zeile = (n, fehlt) => '<option value="' + esc(n) + '"'
      + (aktiv.includes(n) ? " selected" : "") + ">" + esc(n) + (fehlt ? " — fehlt" : "") + "</option>";
    select.innerHTML = (leerText ? '<option value=""' + (aktiv.length ? "" : " selected") + ">" + leerText + "</option>" : "")
      + vorhanden.map((n) => zeile(n, false)).join("")
      + fehlend.map((n) => zeile(n, true)).join("");
  }

  function medienFelderFuellen() {
    const snd = PROFILE.sounds || {}, start = PROFILE.start || {};
    for (const k of ["countdown", "shutter", "done", "print"]) {
      medienOptionen($("snd_" + k), ["ton"], snd[k] ? [snd[k]] : [], "eingebauter Ton");
    }
    $("snd_enabled").value = String(snd.enabled !== false);
    $("snd_volume").value = snd.volume ?? 0.8;
    startWahl = (start.media || []).map(String);
    $("start_fit").value = ["drift", "cover", "contain"].includes(start.fit) ? start.fit : "drift";
    const dim = Number(start.dim ?? -1);
    $("start_dim").value = [...$("start_dim").options].some((o) => +o.value === dim) ? String(dim) : "-1";
    $("start_seconds").value = start.media_seconds || 8;
    startKacheln();
  }

  $("medienSpeichern").onclick = async () => {
    const body = {
      sounds: { ...(PROFILE.sounds || {}),
                enabled: $("snd_enabled").value === "true",
                volume: Math.min(1, Math.max(0, +$("snd_volume").value || 0.8)),
                countdown: $("snd_countdown").value, shutter: $("snd_shutter").value,
                done: $("snd_done").value, print: $("snd_print").value },
      start: { ...(PROFILE.start || {}), media: startWahl.slice(),
               fit: $("start_fit").value, dim: +$("start_dim").value,
               media_seconds: +$("start_seconds").value || 8 },
    };
    $("medienSpeichern").disabled = true;
    try {
      const r = await call("/api/admin/profile", { method: "POST", body: JSON.stringify(body) });
      PROFILE.sounds = body.sounds; PROFILE.start = body.start;
      $("medienNote").textContent = r.server_wins
        ? "Gespeichert — der Server überschreibt es beim nächsten Sync."
        : "Gespeichert.";
      medienLaden();
    } catch (e) { $("medienNote").textContent = "Nicht gespeichert: " + e.message; }
    $("medienSpeichern").disabled = false;
  };

  $("medienUpload").onclick = async () => {
    const f = $("medienDatei").files[0];
    if (!f) { $("medienNote").textContent = "Erst eine Datei auswählen."; return; }
    $("medienUpload").disabled = true;
    $("medienNote").textContent = "Wird eingelagert …";
    try {
      const fd = new FormData();
      fd.append("datei", f);
      const r = await call("/api/admin/assets", { method: "POST", body: fd });
      $("medienDatei").value = "";
      $("medienNote").textContent = "Eingelagert als " + r.name
        + (r.name !== f.name ? " (Name entschärft)" : "") + ".";
      medienLaden();
    } catch (e) { $("medienNote").textContent = "Nicht eingelagert: " + e.message; }
    $("medienUpload").disabled = false;
  };
  // ── Event an der Box ────────────────────────────────────
  // Der Abend darf nicht daran scheitern, dass jemand vorher im Web-Backend
  // etwas anlegen musste. Deshalb steht das hier — und es geht auch offline.
  async function loadEvents() {
    let d;
    try { d = await call("/api/admin/events"); } catch (e) { return; }
    const jetzt = d.current || {};
    $("eventNow").innerHTML = [
      ["Aktuell", esc(jetzt.name || "kein Event") + (jetzt.slug ? ` <code>${esc(jetzt.slug)}</code>` : "")],
      ["Galerie", d.online ? "erreichbar" : "nicht erreichbar — es geht trotzdem weiter"],
      ...(d.claim && d.claim.name ? [["Nachzutragen", esc(d.claim.name) + " — wird beim nächsten Sync gemeldet"]] : []),
    ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
    $("eventList").innerHTML = (d.events || [])
      .map((e) => `<button class="btn-secondary" data-event="${esc(e.slug)}" ${e.current ? "disabled" : ""}
             style="min-height:64px;font-size:19px;padding:0 22px">${esc(e.name)}${e.current ? " ✓" : ""}</button>`)
      .join("") || (d.online ? "" : "");
    $("eventList").querySelectorAll("[data-event]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        try { await call("/api/admin/events/select", { method: "POST", body: JSON.stringify({ slug: b.dataset.event }) }); $("eventNote").textContent = "Event gewechselt."; }
        catch (e) { $("eventNote").textContent = "Wechsel nicht möglich: " + e.message; }
        loadEvents();
      };
    });
  }
  // ── Kopplung mit der Galerie ────────────────────────────
  // The code goes to the booth once and is cleared from the field; the key
  // never comes back to this page. No restart: the sync reads the new
  // address and key at its next round.
  function showPairing(p) {
    p = p || {};
    const ev = p.event || {};
    const rows = p.paired ? [
      ["Gekoppelt mit", (p.gallery_name ? p.gallery_name + " – " : "") + (p.url || "")],
      ["Name der Box in der Galerie", p.device_name || "nicht bekannt (Schlüssel von Hand eingetragen)"],
      ["Verbindung", p.online ? "steht" : "noch kein Kontakt – die Box versucht es weiter"],
      ["Event", ev.slug ? `${ev.name || ev.slug}` : "keins zugewiesen"],
      ["QR-Code zeigt auf", ev.slug && ev.base_url ? ev.base_url : "– (kein QR-Code möglich)"],
    ] : [["Gekoppelt", "noch nicht – Kopplungscode eintragen"]];
    $("pairStand").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")
      + (p.warnings || []).map((w) => `<dt><i class="dot warn"></i>Achtung</dt><dd>${esc(w)}</dd>`).join("")
      + (p.notes || []).map((w) => `<dt><i class="dot skip"></i>Hinweis</dt><dd>${esc(w)}</dd>`).join("");
  }
  $("pairBtn").onclick = async () => {
    const code = $("pair_code").value.trim();
    if (!code) { $("pairNote").textContent = "Bitte den Kopplungscode aus der Galerie eintragen."; return; }
    $("pairBtn").disabled = true;
    $("pairNote").textContent = "Wird gekoppelt …";
    try {
      const r = await call("/api/admin/pair", { method: "POST", body: JSON.stringify({ code }) });
      $("pair_code").value = "";
      const p = r.pairing || {};
      // Keep the hidden fields in step, or a later "Speichern" would write the old address back.
      $("server_url").value = p.url || "";
      $("server_key").value = ""; $("server_key").placeholder = "über den Kopplungscode gesetzt";
      showPairing(p);
      $("pairNote").textContent = "Gekoppelt. Die Box meldet sich in den nächsten Sekunden bei der Galerie und holt "
        + "ihr Profil – ein Neustart ist nicht nötig." + ((p.warnings || []).length ? " Bitte die Punkte oben beachten." : "");
      setTimeout(async () => { try { showPairing((await call("/api/admin/config")).pairing); } catch (e) {} }, 8000);
    } catch (e) { $("pairNote").textContent = "Nicht gekoppelt: " + e.message; }
    $("pairBtn").disabled = false;
  };
  // ── Kopplung mit dem Handy ──────────────────────────────
  // The booth opens a request at the gallery and shows its QR code; this page
  // asks the booth every 2.5 s whether the phone confirmed it (longer after
  // a failed look), and stops at expiry or "Abbrechen".
  let phoneTimer = null, phoneUntil = 0, phoneWait = 2500;
  function phoneStop(msg) {
    clearTimeout(phoneTimer); phoneTimer = null;
    $("phoneBox").hidden = true; $("phoneCancel").hidden = true; $("phoneBtn").disabled = false;
    if (msg !== undefined) $("phoneNote").textContent = msg;
  }
  async function phonePoll() {
    let r;
    try { r = await call("/api/admin/pair/phone"); phoneWait = 2500; }
    catch (e) { phoneWait = Math.min(phoneWait * 2, 15000); r = { state: "open", offline: true }; }
    if (r.state === "paired") {
      phoneStop("Gekoppelt. Die Box meldet sich jetzt bei der Galerie – ein Neustart ist nicht nötig.");
      const p = r.pairing || {};
      $("server_url").value = p.url || ""; $("server_key").value = "";
      $("server_key").placeholder = "über das Handy gesetzt";
      $("phoneUrlRow").hidden = true;
      return showPairing(p);
    }
    const ende = {
      declined: "In der Galerie abgelehnt. Mit „Mit dem Handy koppeln“ neu beginnen.",
      expired: "Abgelaufen – die Anfrage gilt fünf Minuten. Mit „Mit dem Handy koppeln“ neu beginnen.",
      unknown: "Die Galerie kennt die Anfrage nicht mehr. Bitte neu beginnen.",
      collected: "Diese Anfrage wurde schon abgeholt. Bitte neu beginnen.",
      none: "",
    };
    if (r.state in ende) return phoneStop(ende[r.state]);
    const left = Math.max(0, Math.round((phoneUntil - Date.now()) / 1000));
    $("phoneNote").textContent = (r.offline ? "Galerie gerade nicht erreichbar – die Box versucht es weiter. " : "")
      + `Wartet auf die Bestätigung am Handy, noch ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} Minuten.`;
    if (left <= 0) return phoneStop(ende.expired);
    phoneTimer = setTimeout(phonePoll, phoneWait);
  }
  $("phoneBtn").onclick = async () => {
    $("phoneBtn").disabled = true;
    $("phoneNote").textContent = "Anfrage an die Galerie …";
    try {
      const r = await call("/api/admin/pair/phone", { method: "POST", body: JSON.stringify({ url: $("phone_url").value.trim() }) });
      $("phoneQr").src = r.qr; $("phoneCode").textContent = r.code;
      $("phoneBox").hidden = false; $("phoneCancel").hidden = false;
      phoneUntil = Date.now() + (r.seconds || 600) * 1000; phoneWait = 2500;
      $("phoneNote").textContent = "Jetzt den QR-Code mit dem Handy scannen.";
      clearTimeout(phoneTimer); phoneTimer = setTimeout(phonePoll, phoneWait);
    } catch (e) { phoneStop("Nicht gestartet: " + e.message); }
  };
  $("phoneCancel").onclick = async () => {
    await call("/api/admin/pair/phone/cancel", { method: "POST" }).catch(() => {});
    phoneStop("Abgebrochen.");
  };
  // ── Testbilder entfernen ────────────────────────────────
  // The server checks everything again: the number must still be right, a
  // guest run must not be in progress, and runs of an event (or more than a
  // handful) need the number typed — one careless tap must not clear a party.
  let TESTBILDER = null;
  async function loadTestbilder() {
    try { TESTBILDER = await call("/api/admin/testbilder"); } catch (e) { return; }
    const t = TESTBILDER;
    $("testbilderStand").innerHTML = [
      ["Auf der Box", `${t.anzahl} Aufnahme(n), davon ${t.heute} von heute`],
      ["Ohne Event", String(t.ohne_event)],
      ["Im Event", t.event ? `${t.im_event} (${t.event})` : String(t.im_event)],
      ["Schon in der Galerie", `${t.in_galerie} — bleiben dort`],
      ...(t.andere_events ? [["Frühere Events", `${t.andere_events} — bleiben auf der Box`]] : []),
      ...(t.laeuft ? [["Gerade", "Ein Gast ist mitten in einer Aufnahme — bitte warten"]] : []),
    ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    $("testbilderBtn").disabled = !t.anzahl || t.laeuft;
  }
  $("testbilderBtn").onclick = async () => {
    await loadTestbilder();
    const t = TESTBILDER;
    if (!t || !t.anzahl) { $("testbilderNote").textContent = "Keine Aufnahmen auf der Box."; return; }
    const frage = `${t.anzahl} Aufnahme(n) von dieser Box entfernen? Was schon in der Galerie ist, bleibt dort.`;
    let getippt = "";
    if (t.tippen) {
      getippt = window.prompt(`${frage}\n\nZur Bestätigung bitte die Zahl ${t.anzahl} eintippen:`, "");
      if (getippt === null) return;
      if (getippt.trim() !== String(t.anzahl)) { $("testbilderNote").textContent = "Die Zahl stimmt nicht — nichts entfernt."; return; }
    } else if (!window.confirm(frage)) return;
    $("testbilderBtn").disabled = true;
    try {
      const r = await call("/api/admin/testbilder/entfernen", { method: "POST", body: JSON.stringify({
        anzahl: t.anzahl, getippt, zaehler: $("testbilderZaehler").checked, papier: $("testbilderPapier").checked }) });
      $("testbilderNote").textContent = `Entfernt: ${r.entfernt} Aufnahme(n), ${r.uploads_verworfen} wartende Uploads, `
        + `${r.drucke_verworfen} wartende Drucke. Zähler ${r.zaehler ? "auf null" : "unverändert"}, `
        + `Papier/Farbband ${r.papier ? "als voll gezählt" : "unverändert"}.`
        + (r.in_galerie ? ` ${r.in_galerie} Aufnahme(n) sind schon in der Galerie und bleiben dort.` : "");
    } catch (e) { $("testbilderNote").textContent = "Nicht entfernt: " + e.message; }
    loadTestbilder();
  };
  $("eventNew").onclick = async () => {
    const name = $("eventName").value.trim();
    if (name.length < 2) { $("eventNote").textContent = "Das Event braucht einen Namen."; return; }
    $("eventNew").disabled = true;
    try {
      const galerie = $("eventGalerie").value;
      const r = await call("/api/admin/events", { method: "POST", body: JSON.stringify({ name, galerie }) });
      $("eventName").value = "";
      $("eventNote").textContent = r.online
        ? ("Angelegt und in der Galerie eingetragen. " + (galerie === "gemeinsam"
            ? "Alle Gäste sehen alle Aufnahmen." : "Jeder Gast sieht nur seinen eigenen Durchgang."))
        : "Angelegt. Die Galerie ist nicht erreichbar — die Box trägt es nach, sobald wieder Netz da ist.";
    } catch (e) { $("eventNote").textContent = "Nicht angelegt: " + e.message; }
    $("eventNew").disabled = false;
    loadEvents();
  };
  async function load() {
    const d = await call("/api/admin/config");
    const l = d.local;
    $("server_url").value = l.server.url; $("server_key").placeholder = l.server.api_key || "noch nicht gesetzt";
    showPairing(d.pairing);
    $("phoneUrlRow").hidden = !!l.server.url;
    $("booth_name").value = l.booth_name; $("cam_backend").value = l.camera.backend; $("cam_rotate").value = l.camera.rotate;
    $("rc_allowed").checked = !!(l.remote_control || {}).allowed; $("rc_indicator").checked = (l.remote_control || {}).indicator !== false;
    $("gs_allowed").checked = !!(l.guest_share || {}).allowed; $("gs_address").value = (l.guest_share || {}).address || ""; $("gs_port").value = (l.guest_share || {}).port || 8766;
    $("cam_ccapi").value = l.camera.ccapi_url || "";
    $("cam_ccapi_lv").value = l.camera.ccapi_liveview || "medium";
    $("cam_ccapi_disp").value = l.camera.ccapi_display || "on";
    $("cam_dll").value = l.camera.edsdk_dll; $("cam_dcc").value = l.camera.digicam_url || ""; $("cam_folder").value = l.camera.folder || "";
    $("cam_pwc").value = l.camera.preview_webcam ?? ""; $("cam_trig").value = l.camera.trigger_url || ""; camFields(); $("cam_index").value = l.camera.webcam_index; $("cam_mirror").value = String(l.camera.mirror_preview);
    // Masked and empty: the PIN never comes into the page; empty keeps it (admin_config_save).
    $("admin_pin").value = "";
    $("admin_pin").placeholder = l.admin_pin_set ? "gesetzt – leer lassen = unverändert" : "leer lassen = unverändert";
    SYS_PRINTERS = d.printers || [];
    MODELLE = d.printer_models || [];
    KAMERAS = d.camera_models || [];
    camModelle();
    const list = (l.printers && l.printers.length) ? l.printers : [l.printer, ...((l.printer2 || {}).backend && l.printer2.backend !== "none" ? [l.printer2] : [])];
    $("printers").innerHTML = ""; list.forEach(addPrinter);
    $("pr_routing").value = l.print_routing === "orientation" ? "balance" : (l.print_routing || "balance");
    // The one target of the form is taken out of the list the JSON box shows:
    // two places for the same thing would overwrite each other on save.
    const alleZiele = l.targets || [];
    const zweites = alleZiele.find((t) => t.name === "picdrop" || t.name === "zweites-ziel") || null;
    $("targets").value = JSON.stringify(alleZiele.filter((t) => t !== zweites), null, 2);
    $("zt_on").checked = !!zweites && zweites.enabled !== false;
    $("zt_type").value = (zweites && zweites.type) || "ftps";
    $("zt_host").value = (zweites && zweites.host) || "";
    $("zt_port").value = (zweites && zweites.port) || "";
    $("zt_user").value = (zweites && zweites.user) || "";
    $("zt_pass").value = "";
    // Rule B2: a changed server lost its password and waits for a new one.
    const wartet = !!(zweites && zweites.awaiting_password);
    $("zt_pass").placeholder = wartet ? "Bitte das Passwort für den neuen Server eintragen"
      : (zweites && zweites.password ? "gespeichert – leer lassen, um es zu behalten" : "");
    $("zt_pass").dataset.stored = zweites && zweites.password && !wartet ? "1" : "";
    $("zt_path").value = (zweites && zweites.path) || "";
    $("zt_flat").checked = !zweites || zweites.flat !== false;
    $("zt_originals").checked = !zweites || zweites.originals !== false;
    $("zt_prints").checked = !!(zweites && zweites.print_sheets);
    if (wartet) $("zt_result").textContent = "Wartet auf das Passwort: Server, Port, Art oder Benutzer wurden geändert. "
      + "Bis das Passwort für den neuen Server eingetragen ist, bleiben die Dateien in der Warteschlange.";
    ftpWarnung(); serverSchluessel(); ordnerBeispiel();
    const st = l.storage || {}, usb = st.usb || {};
    $("st_folder").value = st.folder || ""; $("st_layout").value = st.layout || "event/date";
    $("st_originals").value = String(st.originals !== false);
    $("usb_enabled").checked = !!usb.enabled; $("usb_path").value = usb.path || "";
    $("usb_originals").value = String(usb.originals !== false);
    $("standalone").checked = !!l.standalone;
    fillProfile(d);
    PhotoboxSettings.mount($("alleEinstellungen"), call);
    const a = d.about || {};
    $("about").innerHTML = [
      ["Programm", `${esc(a.name || "Photobox Booth")} ${esc(a.version || "")}${a.commit ? " · <code>" + esc(a.commit) + "</code>" : ""}`],
      ["Projekt", `<a href="${esc(a.project_url)}" target="_blank" rel="noopener">${esc((a.project_url || "").replace("https://", ""))}</a>`],
      ["Quelltext", `<a href="${esc(a.repo_url)}" target="_blank" rel="noopener">${esc((a.repo_url || "").replace("https://", ""))}</a> · ${esc(a.license || "")}`],
      ["Python", esc(a.python || "–")],
      ["System", esc(a.platform || "–")],
      ["Datenordner", `<code>${esc(a.data_dir || d.data_dir || "")}</code>`],
    ].filter(([k, v]) => v).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
    loadLegal();
    $("log").textContent = (d.log_tail || []).join("\n");
    loadEvents();
    loadTestbilder();
    status();
  }

  // Publisher and third-party notices: written into the .exe by the Windows
  // build (static/legal/, see packaging/windows/build.ps1). A run from the
  // source has neither, and the card stays as it is.
  async function loadLegal() {
    const box = $("legal");
    if (!box || box.dataset.done) return;
    box.dataset.done = "1";
    let pub = null;
    try { const r = await fetch("/static/legal/publisher.json"); if (r.ok) pub = await r.json(); } catch (e) { /* not in this build */ }
    let notices = false;
    try { notices = (await fetch("/static/legal/THIRD-PARTY-NOTICES.txt", {method: "HEAD"})).ok; } catch (e) { /* not in this build */ }
    const parts = [];
    if (pub && pub.name) {
      const contact = [pub.address, pub.email, pub.phone].filter(Boolean).map(esc).join(" · ");
      parts.push(`Herausgeber der Software: ${esc(pub.name)}${contact ? " · " + contact : ""}`);
    }
    if (notices) parts.push(`<a href="/static/legal/THIRD-PARTY-NOTICES.txt" target="_blank" rel="noopener">Komponenten Dritter und ihre Lizenzen</a>`);
    if (parts.length) { box.innerHTML = parts.join("<br>"); box.hidden = false; }
  }

  // ── Profil (Filter, Greenscreen, E-Mail, QR) ────────────
  let PROFILE = {};
  // The admin page in the kiosk's accent: amber by default (app.css), the
  // operator's own colour when the theme is "eigene" - the same rule as the
  // kiosk's applyTheme. The page itself stays dark, so the light scheme's
  // darker amber is not used here.
  function adminAkzent(th) {
    const hex = (v) => (/^#[0-9a-fA-F]{6}$/.test(String(v || "")) ? String(v) : "");
    const root = document.documentElement.style;
    const own = th && th.mode === "eigene" && hex(th.accent);
    root.setProperty("--accent", own || "#e7ad59");
    root.setProperty("--on-accent", (own && hex(th.on_accent)) || "#0f0d09");
  }
  function fillProfile(d) {
    PROFILE = d.profile || {};
    adminAkzent(PROFILE.theme || {});
    // The same bottom margin as the kiosk (layout.safe_bottom, percent of the
    // screen): a bezel that hides the kiosk's edge hides this page's too.
    const safeBottom = Math.max(0, Math.min(20, Number((PROFILE.layout || {}).safe_bottom) || 0));
    document.documentElement.style.setProperty("--admin-safe-bottom", safeBottom + "vh");
    if (window.BoothKeyboard) window.BoothKeyboard.setEnabled((PROFILE.controls || {}).onscreen_keyboard !== false);
    const lut = PROFILE.luts || {}, ch = PROFILE.chroma || {}, mail = PROFILE.mail || {}, qr = PROFILE.qr || {};
    $("profileHint").textContent = ($("server_url").value.trim())
      ? "Diese Box hängt an einer Galerie — beim nächsten Sync gewinnt wieder das Profil vom Server. Für einen schnellen Eingriff vor Ort geht es trotzdem."
      : "Ohne Galerie ist das hier die einzige Stelle, an der diese Einstellungen stehen.";
    const opts = [`<option value="">keine</option>`].concat((d.luts || []).map((x) =>
      `<option value="${esc(x.id)}" ${x.id === ("lut-" + String(lut.baked || "").replace(/^lut-/, "")) ? "selected" : ""}>${esc(x.label)} (${x.size}³)</option>`));
    $("lut_baked").innerHTML = opts.join("");
    $("lut_strength").value = lut.strength ?? 1;
    $("chroma_enabled").checked = !!ch.enabled; $("chroma_color").value = ch.color || "auto";
    $("chroma_tol").value = ch.tolerance ?? 0.35;
    $("chroma_bgs").value = (ch.backgrounds || []).join("\n");
    $("mail_enabled").checked = !!mail.enabled; $("mail_host").value = mail.host || "";
    $("mail_port").value = mail.port || 587; $("mail_user").value = mail.user || "";
    $("mail_from").value = mail.from_addr || ""; $("mail_sec").value = mail.security || "starttls";
    $("mail_subject").value = mail.subject || ""; $("mail_body").value = mail.body || "";
    $("qr_mode").value = qr.mode || "auto"; $("qr_url").value = qr.url || ""; $("qr_utm").value = qr.utm || "";
    medienFelderFuellen();
  }
  $("saveProfile").onclick = async () => {
    const body = {
      luts: { ...(PROFILE.luts || {}), baked: $("lut_baked").value, strength: +$("lut_strength").value || 1 },
      chroma: { ...(PROFILE.chroma || {}), enabled: $("chroma_enabled").checked, color: $("chroma_color").value,
                tolerance: +$("chroma_tol").value || 0.35,
                backgrounds: $("chroma_bgs").value.split("\n").map((s) => s.trim()).filter(Boolean) },
      mail: { ...(PROFILE.mail || {}), enabled: $("mail_enabled").checked, host: $("mail_host").value.trim(),
              port: +$("mail_port").value || 587, user: $("mail_user").value.trim(),
              from_addr: $("mail_from").value.trim(), security: $("mail_sec").value,
              subject: $("mail_subject").value, body: $("mail_body").value,
              ...($("mail_pass").value ? { password: $("mail_pass").value } : {}) },
      qr: { ...(PROFILE.qr || {}), mode: $("qr_mode").value, url: $("qr_url").value.trim(), utm: $("qr_utm").value.trim() },
    };
    try {
      const r = await call("/api/admin/profile", { method: "POST", body: JSON.stringify(body) });
      $("actResult").textContent = r.server_wins
        ? "Profil gespeichert — der Server überschreibt es beim nächsten Sync."
        : "Profil gespeichert.";
      $("mail_pass").value = "";
    } catch (e) { $("actResult").textContent = "Fehler: " + e.message; }
  };
  // The second target as the form states it — or nothing, if it is switched
  // off and empty. A password left empty means "keep the stored one".
  function zweitesZiel() {
    const host = $("zt_host").value.trim();
    if (!host && !$("zt_on").checked) return null;
    const z = { type: $("zt_type").value, name: "picdrop", host, user: $("zt_user").value.trim(),
                path: $("zt_path").value.trim(), flat: $("zt_flat").checked, enabled: $("zt_on").checked,
                originals: $("zt_originals").checked, print_sheets: $("zt_prints").checked };
    if (+$("zt_port").value) z.port = +$("zt_port").value;
    const pw = $("zt_pass").value;
    z.password = pw ? pw : ($("zt_pass").dataset.stored ? "••••" : "");
    return z;
  }
  // Reprint from the admin: the latest runs as tiles, one tap prints one more sheet.
  $("nachdruckLaden").onclick = async () => {
    const liste = $("nachdruckListe"), aus = $("nachdruckResult");
    aus.textContent = "Wird geladen …";
    try {
      const d = await call("/api/gallery?offset=0&limit=12");
      liste.textContent = "";
      (d.items || []).forEach((it) => {
        const knopf = document.createElement("button");
        knopf.type = "button"; knopf.className = "btn-secondary";
        knopf.style.cssText = "display:grid;gap:8px;padding:10px;justify-items:center;height:auto";
        const bild = document.createElement("img");
        bild.src = it.thumb_url || it.result_url || ""; bild.alt = "";
        bild.style.cssText = "width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px";
        const text = document.createElement("span"); text.textContent = "Drucken";
        knopf.append(bild, text);
        knopf.onclick = async () => {
          knopf.disabled = true;
          try {
            const r = await call("/api/admin/reprint/" + encodeURIComponent(it.uid), { method: "POST", body: JSON.stringify({ copies: 1 }) });
            aus.textContent = "Druck ist unterwegs" + (r.pending > 1 ? " (" + r.pending + " in der Warteschlange)." : ".");
          } catch (e) { aus.textContent = "Das hat nicht geklappt: " + e.message; }
          setTimeout(() => { knopf.disabled = false; }, 5000);
        };
        liste.append(knopf);
      });
      aus.textContent = (d.items || []).length ? "" : "Es gibt noch keine Aufnahmen.";
    } catch (e) { aus.textContent = "Das hat nicht geklappt: " + e.message; }
  };
  // FTP without TLS says so where it is chosen; SFTP shows the server's key fingerprint.
  function ftpWarnung() { $("zt_ftp_warn").hidden = $("zt_type").value !== "ftp"; }
  // The folder as one picture would get it (same rule as photobox_sync/zielordner.py,
  // which checks it again on save): placeholders filled with an example event.
  function ordnerBeispiel() {
    const roh = $("zt_path").value.trim().replace(/\\/g, "/");
    const werte = { "{event}": "sommerfest-2026", "{event_name}": "Sommerfest-2026", "{datum}": "2026-07-20",
                    "{jahr}": "2026", "{monat}": "07", "{tag}": "20" };
    const unbekannt = (roh.match(/\{[^{}]*\}/g) || []).filter((p) => !(p in werte));
    const teile = roh.replace(/\{[^{}]*\}/g, (p) => werte[p] || p).split("/").map((t) => t.trim()).filter((t) => t && t !== ".");
    const aus = $("zt_path_example");
    if (unbekannt.length) { aus.textContent = "Unbekannter Platzhalter: " + unbekannt.join(", "); return; }
    if (teile.includes("..")) { aus.textContent = "„..“ ist nicht erlaubt."; return; }
    const vorn = roh.startsWith("/") ? "/" : "(Startordner)/";
    const datei = "Sommerfest-2026_2026-07-20_21-14-03.jpg";
    const ordner = $("zt_flat").checked ? teile : teile.concat(["sommerfest-2026", "2026-07-20", "fotos"]);
    aus.textContent = "Beispiel: " + vorn + ordner.concat([datei]).join("/") + ($("zt_flat").checked
      ? " — alle Dateien direkt in diesen Ordner."
      : " — darunter je Event und Tag ein Ordner, getrennt in „fotos“ und „originale“.");
  }
  $("zt_path").addEventListener("input", ordnerBeispiel);
  $("zt_flat").addEventListener("change", ordnerBeispiel);
  $("zt_type").addEventListener("change", () => { ftpWarnung(); serverSchluessel(); });
  async function serverSchluessel() {
    const box = $("zt_hostkey_box"), text = $("zt_hostkey"), knopf = $("zt_hostkey_accept");
    if ($("zt_type").value !== "sftp") { box.hidden = true; return; }
    try {
      const k = await call("/api/admin/targets/host-key");
      box.hidden = !k.host_key && !k.host_key_seen;
      const teile = [];
      if (k.host_key) teile.push("Gespeicherter Server-Schlüssel: " + k.host_key.sha256);
      if (k.host_key_seen) teile.push("Der Server zeigt jetzt einen anderen Schlüssel: " + k.host_key_seen.sha256 +
        ". Die Box sendet nichts, bis du ihn mit dem Schlüssel des Servers verglichen und angenommen hast.");
      text.textContent = teile.join(" ");
      knopf.hidden = !k.host_key_seen;
      knopf.dataset.fingerprint = k.host_key_seen ? k.host_key_seen.sha256 : "";
    } catch (e) { box.hidden = true; }
  }
  $("zt_hostkey_accept").onclick = async () => {
    const knopf = $("zt_hostkey_accept");
    // Two taps: the first says what will happen, the second accepts exactly the shown fingerprint.
    if (knopf.dataset.armed !== "1") {
      knopf.dataset.armed = "1"; knopf.textContent = "Wirklich annehmen: " + knopf.dataset.fingerprint;
      setTimeout(() => { knopf.dataset.armed = ""; knopf.textContent = "Neuen Schlüssel annehmen"; }, 8000);
      return;
    }
    knopf.dataset.armed = ""; knopf.textContent = "Neuen Schlüssel annehmen";
    try {
      await call("/api/admin/targets/host-key", { method: "POST", body: JSON.stringify({ fingerprint: knopf.dataset.fingerprint }) });
      $("zt_result").textContent = "Neuer Schlüssel angenommen.";
    } catch (e) { $("zt_result").textContent = "Nicht angenommen: " + e.message; }
    serverSchluessel();
  };
  $("zt_test").onclick = async () => {
    const aus = $("zt_result"), z = zweitesZiel();
    if (!z || !z.host) { aus.textContent = "Bitte zuerst den Server eintragen."; return; }
    aus.textContent = "Wird geprüft …";
    try {
      const r = await call("/api/admin/targets/check", { method: "POST", body: JSON.stringify({ target: z }) });
      aus.textContent = (r.ok ? "Verbindung steht. " : "Das hat nicht geklappt: ") + (r.message || "");
    } catch (e) { aus.textContent = "Das hat nicht geklappt: " + e.message; }
    serverSchluessel();
  };
  // Once, this event only: what the second target does not have yet goes into its queue.
  $("zt_backfill").onclick = async () => {
    const aus = $("zt_result");
    aus.textContent = "Wird nachgesehen …";
    try {
      const r = await call("/api/admin/targets/backfill", { method: "POST" });
      aus.textContent = r.result || "Erledigt.";
      warteschlangen();
    } catch (e) { aus.textContent = "Nicht nachgesendet: " + e.message; }
  };
  $("save").onclick = async () => {
    let targets;
    try { targets = JSON.parse($("targets").value || "[]"); } catch (e) { return alert("Ziele: ungültiges JSON"); }
    if (!Array.isArray(targets)) targets = [];
    const zt = zweitesZiel();
    if (zt) targets = targets.filter((t) => t.name !== zt.name).concat([zt]);
    const body = {
      booth_name: $("booth_name").value, ...($("admin_pin").value.trim() ? { admin_pin: $("admin_pin").value.trim() } : {}), targets,
      remote_control: { allowed: $("rc_allowed").checked, indicator: $("rc_indicator").checked },
      guest_share: { allowed: $("gs_allowed").checked, address: $("gs_address").value.trim(), port: +$("gs_port").value || 8766 },
      server: { url: $("server_url").value.trim(), ...( $("server_key").value.trim() ? { api_key: $("server_key").value.trim() } : {}) },
      camera: { backend: $("cam_backend").value, rotate: +$("cam_rotate").value, edsdk_dll: $("cam_dll").value, ccapi_url: $("cam_ccapi").value.trim(),
        ccapi_liveview: $("cam_ccapi_lv").value, ccapi_display: $("cam_ccapi_disp").value, digicam_url: $("cam_dcc").value.trim(), folder: $("cam_folder").value.trim(), preview_webcam: $("cam_pwc").value.trim(), trigger_url: $("cam_trig").value.trim(), webcam_index: +$("cam_index").value, mirror_preview: $("cam_mirror").value === "true" },
      printers: readPrinters(), print_routing: $("pr_routing").value,
      printer2: { backend: "none" },
      standalone: $("standalone").checked,
      storage: { folder: $("st_folder").value.trim(), layout: $("st_layout").value,
                 originals: $("st_originals").value === "true",
                 usb: { enabled: $("usb_enabled").checked, path: $("usb_path").value.trim(),
                        originals: $("usb_originals").value === "true" } },
    };
    await call("/api/admin/config", { method: "POST", body: JSON.stringify(body) });
    await call("/api/admin/action/restart", { method: "POST" }).catch(() => {});
    $("actResult").textContent = "Gespeichert. Die App startet neu …";
    setTimeout(() => (location.href = "/"), 6000);
  };
  $("doctorBtn").onclick = async () => {
    $("doctor").innerHTML = "<dt>läuft …</dt>";
    try {
      const d = await call("/api/admin/doctor");
      const words = { ok: "in Ordnung", warn: "ansehen", fail: "geht so nicht", skip: "nicht eingerichtet" };
      $("doctor").innerHTML = d.checks.map((c) =>
        `<dt><i class="dot ${esc(c.state)}"></i>${esc(c.name)} <small style="font-weight:400;color:var(--muted)">${esc(words[c.state] || "")}</small></dt>`
        + `<dd>${esc(c.message)}${c.hint && c.state !== "ok" && c.state !== "skip" ? `<br><em>${esc(c.hint)}</em>` : ""}</dd>`).join("");
      $("actResult").textContent = { ok: "Alles in Ordnung.", warn: "Läuft, aber sieh dir die gelben Punkte an.",
                                     fail: "So geht die Box nicht auf ein Event." }[d.worst];
    } catch (e) { $("actResult").textContent = "Fehler: " + e.message; }
  };
  // ── Meldungen ───────────────────────────────────────────────────
  const meldPunkt = { kritisch: "fail", warnung: "warn", info: "ok" };
  const meldWort = { kritisch: "die Box steht", warnung: "bitte ansehen", info: "Hinweis" };
  async function meldLaden() {
    try {
      const d = await call("/api/admin/meldungen");
      const offen = d.offen.length
        ? d.offen.map((m) => `<dt><i class="dot ${meldPunkt[m.stufe] || "skip"}"></i>${esc(m.titel)} `
            + `<small style="font-weight:400;color:var(--muted)">${esc(meldWort[m.stufe] || "")} · ${esc(m.quelle)}</small></dt>`
            + `<dd>${esc(m.text)}</dd>`).join("")
        : `<dt><i class="dot ok"></i>Nichts offen</dt><dd>Die Box hat gerade nichts zu melden.</dd>`;
      const verlauf = d.verlauf.slice(0, 12).map((m) =>
        `<dt><i class="dot ${meldPunkt[m.stufe] || "skip"}"></i>${esc(m.zeit)} — ${esc(m.titel)}</dt>`).join("");
      $("meldungen").innerHTML = offen + (verlauf ? `<dt style="margin-top:18px">Verlauf</dt><dd></dd>` + verlauf : "");
    } catch (e) {}
  }
  $("meldLaden").onclick = meldLaden;
  meldLaden();

  // ── Windows & Dauerbetrieb ──────────────────────────────────────
  const dbWorte = { optimiert: "eingestellt", offen: "offen", unbekannt: "nicht lesbar",
                    "nicht-windows": "nur unter Windows" };
  const dbPunkt = { optimiert: "ok", offen: "warn", unbekannt: "skip", "nicht-windows": "skip" };
  function dbZeichnen(d) {
    $("dauerbetriebCard").hidden = !d.windows;
    $("dauerbetrieb").innerHTML = d.punkte.map((e) => {
      const marken = [];
      if (e.admin) marken.push("Administrator");
      if (e.neustart) marken.push("Neustart nötig");
      const schalter = [`<button class="btn-secondary" data-db="${esc(e.key)}" data-was="anwenden"`
        + `${e.zustand === "optimiert" ? " disabled" : ""}>Anwenden</button>`,
        `<button class="btn-secondary" data-db="${esc(e.key)}" data-was="zuruecksetzen"`
        + `${e.gesichert ? "" : " disabled"}>Zurücksetzen</button>`].join(" ");
      return `<dt><i class="dot ${dbPunkt[e.zustand] || "skip"}"></i>${esc(e.titel)} `
        + `<small style="font-weight:400;color:var(--muted)">${esc(dbWorte[e.zustand] || "")}`
        + (marken.length ? " · " + esc(marken.join(", ")) : "")
        + (e.gesichert ? " · von der Box gesetzt" : "") + `</small></dt>`
        + `<dd>${esc(e.warum)}<br>${schalter}</dd>`;
    }).join("");
    document.querySelectorAll("[data-db]").forEach((b) => b.onclick = async () => {
      $("actResult").textContent = "läuft …";
      try {
        const a = await call(`/api/admin/dauerbetrieb/${b.dataset.db}/${b.dataset.was}`, { method: "POST" });
        $("actResult").textContent = a.ergebnis.map((r) => r.meldung).join(" · ");
        dbZeichnen({ windows: true, punkte: a.punkte });
      } catch (e) { $("actResult").textContent = "Fehler: " + e.message; }
    });
  }
  const dbLaden = async () => { try { dbZeichnen(await call("/api/admin/dauerbetrieb")); } catch (e) {} };
  $("dbLaden").onclick = dbLaden;
  $("dbAlle").onclick = async () => {
    $("actResult").textContent = "läuft …";
    const a = await call("/api/admin/dauerbetrieb/alle/anwenden", { method: "POST" });
    $("actResult").textContent = a.ergebnis.filter((r) => !r.ok).length
      ? a.ergebnis.filter((r) => !r.ok).map((r) => r.meldung).join(" · ")
      : "Alle empfohlenen Einstellungen übernommen.";
    dbZeichnen({ windows: true, punkte: a.punkte });
  };
  $("dbZurueck").onclick = async () => {
    $("actResult").textContent = "läuft …";
    const a = await call("/api/admin/dauerbetrieb/alle/zuruecksetzen", { method: "POST" });
    $("actResult").textContent = a.ergebnis.length
      ? "Zurückgenommen: " + a.ergebnis.length + " Einstellung(en)."
      : "Die Box hat nichts verstellt — es gibt nichts zurückzunehmen.";
    dbZeichnen({ windows: true, punkte: a.punkte });
  };
  dbLaden();

  document.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    $("actResult").textContent = "läuft …";
    try { $("actResult").textContent = (await call("/api/admin/action/" + b.dataset.act, { method: "POST" })).result; }
    catch (e) { $("actResult").textContent = "Fehler: " + e.message; }
  });
})();

  // Buttons that lead to another page (data-href): no inline onclick, the
  // page's CSP allows none (R2-U1).
  document.querySelectorAll("[data-href]").forEach((b) => b.addEventListener("click", () => { location.href = b.dataset.href; }));
