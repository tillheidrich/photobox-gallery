/* Every setting of the booth, editable at the booth — without a gallery and
   without internet. The form is built from the profile and its defaults
   (/api/admin/profile/full): a setting that exists shows up here, nobody has
   to remember to add a field. Names and choices below only make it readable.

   Used by admin.html: PhotoboxSettings.mount(element, call). */
(function () {
  "use strict";

  // Group = one top-level key of the profile: [title, what it is for].
  const GROUPS = {
    event: ["Veranstaltung", "Name, Datum und Zeilen, die auf Karten und Bildschirm stehen"],
    start: ["Startbild", "Was Gäste zuerst sehen: Titel, Statuszeile, Kacheln, Bilder"],
    modes: ["Modi", "Was die Box anbietet: photo, collage, gif, boomerang, video"],
    formats: ["Formate", "Welche Bildformate zur Wahl stehen"],
    default_format: ["Standardformat", "Format, mit dem die Box startet"],
    countdown: ["Countdown", "Sekunden, Live-Bild, „Bitte lächeln“, Abbrechen"],
    flash: ["Blitz", "Ladezeit und Verhalten des Aufsteckblitzes"],
    screen_flash: ["Bildschirm als Blitz", "Weißer Bildschirm beim Auslösen"],
    retake: ["Nochmal", "Darf eine Aufnahme wiederholt werden"],
    review_seconds: ["Bild kurz zeigen", "Sekunden, die jede Aufnahme stehen bleibt"],
    print: ["Drucken", "An oder aus, Kopien, Limits, Papierzähler, Schärfe"],
    print_layout: ["Druckblatt", "Papier, Rand, Einpassen"],
    print_qr: ["QR auf dem Druck", "Größe und Ecke"],
    collage_templates: ["Eigene Vorlagen", "Vorlagen aus dem Editor, die angeboten werden"],
    collage_builtins: ["Mitgelieferte Vorlagen", "Leer heißt: alle"],
    collage_plain_qr: ["QR-Code auf schlichten Layouts", "Aus: nur Fotos, kein Code"],
    collage_exact: ["Genau die angehakten Vorlagen", "Eigene und mitgelieferte zusammen"],
    collage_fit: ["Foto im Bildfeld", "Beschneiden oder ganz einpassen"],
    collage_upload: ["Collage in der Galerie", "Blatt, Einzelbilder oder beides"],
    collage_style: ["Aussehen der Vorlagen", "Farben, Schrift, Zeilen, Ecken, Rahmen"],
    backgrounds: ["Collage-Hintergründe", "Vom Gast wählbar"],
    frame: ["Rahmen und Texte auf dem Bild", "Eigenes PNG über Foto und Druck, freie Textzeilen"],
    looks: ["Looks", "Welche Looks angeboten werden"],
    looks_max: ["Looks: Anzahl", "So viele stehen höchstens zur Wahl"],
    looks_mode: ["Looks: Zeitpunkt", "Vor oder nach der Aufnahme"],
    looks_auto: ["Looks: fest", "Dieser Look wird ohne Wahl angewendet"],
    luts: ["Farb-Looks (LUT)", "Welche gezeigt werden, welcher eingerechnet wird"],
    chroma: ["Greenscreen", "Farbe, Toleranz, Hintergründe"],
    mirror: ["Spiegeln", "Live-Bild und Ergebnis"],
    boomerang: ["Boomerang (eigener Aufnahme-Modus aus dem Livebild)", "Dauer, Format, Wiederholungen"],
    gif: ["GIF", "Anzahl Bilder, Tempo, Format"],
    video: ["Video", "Dauer, Format, Ton"],
    guest_text: ["Gruß des Gastes", "Kurzer Text, der mit auf die Karte kommt"],
    draw: ["Zeichnen aufs Bild", "Gäste malen oder unterschreiben nach der Aufnahme mit dem Finger"],
    guest_choice: ["Wahl des Gastes", "Was der Gast vor der Aufnahme entscheiden darf"],
    mail: ["E-Mail an Gäste", "Versand über die Galerie oder ein eigenes Postfach"],
    qr: ["QR-Code", "Wohin er zeigt"],
    qr_on_screen: ["QR auf dem Bildschirm", "Nach der Aufnahme zeigen"],
    qr_url_template: ["QR-Adresse (alt)", "Eigene Adresse mit {uid} und {event}"],
    wifi_share: ["Download ohne Internet", "Gästeseite im WLAN der Box. An dieser Box zusätzlich gesperrt"],
    privacy: ["Datensparsamer Betrieb", "Nur drucken, nichts hochladen, automatisch löschen"],
    consent: ["Einwilligung", "„In die Galerie“ oder „Nur drucken“ vor der Aufnahme"],
    disclaimer: ["Hinweis vor dem Start", "Text, dem der Gast zustimmt"],
    upload_originals: ["Bilder zusätzlich ohne Rahmen hochladen", "Gäste laden jedes Bild dann auch ohne Rahmen herunter"],
    result_seconds: ["Ergebnis zeigen", "Sekunden bis zurück zum Start"],
    idle_seconds: ["Leerlauf", "Sekunden ohne Eingabe bis zurück zum Start"],
    gallery_on_booth: ["Galerie an der Box", "Gäste blättern durch alle Aufnahmen"],
    allow_delete: ["Löschen erlauben", "Gast darf sein Bild löschen"],
    sounds: ["Töne", "Countdown, Auslöser, fertig, Druck"],
    attract: ["Bildschirmschoner", "Was läuft, wenn niemand da ist"],
    backdrops: ["Hintergrund je Bildschirm", "Bild oder Farbe hinter jedem Schritt"],
    phase_media: ["Medien je Phase", "Bild oder Video beim Verarbeiten, zwischen Aufnahmen, danach, bei Fehlern"],
    texts: ["Texte", "Jede Zeile der Oberfläche überschreiben, je Sprache"],
    languages: ["Sprachen", "de, en …; die erste ist die Startsprache"],
    theme: ["Farben und Form", "Hauptfarbe, Hintergrund, Schrift, Ecken, Knopfgröße"],
    accent: ["Hauptfarbe (alt)", "Wird von „Farben und Form“ gespiegelt"],
    layout: ["Bildschirm", "Ränder für Blenden, Größe, Ecke des Betreiber-Menüs"],
    controls: ["Bedienung", "Buzzer, Tasten, Maus, Touch, Mauszeiger"],
    access: ["Zugang", "PIN, Schloss in der Ecke, Sperren an der Box"],
    lock: ["Pause und Zeitfenster", "Box pausieren, Pausenbild, feste Zeiten"],
    camera: ["Belichtung", "ISO, Blende, Zeit, Weißabgleich. Leer = Wert an der Kamera"],
    aufraeumen: ["Aufräumen", "Alte Aufnahmen an der Box löschen"],
    meldungen: ["Betriebsmeldungen", "Wovor die Box warnt"],
    webhooks: ["Webhooks", "Andere Systeme benachrichtigen"],
    http_trigger: ["Auslösen von außen", "Buzzer über HTTP"],
  };

  // What is asked for most comes first; the rest follows in profile order.
  const ORDER = ["event", "start", "modes", "countdown", "print", "print_layout", "collage_style", "frame",
    "theme", "access", "controls", "layout", "sounds", "consent", "texts"];

  const CHOICES = {
    "countdown.live_view": ["fullscreen", "frame", "off"],
    "start.layout": ["bottom", "left", "right", "center", "custom"],
    "start.text_tone": ["auto", "hell", "dunkel"],
    "theme.mode": ["dunkel", "hell", "eigene"],
    "start.button_size": ["m", "l", "xl"],
    "start.fit": ["drift", "cover", "contain"],
    "print_layout.paper": ["10x15", "postcard", "13x18", "15x20", "5x15", "card"],
    "print_layout.fit": ["cover", "contain"],
    "print.sharpen": ["off", "light", "medium", "strong"],
    "layout.corner": ["top-left", "top-right", "bottom-left", "bottom-right"],
    "controls.mouse_start": ["off", "left", "right", "middle", "any"],
    "collage_upload": ["collage", "photos", "both"],
    "chroma.color": ["auto", "green", "blue"],
    "mail.transport": ["gallery", "smtp"],
    "mail.security": ["starttls", "ssl", "none"],
    "mail.attach": ["web", "original", "none"],
    "privacy.mode": ["normal", "print_only"],
    "default_format": ["4:5", "9:16", "3:2", "1:1", "16:9"],
    // Same lists as the gallery form (app/blueprints/admin_booth.py): a word the
    // booth does not know would silently fall back to the default.
    "attract.source": ["recent", "media", "own", "text", "blank", "off"],
    "attract.hint_style": ["bar", "center", "none"],
    "attract.fit": ["cover", "contain", "auto"],
    "start.gallery_style": ["pill", "prominent"],
    "start.corner_size": ["m", "l", "xl"],
    "layout.gallery_size": ["m", "l", "xl"], "layout.back_size": ["m", "l", "xl"],
    "gif.format": ["4:5", "9:16", "3:2", "1:1", "2:3"],
    "gif.boomerang.format": ["4:5", "9:16", "3:2", "1:1", "2:3", "16:9"],
    "gif.boomerang.layout": ["blur", "crop"],
    "boomerang.layout": ["blur", "crop"],
    "video.layout": ["blur", "crop"],
    // Same list as CLIP_SOURCES in profile_rules.py.
    "boomerang.source": ["live", "camera"],
    "video.source": ["live", "camera"],
    // Same lists as the gallery form (PREVIEW_* in app/blueprints/admin_booth.py).
    "camera.preview_iso": ["auto", "400", "800", "1600", "3200", "6400"],
    "camera.preview_aperture": ["", "1.8", "2", "2.8", "3.5", "4", "5", "5.6"],
    "camera.preview_shutter": ["", "1/15", "1/30", "1/60", "1/125"],
    "camera.preview_iso_max": ["3200", "6400", "12800", "25600"],
    "camera.preview_shutter_min": ["1/60", "1/30", "1/15"],
  };

  // path: [min, max, step] — the limits the gallery form applies when it saves
  // (_num(...) in app/blueprints/admin_booth.py). Without them a typo at the
  // booth, say 0 pictures for a GIF, would be stored as typed.
  const LIMITS = {
    "countdown.first": [1, 20, 1], "countdown.next": [1, 20, 1], "countdown.review_seconds": [0, 10, 0.5],
    "countdown.smile_ms": [200, 3000, 100], "review_seconds": [3, 60, 1],
    "gif.shots": [2, 10, 1], "gif.interval": [0.5, 10, 0.5], "gif.frame_ms": [80, 3000, 20],
    "gif.video_seconds": [0, 60, 1], "gif.from_collage_min": [2, 8, 1],
    "gif.boomerang.fps": [5, 60, 1], "gif.boomerang.loops": [1, 10, 1],
    "boomerang.seconds": [1, 10, 0.5], "boomerang.fps": [5, 60, 1], "boomerang.loops": [1, 10, 1],
    "boomerang.min_total_seconds": [5, 60, 1], "boomerang.max_total_seconds": [5, 60, 1],
    "video.seconds": [1, 60, 1], "video.fps": [5, 60, 1],
    "attract.after_seconds": [10, 3600, 10], "attract.seconds_per_item": [2, 120, 1], "attract.dim": [0, 80, 5],
    "attract.hint_size": [100, 250, 10],
    "sounds.volume": [0, 1, 0.05], "looks_max": [0, 24, 1],
    "flash.preview_gain": [0, 100, 1],
    "aufraeumen.nach_tagen": [1, 365, 1], "aufraeumen.behalten_mindestens": [0, 1000, 1],
    "meldungen.wiederholen_minuten": [0, 1440, 5],
    "print.queue_max": [1, 50, 1], "print.queue_max_hours": [1, 24, 1],
  };

  // Lists of numbers, typed with commas and saved as numbers (NUMBER_LISTS in
  // profile_rules.py: [min, max, most entries]).
  const NUMBER_LISTS = { "boomerang.seconds_choices": [1, 15, 4] };

  // A list whose members are a fixed set: ticked, not typed. [value, name].
  const MULTI = {
    modes: [["photo", "Foto"], ["collage", "Collage"], ["gif", "GIF"], ["boomerang", "Boomerang"], ["video", "Video"]],
  };

  const LABELS = {
    "event.name": "Name der Veranstaltung", "event.date": "Datum", "event.slug": "Kennung",
    "start.show_title": "Titel zeigen", "start.show_status": "Statuszeile zeigen (Kamera, Drucker, Netz)",
    "start.show_gallery_button": "Knopf „Galerie“ zeigen",
    "start.gallery_style": "Knopf „Galerie“: dezent oder groß in der Akzentfarbe",
    "start.corner_size": "Galerie, Sprache und Schloss: Größe (m wie früher, l, xl)", "start.media": "Bilder und Videos im Hintergrund",
    "start.media_seconds": "Wechsel alle … Sekunden", "start.layout": "Anordnung der Kacheln",
    "start.button_size": "Größe der Kacheln", "start.fit": "Hintergrund einpassen",
    "countdown.first": "Sekunden vor dem ersten Foto", "countdown.next": "Sekunden vor jedem weiteren",
    "countdown.live_view": "Live-Bild im Countdown", "countdown.smile_text": "Text beim Auslösen",
    "countdown.cancel": "Abbrechen erlauben",
    "print.enabled": "Drucken an", "print.auto": "Automatisch drucken", "print.copies_max": "Kopien je Druckauftrag (höchstens)",
    "print.limit_session": "Drucke je Aufnahme (höchstens)", "print.limit_event": "Drucke je Veranstaltung (0 = unbegrenzt)",
    "print.qr_on_print": "QR-Code auf dem Druck", "print.seconds_per_print": "Sekunden je Karte (für die Wartezeit)",
    "print.sheets_per_cassette": "Blatt je Kassette (0 = keine Warnung)", "print.sharpen": "Nachschärfen",
    "print.number_keys": "Zifferntasten 1–9 drucken Kopien", "print.reprint_from_gallery": "Nachdruck aus der Galerie an der Box",
    "print.queue_while_empty": "Drucke vormerken, wenn Papier oder Farbband fehlt",
    "print.fit_to_paper": "Vorlage an das Papier des Druckers anpassen", "print.queue_max": "Höchstens so viele vorgemerkte Drucke",
    "print.queue_max_hours": "Vorgemerkte Drucke verfallen nach … Stunden",
    "print_layout.margin_mm": "Rand in mm", "print_layout.fit": "Einpassen", "print_layout.paper": "Papier",
    "print_layout.background": "Papierfarbe", "print_layout.per_sheet": "Stück je Blatt", "print_layout.rotate_to_fit": "Drehen, wenn es besser passt",
    "frame.enabled": "Rahmen an", "frame.overlay": "PNG über dem Bild", "frame.print_overlay": "Eigenes PNG für den Druck",
    "frame.on_photo": "Auf dem Foto", "frame.on_print": "Auf dem Druck", "frame.texts": "Textzeilen",
    "access.pin": "PIN", "access.lock_icon": "Schloss in der Ecke zeigen", "access.lock_on_booth": "Sperren an der Box erlauben",
    "access.hotspot_seconds": "Ecke gedrückt halten (Sekunden)",
    "controls.hide_cursor": "Mauszeiger ausblenden (erscheint, wenn die Maus bewegt wird)", "controls.onscreen_keyboard": "Bildschirmtastatur für Eingabefelder", "controls.touch": "Touch erlauben",
    "controls.mouse_start": "Maustaste als Buzzer",
    "controls.arrow_navigation": "Pfeiltasten bedienen die Oberfläche (Pfeile lösen dann nie aus; aus für einen Presenter mit Pfeil rechts als Buzzer)", "controls.keys_start": "Tasten, die auslösen",
    "layout.corner": "Ecke des Betreiber-Menüs",
    "layout.gallery_size": "Kacheln der Galerie an der Box", "layout.back_size": "Knopf „Zurück“: Größe", "layout.scale": "Größe der Oberfläche in %",
    "sounds.volume": "Lautstärke",
    "consent.enabled": "Einwilligung abfragen", "consent.print_only": "„Nur drucken“ anbieten", "consent.days": "Tage bis zum Löschen",
    "theme.accent": "Hauptfarbe", "theme.bg": "Hintergrund", "theme.text": "Schrift", "theme.panel": "Flächen",
    "theme.mode": "Farbschema (eigene = die sechs Farben)", "theme.on_accent": "Schrift auf der Hauptfarbe",
    "theme.muted": "Nebentext",
    "start.text_tone": "Schrift auf dem Startbild: auto = hell auf eigenem Startbild, sonst Schema · hell · dunkel",
    "lock.active": "Box pausieren",
    "modes": "Angebotene Modi (mindestens einer)",
    "countdown.poses": "Ansage zwischen den Aufnahmen einer Serie („Jetzt alle winken!“)",
    "camera.preview_iso": "ISO im Live-Bild mit Blitz (auto = so hell wie nötig)",
    "camera.preview_aperture": "Blende im Live-Bild mit Blitz (leer = wie Aufnahme)",
    "camera.preview_shutter": "Zeit im Live-Bild mit Blitz (leer = wie Aufnahme)",
    "camera.preview_iso_max": "Höchste ISO im Live-Bild bei „auto“",
    "camera.preview_shutter_min": "Längste Zeit im Live-Bild bei „auto“ (danach hellt die Box in Software auf)",
    "flash.preview_gain": "Live-Bild aufhellen (%)",
    "flash.preview_gain_auto": "Live-Bild automatisch je Bild aufhellen",
    "countdown.sound": "Ton beim Countdown", "countdown.review_seconds": "Aufnahme nach dem Auslösen zeigen (Sekunden)",
    "countdown.smile_ms": "„Bitte lächeln“ zeigen (Millisekunden)", "countdown.smile_media": "Bild für „Bitte lächeln“ (Dateiname)",
    "countdown.crop_guide": "Beschnitt im Live-Bild andeuten", "countdown.style": "Darstellung", "countdown.display": "Zahl oder Ring",
    "countdown.size": "Größe der Zahl", "countdown.color": "Farbe der Zahl",
    "gif.shots": "Anzahl Bilder", "gif.interval": "Pause zwischen den Aufnahmen (Sekunden)",
    "gif.frame_ms": "Anzeigedauer je Bild (Millisekunden)", "gif.video_seconds": "Mindestlänge des Films zum GIF (Sekunden, 0 = drei Durchläufe)",
    "gif.format": "Bildformat", "gif.pingpong": "Vor und zurück abspielen", "gif.also_mp4": "Zusätzlich als Film (MP4)",
    "gif.from_collage": "Aus einer Collage zusätzlich ein GIF bauen", "gif.from_collage_min": "Erst ab so vielen Einzelbildern",
    "gif.boomerang": "Zum GIF zusätzlich: Hin-und-zurück-Film",
    "gif.boomerang.enabled": "Boomerang-Fassung erzeugen", "gif.boomerang.format": "Format des Boomerangs",
    "gif.boomerang.layout": "Bild einpassen: blur = ganz, mit unscharfem Rand · crop = beschnitten",
    "gif.boomerang.loops": "Wiederholungen", "gif.boomerang.fps": "Bilder pro Sekunde",
    "gif.boomerang.guest_choice": "Gast darf am Bildschirm umschalten",
    "attract.enabled": "Bildschirmschoner benutzen", "attract.after_seconds": "Startet nach … Sekunden Ruhe",
    "attract.source": "Was läuft (recent = die letzten Aufnahmen)", "attract.seconds_per_item": "Sekunden je Bild",
    "attract.show_hint": "Hinweis „Bildschirm berühren, um Fotos zu machen“ zeigen",
    "attract.hint_style": "Hinweis: Platz", "attract.fit": "Bilder einpassen", "attract.dim": "Abdunkeln in % (0–80)",
    "attract.hint_size": "Größe des Hinweises in % (100–250)",
    "attract.mute": "Videos ohne Ton", "attract.media": "Eigene Auswahl (Dateinamen)",
    "attract.color": "Farbe der Fläche", "attract.text_color": "Farbe des Hinweises",
    "sounds.enabled": "Töne an (aus = die Box bleibt stumm)",
    "boomerang.seconds": "Dauer (Sekunden)", "boomerang.fps": "Bilder pro Sekunde", "boomerang.loops": "Wiederholungen",
    "boomerang.seconds_guest_choice": "Gast wählt die Aufnahmedauer (Knöpfe vor der Aufnahme)",
    "boomerang.seconds_choices": "Dauern zur Wahl (1–15 s, höchstens vier, z. B. 2, 5, 10)",
    "boomerang.min_total_seconds": "Fertiger Film mindestens (Sekunden, bei Wahl der Dauer)",
    "boomerang.max_total_seconds": "Fertiger Film höchstens (Sekunden, bei Wahl der Dauer)",
    "boomerang.format": "Format", "boomerang.layout": "Bild einpassen",
    "boomerang.guest_choice": "Gast wählt das Format auch, wenn die Formatwahl für Fotos aus ist",
    "boomerang.print": "Boomerang drucken: Bogen mit den Collage-Layouts (Bilder aus der Aufnahme)",
    "video.seconds": "Dauer (Sekunden)", "video.fps": "Bilder pro Sekunde", "video.format": "Format", "video.layout": "Bild einpassen",
    "boomerang.source": "Quelle: live = Live-Bild · camera = Film der Kamera (höhere Auflösung; braucht eine Kamera, die über USB filmt, und eine SD-Karte in der Kamera)",
    "video.source": "Quelle: live = Live-Bild · camera = Film der Kamera (höhere Auflösung; braucht eine Kamera, die über USB filmt, und eine SD-Karte in der Kamera)",
    // Frames ("Rahmen") for boomerang and video, media/motion_frames.py.
    "boomerang.frames": "Rahmen anbieten (der Film läuft im Fenster eines Rahmens)",
    "boomerang.frame_templates": "Eigene Rahmen (Dateinamen)", "boomerang.frame_builtins": "Mitgelieferte Rahmen (leer = alle)",
    "boomerang.frame_none": "Auch „Ohne Rahmen“ anbieten", "boomerang.frame_default": "Vorausgewählt (leer = der erste, none = ohne)",
    "boomerang.frame_guest_choice": "Gast wählt den Rahmen (aus = immer die Vorauswahl)",
    "video.frames": "Rahmen anbieten (der Film läuft im Fenster eines Rahmens)",
    "video.frame_templates": "Eigene Rahmen (Dateinamen)", "video.frame_builtins": "Mitgelieferte Rahmen (leer = alle)",
    "video.frame_none": "Auch „Ohne Rahmen“ anbieten", "video.frame_default": "Vorausgewählt (leer = der erste, none = ohne)",
    "video.frame_guest_choice": "Gast wählt den Rahmen (aus = immer die Vorauswahl)",
    // The GIF is set up like the boomerang (18.11.2026) and takes its frames.
    "gif.guest_choice": "Gast wählt das Format auch, wenn die Formatwahl für Fotos aus ist",
    "gif.frames": "Rahmen anbieten (die Rahmen des Boomerangs, um jedes Bild des GIFs)",
    "gif.frame_templates": "Eigene Rahmen (Dateinamen)", "gif.frame_builtins": "Mitgelieferte Rahmen (leer = alle)",
    "gif.frame_none": "Auch „Ohne Rahmen“ anbieten", "gif.frame_default": "Vorausgewählt (leer = der erste, none = ohne)",
    "gif.frame_guest_choice": "Gast wählt den Rahmen (aus = immer die Vorauswahl)",
    "draw.enabled": "Zeichnen am Ergebnis anbieten", "draw.modes": "Bei (photo, collage)",
    "draw.colors": "Farben zur Wahl (#rrggbb, höchstens 8)",
  };

  const SECRET = "••••";
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isPlain = (v) => v && typeof v === "object" && !Array.isArray(v);
  const isSimpleList = (v) => Array.isArray(v) && v.every((x) => typeof x === "string" || typeof x === "number");
  const pretty = (key) => key.replace(/_/g, " ");

  let state = null;          // answer of /api/admin/profile/full
  let dirty = new Set();     // top-level keys that were touched
  let root = null, call = null;

  // A number pulled into the limits the gallery form applies; a path without
  // a limit is returned as it is.
  function limit(path, value) {
    const l = LIMITS[path];
    if (!l || !Number.isFinite(value)) return value;
    return Math.min(l[1], Math.max(l[0], value));
  }

  // Modes ticked on the page. None ticked would leave a booth with nothing to
  // offer; the gallery form falls back to a photo, so does this.
  function readMulti(values) {
    return values.length ? values : ["photo"];
  }

  function field(path, value, fallback) {
    const id = "pf_" + path.replace(/[^a-z0-9]/gi, "_");
    const label = LABELS[path] || pretty(path.split(".").slice(1).join(" › ") || path);
    const kind = fallback === undefined ? value : fallback;
    const data = `data-path="${esc(path)}"`;
    if (MULTI[path] && Array.isArray(value)) {
      return `<div class="ps-multi" id="${id}" ${data} data-kind="multi" role="group" aria-label="${esc(label)}"><span>${esc(label)}</span>`
        + MULTI[path].map(([v, name]) => `<label class="check ps-check"><input type="checkbox" value="${esc(v)}"${value.includes(v) ? " checked" : ""}> ${esc(name)}</label>`).join("")
        + "</div>";
    }
    if (typeof kind === "boolean") {
      return `<label class="check ps-check"><input type="checkbox" id="${id}" ${data} data-kind="bool"${value ? " checked" : ""}> ${esc(label)}</label>`;
    }
    if (CHOICES[path]) {
      const list = CHOICES[path].includes(value) ? CHOICES[path] : [value, ...CHOICES[path]];
      return `<label>${esc(label)}<select id="${id}" ${data} data-kind="text">`
        + list.map((o) => `<option${o === value ? " selected" : ""}>${esc(o)}</option>`).join("") + "</select></label>";
    }
    if (typeof kind === "number") {
      const l = LIMITS[path];
      const range = l ? ` min="${l[0]}" max="${l[1]}" step="${l[2]}"` : ` step="${Number.isInteger(kind) && Number.isInteger(value) ? "1" : "any"}"`;
      return `<label>${esc(label)}<input type="number"${range} id="${id}" ${data} data-kind="number" value="${esc(value)}"></label>`;
    }
    if (typeof kind === "string" || kind === null) {
      const text = value === null || value === undefined ? "" : String(value);
      if (/^#[0-9a-f]{6}$/i.test(text)) {
        return `<label>${esc(label)}<span class="ps-color"><input type="color" value="${esc(text)}" data-mirror="${id}">`
          + `<input id="${id}" ${data} data-kind="text" value="${esc(text)}"></span></label>`;
      }
      const secret = text === SECRET ? ' placeholder="unverändert lassen"' : "";
      return `<label>${esc(label)}<input id="${id}" ${data} data-kind="text" value="${esc(text)}"${secret} autocomplete="off"></label>`;
    }
    if (NUMBER_LISTS[path] && Array.isArray(value)) {
      return `<label>${esc(label)} <small>(Sekunden, mit Komma getrennt)</small><input id="${id}" ${data} data-kind="numlist" inputmode="decimal" value="${esc(value.join(", "))}"></label>`;
    }
    if (isSimpleList(value) && isSimpleList(kind || [])) {
      return `<label>${esc(label)} <small>(mit Komma getrennt)</small><input id="${id}" ${data} data-kind="list" value="${esc(value.join(", "))}"></label>`;
    }
    return `<label>${esc(label)} <small>(JSON)</small><textarea id="${id}" ${data} data-kind="json" spellcheck="false">${esc(JSON.stringify(value, null, 2))}</textarea></label>`;
  }

  function fields(path, value, fallback) {
    if (isPlain(value) && Object.keys(value).length && (fallback === undefined || isPlain(fallback))) {
      const keys = Object.keys(value);
      const leaves = keys.filter((k) => !isPlain(value[k]) || !Object.keys(value[k]).length);
      const nested = keys.filter((k) => !leaves.includes(k));
      return `<div class="ps-grid">${leaves.map((k) => field(path + "." + k, value[k], (fallback || {})[k])).join("")}</div>`
        + nested.map((k) => `<fieldset class="ps-sub"><legend>${esc(LABELS[path + "." + k] || pretty(k))}</legend>`
          + fields(path + "." + k, value[k], (fallback || {})[k]) + "</fieldset>").join("");
    }
    return `<div class="ps-grid">${field(path, value, fallback)}</div>`;
  }

  function render() {
    const p = state.profile, d = state.defaults;
    const keys = [...ORDER.filter((k) => k in p), ...Object.keys(d).filter((k) => !ORDER.includes(k) && k !== "version")];
    const hold = state.server
      ? `<label class="check ps-hold"><input type="checkbox" id="psHold"${state.hold ? " checked" : ""}> `
        + "Einstellungen dieser Box festhalten. Die Galerie überschreibt sie dann nicht, bis das Häkchen wieder weg ist.</label>"
      : '<p class="hint">Diese Box läuft ohne Galerie. Was hier steht, gilt.</p>';
    root.innerHTML = `
      <h2>Alle Einstellungen</h2>
      <p class="hint">Jede Einstellung der Box, auch ohne Internet. Dieselben Werte stehen in der Galerie unter „Box-Profil“.</p>
      ${hold}
      <input id="psSearch" type="search" placeholder="Suchen, z. B. Countdown, Druck, Schloss" autocomplete="off">
      <div id="psGroups">${keys.map((k) => {
        const [title, about] = GROUPS[k] || [pretty(k), ""];
        return `<details class="ps-group" data-key="${esc(k)}" data-find="${esc((title + " " + about + " " + k + " " + JSON.stringify(Object.keys(isPlain(p[k]) ? p[k] : {}))).toLowerCase())}">`
          + `<summary><b>${esc(title)}</b><span>${esc(about)}</span></summary>${fields(k, p[k], d[k])}</details>`;
      }).join("")}</div>
      <div class="actions ps-actions"><button type="button" class="btn-primary" id="psSave" disabled>Einstellungen speichern</button>
        <span id="psNote" role="status"></span></div>`;
    root.querySelector("#psSearch").addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      root.querySelectorAll(".ps-group").forEach((g) => {
        const hit = !q || g.dataset.find.includes(q) || g.textContent.toLowerCase().includes(q);
        g.hidden = !hit;
        if (q && hit) g.open = true;
      });
    });
    root.querySelectorAll("[data-mirror]").forEach((c) => c.addEventListener("input", () => {
      const t = root.querySelector("#" + c.dataset.mirror);
      t.value = c.value;
      t.dispatchEvent(new Event("input", { bubbles: true }));
    }));
    root.querySelector("#psGroups").addEventListener("input", touched);
    root.querySelector("#psGroups").addEventListener("change", touched);
    root.querySelector("#psSave").addEventListener("click", save);
    const h = root.querySelector("#psHold");
    if (h) h.addEventListener("change", async () => {
      const r = await call("/api/admin/profile/hold", { method: "POST", body: JSON.stringify({ hold: h.checked }) });
      state.hold = r.hold;
      note(r.hold ? "Festgehalten. Die Galerie wartet." : "Freigegeben. Beim nächsten Kontakt gilt wieder das Profil der Galerie.");
    });
  }

  function touched(e) {
    const owner = e.target.closest ? e.target.closest("[data-path]") : null;
    const path = owner && owner.dataset.path;
    if (!path) return;
    dirty.add(path.split(".")[0]);
    root.querySelector("#psSave").disabled = false;
    note("");
  }

  function note(text, bad) {
    const n = root.querySelector("#psNote");
    n.textContent = text;
    n.dataset.bad = bad ? "1" : "";
  }

  function read(el) {
    switch (el.dataset.kind) {
      case "bool": return el.checked;
      case "number": return limit(el.dataset.path, el.value === "" ? 0 : Number(el.value));
      case "multi": return readMulti([...el.querySelectorAll("input:checked")].map((i) => i.value));
      case "list": return el.value.split(",").map((s) => s.trim()).filter(Boolean);
      case "numlist": {
        // Out of range, words and doubles drop here; the booth checks again.
        const [low, high, most] = NUMBER_LISTS[el.dataset.path];
        const out = [];
        for (const n of el.value.split(/[,;\s]+/).map(Number)) {
          if (Number.isFinite(n) && n >= low && n <= high && !out.includes(n)) out.push(n);
        }
        return out.slice(0, most);
      }
      case "json": return JSON.parse(el.value || "null");
      default: return el.value;
    }
  }

  function setPath(target, parts, value) {
    let o = target;
    for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]];
    o[parts[parts.length - 1]] = value;
  }

  async function save() {
    const patch = {};
    for (const key of dirty) patch[key] = JSON.parse(JSON.stringify(state.profile[key]));
    for (const el of root.querySelectorAll("#psGroups [data-path]")) {
      const parts = el.dataset.path.split(".");
      if (!dirty.has(parts[0])) continue;
      let value;
      try { value = read(el); } catch (err) {
        el.focus();
        return note(`„${el.dataset.path}“ ist kein gültiges JSON.`, true);
      }
      if (parts.length === 1) patch[parts[0]] = value; else setPath(patch, parts, value);
    }
    try {
      const r = await call("/api/admin/profile", { method: "POST", body: JSON.stringify(patch) });
      const taken = r.server_wins && !state.hold
        ? " Achtung: Die Galerie überschreibt das beim nächsten Kontakt. Oben „festhalten“ anhaken, wenn es bleiben soll." : "";
      await load();
      note("Gespeichert. Die Box hat es sofort übernommen." + taken, !!taken);
    } catch (err) {
      note(String(err.message || err), true);
    }
  }

  async function load() {
    state = await call("/api/admin/profile/full");
    dirty = new Set();
    const open = new Set([...root.querySelectorAll(".ps-group[open]")].map((g) => g.dataset.key));
    render();
    open.forEach((k) => { const g = root.querySelector(`.ps-group[data-key="${k}"]`); if (g) g.open = true; });
  }

  window.PhotoboxSettings = {
    // Pure helpers, exposed for the tests (node, no page needed).
    field, fields, limit, readMulti, limits: LIMITS,
    mount(element, callFn) {
      root = element; call = callFn;
      return load().catch((err) => { root.innerHTML = `<h2>Alle Einstellungen</h2><p class="hint">${esc(err.message || err)}</p>`; });
    },
  };
})();
