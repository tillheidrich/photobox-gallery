/* Template editor — the same file in the web admin and at the booth.
 *
 * Work happens on a sheet drawn to scale. What is saved is the JSON the booth
 * renders — no intermediate format, no second truth. The checks live in
 * vorlagen_kern.py (gallery and booth alike); this file only has to agree
 * with them and with the renderer on geometry.
 *
 * Embedded through pbVorlagenEditor.start({wurzel, vorlage, speichern, loeschen,
 * sprache}). "speichern" receives the finished template and returns a
 * promise; where it ends up is up to the page — in the web admin that is the
 * gallery, at the booth the local disk.
 *
 * The decisions (undo steps, layer order, snapping, alignment) are pure
 * functions between the <editor-kern> markers, so tests can run them with
 * Node without a browser.
 */
(function () {
  "use strict";

  // <editor-kern>
  /* Undo history. A state is a string (the template as JSON), so a step can
   * never alias a later edit. Between beginne() and schliesse() — a drag, a
   * resize — intermediate states are ignored: a drag is one step, not one
   * per mouse move. */
  function Verlauf(max) {
    this.max = max || 100;
    this.stapel = [];
    this.zeiger = -1;
    this.offen = false;
  }
  Verlauf.prototype.aktuell = function () { return this.zeiger >= 0 ? this.stapel[this.zeiger] : null; };
  Verlauf.prototype.merke = function (zustand) {
    if (this.offen) return false;
    if (this.zeiger >= 0 && this.stapel[this.zeiger] === zustand) return false;
    this.stapel = this.stapel.slice(0, this.zeiger + 1);
    this.stapel.push(zustand);
    if (this.stapel.length > this.max) this.stapel.shift();
    this.zeiger = this.stapel.length - 1;
    return true;
  };
  Verlauf.prototype.beginne = function () { this.offen = true; };
  Verlauf.prototype.schliesse = function (zustand) {
    this.offen = false;
    return this.merke(zustand);
  };
  Verlauf.prototype.kannZurueck = function () { return this.zeiger > 0; };
  Verlauf.prototype.kannVor = function () { return this.zeiger < this.stapel.length - 1; };
  Verlauf.prototype.zurueck = function () {
    if (!this.kannZurueck()) return null;
    this.zeiger -= 1;
    return this.stapel[this.zeiger];
  };
  Verlauf.prototype.vor = function () {
    if (!this.kannVor()) return null;
    this.zeiger += 1;
    return this.stapel[this.zeiger];
  };

  /* Every element gets a stable id. The scheme is the one vorlagen_kern.py
   * uses for templates without ids, so a builtin template and its saved copy
   * name their layers alike. */
  function sichereIds(v) {
    var vergeben = { qr: true, overlay: true };
    function nimm(el, vorschlag) {
      var id = String(el.id || "").toLowerCase().replace(/[^a-z0-9-]+/g, "-").slice(0, 24);
      if (!id || vergeben[id]) {
        id = vorschlag; var n = 2;
        while (vergeben[id]) id = vorschlag + "-" + (n++);
      }
      vergeben[id] = true;
      el.id = id;
    }
    (v.slots || []).forEach(function (s, i) { nimm(s, "slot-" + (i + 1)); });
    (v.texts || []).forEach(function (t, i) { nimm(t, "text-" + (i + 1)); });
    (v.images || []).forEach(function (b, i) { nimm(b, "image-" + (i + 1)); });
    (v.shapes || []).forEach(function (f, i) { nimm(f, "shape-" + (i + 1)); });
    if (v.qr) v.qr.id = "qr";
    return v;
  }

  /* Layer order bottom to top, as ids — the same rule as ebenen() in
   * vorlagen_kern.py: without "order" photos, overlay, texts, QR; ids the
   * order does not know are dropped, layers it forgot go on top (the overlay
   * right above the last photo). */
  var STANDARD_RANG = { slot: 0, overlay: 1, image: 2, shape: 3, text: 4, qr: 5 };
  function ebenenListe(v) {
    var alle = [];
    (v.slots || []).forEach(function (s) { alle.push({ art: "slot", id: s.id }); });
    (v.texts || []).forEach(function (t) { alle.push({ art: "text", id: t.id }); });
    (v.images || []).forEach(function (b) { alle.push({ art: "image", id: b.id }); });
    (v.shapes || []).forEach(function (f) { alle.push({ art: "shape", id: f.id }); });
    alle.push({ art: "overlay", id: "overlay" });
    if (v.qr) alle.push({ art: "qr", id: "qr" });
    return alle;
  }
  function ebenenFolge(v) {
    var alle = ebenenListe(v);
    var standard = alle.map(function (e, i) { return [e, i]; }).sort(function (a, b) {
      return (STANDARD_RANG[a[0].art] - STANDARD_RANG[b[0].art]) || (a[1] - b[1]);
    }).map(function (p) { return p[0]; });
    if (!Array.isArray(v.order)) return standard.map(function (e) { return e.id; });
    var nachId = {}, folge = [], gesehen = {};
    alle.forEach(function (e) { nachId[e.id] = e; });
    v.order.forEach(function (id) {
      id = String(id);
      if (nachId[id] && !gesehen[id]) { folge.push(nachId[id]); gesehen[id] = true; }
    });
    standard.forEach(function (e) {
      if (gesehen[e.id]) return;
      if (e.art === "overlay") {
        var letzte = -1;
        folge.forEach(function (f, i) { if (f.art === "slot") letzte = i; });
        folge.splice(letzte + 1, 0, e);
      } else {
        folge.push(e);
      }
    });
    return folge.map(function (e) { return e.id; });
  }
  function verschiebe(o, id, ziel) {
    var rest = o.filter(function (x) { return x !== id; });
    if (rest.length === o.length) return o.slice();
    ziel = Math.max(0, Math.min(rest.length, ziel));
    rest.splice(ziel, 0, id);
    return rest;
  }
  function nachVorn(o, id) { return verschiebe(o, id, o.indexOf(id) + 1); }
  function nachHinten(o, id) { return verschiebe(o, id, Math.max(0, o.indexOf(id) - 1)); }
  function ganzVorn(o, id) { return verschiebe(o, id, o.length); }
  function ganzHinten(o, id) { return verschiebe(o, id, 0); }

  /* Rotation in degrees, clockwise, 0 <= r < 360 — as vorlagen_kern.py stores
   * it. With Shift the handle snaps to 45° steps; without, it still catches
   * the right angles within 2°, because a slot that is meant to be straight
   * should not end up at 89°. */
  function rasteWinkel(w, umschalt) {
    w = ((Number(w) || 0) % 360 + 360) % 360;
    if (umschalt) return (Math.round(w / 45) * 45) % 360;
    var rechts = Math.round(w / 90) * 90;
    if (Math.abs(w - rechts) <= 2) return rechts % 360;
    return Math.round(w) % 360;
  }
  /* Angle of the pointer seen from the middle, 0° pointing up. */
  function zeigerWinkel(mx, my, px, py) {
    return Math.atan2(px - mx, -(py - my)) * 180 / Math.PI;
  }
  /* Resize from the bottom-right handle of an element turned by "winkel":
   * the pointer's movement is taken along the element's own axes, and the
   * opposite (top-left) corner stays where it is on the sheet. */
  function groesseGedreht(a, dx, dy, winkel, min) {
    var r = (winkel || 0) * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    var lx = dx * c + dy * s, ly = -dx * s + dy * c;
    return neueGroesse(a, Math.max(min, Math.round(a.w + lx)), Math.max(min, Math.round(a.h + ly)), winkel);
  }
  /* The element at size w×h with its top-left corner (turned) unmoved. */
  function neueGroesse(a, w, h, winkel) {
    var r = (winkel || 0) * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    var cx = a.x + a.w / 2, cy = a.y + a.h / 2;
    var ex = cx - (a.w / 2) * c + (a.h / 2) * s, ey = cy - (a.w / 2) * s - (a.h / 2) * c;
    var nx = ex + (w / 2) * c - (h / 2) * s, ny = ey + (w / 2) * s + (h / 2) * c;
    return { x: Math.round(nx - w / 2), y: Math.round(ny - h / 2), w: w, h: h };
  }

  /* What an element covers on the sheet: the axis-aligned hull of its turned
   * box. Aligning and snapping go by what is seen, not by the stored x/y. */
  function sichtbar(b) {
    var r = (b.r || 0) * Math.PI / 180, c = Math.abs(Math.cos(r)), s = Math.abs(Math.sin(r));
    var w = b.w * c + b.h * s, h = b.w * s + b.h * c;
    var mx = b.x + b.w / 2, my = b.y + b.h / 2;
    return { x: mx - w / 2, y: my - h / 2, w: w, h: h };
  }
  function huelle(boxen) {
    var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    boxen.forEach(function (b) {
      var s = sichtbar(b);
      x1 = Math.min(x1, s.x); y1 = Math.min(y1, s.y);
      x2 = Math.max(x2, s.x + s.w); y2 = Math.max(y2, s.y + s.h);
    });
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
  }
  /* Moves ({id: [dx, dy]}) that line the boxes up on "bezug" — the sheet
   * for one element, the hull of the selection for several. */
  function ausrichten(boxen, wie, bezug) {
    var aus = {};
    boxen.forEach(function (b) {
      var s = sichtbar(b), dx = 0, dy = 0;
      if (wie === "links") dx = bezug.x - s.x;
      else if (wie === "rechts") dx = bezug.x + bezug.w - (s.x + s.w);
      else if (wie === "hmitte") dx = bezug.x + bezug.w / 2 - (s.x + s.w / 2);
      else if (wie === "oben") dy = bezug.y - s.y;
      else if (wie === "unten") dy = bezug.y + bezug.h - (s.y + s.h);
      else if (wie === "vmitte") dy = bezug.y + bezug.h / 2 - (s.y + s.h / 2);
      aus[b.id] = [Math.round(dx), Math.round(dy)];
    });
    return aus;
  }
  /* Equal gaps between three or more boxes; the outer two stay put. */
  function verteilen(boxen, achse) {
    var aus = {};
    var x = achse === "x";
    var liste = boxen.map(function (b) { return { id: b.id, s: sichtbar(b) }; });
    liste.sort(function (a, b) { return x ? a.s.x - b.s.x : a.s.y - b.s.y; });
    liste.forEach(function (e) { aus[e.id] = [0, 0]; });
    if (liste.length < 3) return aus;
    var anfang = x ? liste[0].s.x : liste[0].s.y;
    var letzte = liste[liste.length - 1].s;
    var ende = x ? letzte.x + letzte.w : letzte.y + letzte.h;
    var summe = liste.reduce(function (n, e) { return n + (x ? e.s.w : e.s.h); }, 0);
    var luecke = (ende - anfang - summe) / (liste.length - 1);
    var pos = anfang;
    liste.forEach(function (e) {
      var d = Math.round(pos - (x ? e.s.x : e.s.y));
      aus[e.id] = x ? [d, 0] : [0, d];
      pos += (x ? e.s.w : e.s.h) + luecke;
    });
    return aus;
  }
  /* Lines worth snapping to: edges and middles of the sheet and of every
   * other (visible) element. */
  function fanglinien(blatt, andere) {
    var xs = [blatt.x, blatt.x + blatt.w / 2, blatt.x + blatt.w];
    var ys = [blatt.y, blatt.y + blatt.h / 2, blatt.y + blatt.h];
    andere.forEach(function (b) {
      var s = sichtbar(b);
      xs.push(s.x, s.x + s.w / 2, s.x + s.w);
      ys.push(s.y, s.y + s.h / 2, s.y + s.h);
    });
    return { xs: xs, ys: ys };
  }
  /* The correction that brings the moving hull's nearest edge or middle onto
   * a line, if one is within "toleranz" — and which lines it now touches. */
  function einrasten(b, linien, toleranz) {
    function achse(kanten, ziele) {
      var best = null;
      kanten.forEach(function (k) {
        ziele.forEach(function (z) {
          var d = z - k;
          if (Math.abs(d) <= toleranz && (best === null || Math.abs(d) < Math.abs(best))) best = d;
        });
      });
      if (best === null) return { d: 0, linien: [] };
      var getroffen = [];
      kanten.forEach(function (k) {
        ziele.forEach(function (z) {
          if (Math.abs(k + best - z) < 0.5 && getroffen.indexOf(z) < 0) getroffen.push(z);
        });
      });
      return { d: Math.round(best), linien: getroffen.sort(function (a, c) { return a - c; }) };
    }
    var ax = achse([b.x, b.x + b.w / 2, b.x + b.w], linien.xs);
    var ay = achse([b.y, b.y + b.h / 2, b.y + b.h], linien.ys);
    return { dx: ax.d, dy: ay.d, linienX: ax.linien, linienY: ay.linien };
  }

  /* Dye-sublimation printers print borderless by printing a little larger
   * than the sheet and cutting — what is near the edge may be cut off. The
   * booth knows no per-printer figure for that, so the safe area is a share
   * of each side, 3 % unless the template says otherwise ("safe_area"). */
  function sichererBereich(v) {
    var p = v.safe_area === undefined || v.safe_area === null || v.safe_area === "" ? 3 : Number(v.safe_area) || 0;
    var W = v.canvas[0], H = v.canvas[1];
    var rx = Math.round(W * p / 100), ry = Math.round(H * p / 100);
    return { x: rx, y: ry, w: W - 2 * rx, h: H - 2 * ry };
  }
  function ausserhalbSicher(b, bereich) {
    var s = sichtbar(b);
    return s.x < bereich.x - 0.5 || s.y < bereich.y - 0.5 ||
      s.x + s.w > bereich.x + bereich.w + 0.5 || s.y + s.h > bereich.y + bereich.h + 0.5;
  }
  /* Where the sheet is cut after printing: the 2×6" strip is printed twice
   * side by side on a 10×15 sheet and cut down the middle. */
  function schnitte(v) {
    return v.paper === "streifen" ? [Math.round(v.canvas[0] / 2)] : [];
  }
  function kreuztSchnitt(b, xs) {
    var s = sichtbar(b);
    return xs.some(function (x) { return s.x < x && s.x + s.w > x; });
  }
  /* Copy and paste within one template (Ctrl/Cmd+C, Ctrl/Cmd+V). The copies
   * land 40 px further right and down, stay on the sheet, get ids of their
   * own and respect each list's limit; the QR code exists once and is not
   * copied. Returns the new elements; whatever did not fit is left out. */
  var KOPIER_LISTEN = { slot: ["slots", 12], text: ["texts", 6], image: ["images", 10], shape: ["shapes", 20] };
  function kopienEinfuegen(v, ablage) {
    var neu = [];
    (ablage || []).forEach(function (a) {
      var ziel = KOPIER_LISTEN[a.art];
      if (!ziel) return;
      v[ziel[0]] = v[ziel[0]] || [];
      if (v[ziel[0]].length >= ziel[1]) return;
      var e = JSON.parse(JSON.stringify(a.el));
      delete e.id; delete e.name; delete e.locked;
      e.x = Math.max(0, Math.min((e.x || 0) + 40, v.canvas[0] - (e.w || 0)));
      e.y = Math.max(0, Math.min((e.y || 0) + 40, v.canvas[1] - (e.h || 0)));
      v[ziel[0]].push(e);
      neu.push({ art: a.art, el: e });
    });
    sichereIds(v);
    return neu;
  }

  // ── Text that stays in its field (vorlagen_render.textsatz) ──
  // The same rule as the renderer, step for step, so the sheet shows what
  // is printed: a line too wide for its field is set smaller (down to 60 %),
  // then in two lines if the field is tall enough, then smaller still —
  // never cut. ``mass`` measures: breite(line, size) in px and
  // metrik(size) = [ascent, descent]; the editor measures with a canvas.
  var SCHRUMPF_MIN = 0.6, SCHRUMPF_BODEN = 0.4, UMBRUCH_ABSTAND = 1.15, SEITENLUFT = 0.25;
  function sichererRand(v) {
    // Every paper of vorlagen_kern.PAPIERE keeps 4 mm; templates are 300 dpi.
    return Math.round(4 * 300 / 25.4) + Math.max(0, Number(v.anschnitt_px) || 0);
  }
  function textKaesten(v) {
    var q = v.qr;
    return q && !q.hidden ? [[q.x || 0, q.y || 0, (q.x || 0) + (q.size || 220), (q.y || 0) + (q.size || 220)]] : [];
  }
  function textWaagrecht(t, breite, rand, kaesten) {
    var lo = rand, hi = breite - rand, roh = Number(t.x) || 0;
    if (roh > hi || roh < lo) return [lo, hi, roh > hi ? hi : lo, roh > hi ? "right" : "left"];
    var x = roh, y = Number(t.y) || 0, g = Number(t.size) || 48, luft = Math.round(g * SEITENLUFT);
    (kaesten || []).forEach(function (k) {
      if (k[1] < y + g * 1.2 && k[3] > y && !(k[0] <= x && x <= k[2])) {
        if (k[0] > x) hi = Math.min(hi, k[0] - luft); else lo = Math.max(lo, k[2] + luft);
      }
    });
    var a = t.align || "left", w = Number(t.w) > 0 ? Number(t.w) : 0, l, r;
    if (w) { l = a === "center" ? x - w / 2 : a === "right" ? x - w : x; r = l + w; }
    else if (a === "center") { var halb = Math.max(0, Math.min(x - lo, hi - x)); l = x - halb; r = x + halb; }
    else if (a === "right") { l = lo; r = x; }
    else { l = x; r = hi; }
    l = Math.max(l, lo);
    return [l, Math.max(Math.min(r, hi), l), x, a];
  }
  function textFeld(t, v) {
    var W = v.canvas[0], H = v.canvas[1], rand = sichererRand(v), kaesten = textKaesten(v);
    var wa = textWaagrecht(t, W, rand, kaesten), oben = Number(t.y) || 0, unten = H - rand;
    if (Number(t.h) > 0) unten = Math.min(unten, oben + Number(t.h));
    else {
      var unter = function (l, r, top) { if (top > oben && l < wa[1] && r > wa[0]) unten = Math.min(unten, top); };
      (v.texts || []).forEach(function (e) {
        if (e === t || e.hidden) return;
        var b = textWaagrecht(e, W, rand, kaesten);
        unter(b[0], b[1], Number(e.y) || 0);
      });
      (v.slots || []).forEach(function (e) { if (!e.hidden) unter(e.x, e.x + e.w, e.y); });
      if (v.qr && !v.qr.hidden) unter(v.qr.x, v.qr.x + (v.qr.size || 220), v.qr.y);
    }
    return { links: wa[0], rechts: wa[1], oben: oben, unten: unten, x: wa[2], align: wa[3],
             verschoben: wa[2] !== (Number(t.x) || 0) };
  }
  function textUmbrechen(absaetze, breite, platz, zwei) {
    if (absaetze.length === 1) {
      var woerter = absaetze[0].split(" "), beste = null, besteB = null;
      for (var i = 1; i < woerter.length; i++) {
        var a = woerter.slice(0, i).join(" ").trim(), b = woerter.slice(i).join(" ").trim();
        if (!a || !b) continue;
        var w = Math.max(breite(a), breite(b));
        if (w <= platz && (besteB === null || w < besteB)) { beste = [a, b]; besteB = w; }
      }
      if (beste || zwei) return beste;
    }
    var zeilen = [];
    for (var p = 0; p < absaetze.length; p++) {
      var aktuell = "", teile = [];
      absaetze[p].split(" ").forEach(function (wort) {
        if (teile.length && teile[teile.length - 1] === "&") teile[teile.length - 1] += " " + wort;
        else teile.push(wort);
      });
      for (var j = 0; j < teile.length; j++) {
        var probe = aktuell ? aktuell + " " + teile[j] : teile[j];
        if (breite(probe) <= platz) aktuell = probe;
        else if (!aktuell || breite(teile[j]) > platz) return null;
        else { zeilen.push(aktuell); aktuell = teile[j]; }
      }
      zeilen.push(aktuell);
    }
    return zeilen;
  }
  function textSatz(t, wert, v, mass) {
    var g = Number(t.size) || 48, feld = textFeld(t, v), platz = Math.max(1, feld.rechts - feld.links);
    var lh = Number(t.line_height) > 0 ? Number(t.line_height) : 0;
    var abstandSatz = lh ? Math.round(g * Math.max(0.8, Math.min(3, lh))) : 0;
    var absaetze = (abstandSatz || /\n/.test(wert)) ? wert.split("\n") : [wert];
    var breite = function (z, s) { return z ? mass.breite(z, s) : 0; };
    var breitest = function (zeilen, s) { return Math.max.apply(null, zeilen.map(function (z) { return breite(z, s); })); };
    var abstand = function (s) { return Math.round(s * Math.max(0.8, Math.min(3, lh || UMBRUCH_ABSTAND))); };
    var hoehe = function (n, s) { var m = mass.metrik(s); return (n - 1) * abstand(s) + m[0] + m[1]; };
    var natur = breitest(absaetze, g);
    var aus = { size: g, zeilen: absaetze, abstand: abstandSatz, x: Number(t.x) || 0, align: t.align || "left",
                geaendert: false, stufe: "", breite: natur, breiteGesetzt: natur, platz: platz, feld: feld };
    if (!wert.trim() || (natur <= platz + 0.5 && !feld.verschoben)) return aus;
    var passend = function (zeilen, hoechstens, mindestens) {
      var s = Math.max(1, Math.min(hoechstens, Math.floor(hoechstens * platz / Math.max(1, breitest(zeilen, hoechstens)))));
      while (s > mindestens && breitest(zeilen, s) > platz) s--;
      return s >= mindestens && breitest(zeilen, s) <= platz ? s : null;
    };
    var mehr = absaetze.length > 1;
    var fertig = function (s, zeilen, stufe, mehrzeilig) {
      aus.size = s; aus.zeilen = zeilen; aus.geaendert = true; aus.stufe = stufe; aus.align = feld.align;
      aus.x = feld.align === "center" ? (feld.links + feld.rechts) / 2 : feld.align === "right" ? feld.rechts : feld.links;
      aus.abstand = mehrzeilig ? abstand(s) : 0;
      aus.breiteGesetzt = breitest(zeilen, s);
      return aus;
    };
    var minimum = Math.max(1, Math.ceil(g * SCHRUMPF_MIN));
    if (natur <= platz + 0.5) return fertig(g, absaetze, "verschoben", mehr);
    var s = passend(absaetze, g, minimum);
    if (s) return fertig(s, absaetze, "kleiner", mehr);
    var hoch = feld.unten - feld.oben;
    var umbrochen = function (s, zwei) {
      var z = textUmbrechen(absaetze, function (x) { return breite(x, s); }, platz, zwei);
      return z && z.length > absaetze.length && hoehe(z.length, s) <= hoch ? z : null;
    };
    var arten = absaetze.length === 1 ? [true, false] : [false];
    for (var k = 0; k < arten.length; k++) {
      for (s = g; s >= minimum; s--) {
        var z = umbrochen(s, arten[k]);
        if (z) return fertig(s, z, "zweizeilig", true);
      }
    }
    var boden = Math.max(1, Math.floor(g * SCHRUMPF_BODEN));
    var einzeilig = passend(absaetze, minimum, 1) || 1;
    for (s = minimum - 1; s > einzeilig; s--) {
      var z2 = umbrochen(s, false);
      if (z2) return fertig(s, z2, s >= boden ? "zweizeilig" : "sehr_klein", true);
    }
    return fertig(einzeilig, absaetze, einzeilig >= boden ? "kleiner" : "sehr_klein", mehr);
  }
  // </editor-kern>

  var PAPIERE = {
    "10x15": { name: "Postkarte 10×15 hoch", canvas: [1200, 1800] },
    "10x15-quer": { name: "Postkarte 10×15 quer", canvas: [1800, 1200] },
    "streifen": { name: "Streifen 2×6″ (doppelt)", canvas: [1200, 1800] },
    "quadrat": { name: "Quadrat 10×10", canvas: [1200, 1200] },
    "13x18": { name: "13×18 hoch", canvas: [1535, 2126] },
    "13x18-quer": { name: "13×18 quer", canvas: [2126, 1535] },
    // Same list as vorlagen_kern.PAPIERE; a single strip is printed twice on 10×15.
    "streifen-2x6": { name: "Fotostreifen 5×15 (2×6″), zweimal auf 10×15", canvas: [600, 1800] },
    "15x20": { name: "15×20 hoch (6×8″)", canvas: [1800, 2400] },
    "15x20-quer": { name: "15×20 quer (6×8″)", canvas: [2400, 1800] },
    "15x23": { name: "15×23 hoch (6×9″)", canvas: [1800, 2700] },
    "11x20": { name: "11×20 hoch (4,5×8″, QW410)", canvas: [1350, 2400] },
    "20x25": { name: "20×25 hoch (8×10″, DS820)", canvas: [2400, 3000] }
  };

  // The house style ("clean" in compose.py) fills the @-tokens on screen; the
  // booth's own style replaces it on the print.
  var STIL_FARBEN = { "@hintergrund": "#0f0d09", "@schrift": "#f5f3f0", "@gedaempft": "#898580", "@akzent": "#e7ad59" };
  var BEISPIEL = { headline: "Mia & Tom", subline: "20.09.2026", event_name: "Mia & Tom", date: "20.09.2026" };

  function ikon(pfad) {
    return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + pfad + "</svg>";
  }
  var AUGE = ikon('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>');
  var AUGE_ZU = ikon('<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2"/>' +
    '<path d="M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/>');
  var SCHLOSS = ikon('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.7-1.5"/>');
  var SCHLOSS_ZU = ikon('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>');

  var TEXTE = {
    de: {
      undo: "Rückgängig", redo: "Wiederholen", addSlot: "+ Bildfeld", addText: "+ Text", addQr: "+ QR-Code",
      remove: "Entfernen", save: "Vorlage speichern", del: "Vorlage löschen", name: "Name",
      paper: "Blattformat", slot: "Bildfeld", text: "Textzeile", qr: "QR-Code", shot: "zeigt Aufnahme",
      x: "links", y: "oben", w: "Breite", h: "Höhe", size: "Größe", value: "Text",
      align: "Ausrichtung", aLeft: "links", aCenter: "zentriert", aRight: "rechts", bold: "fett",
      saved: "Gespeichert.", notSaved: "Nicht gespeichert: ", unsaved: "ungespeicherte Änderungen",
      confirmDel: "Diese Vorlage löschen?", nothing: "Nichts ausgewählt — ein Element auf dem Blatt anklicken.",
      hint: "Ziehen verschiebt, die Ecke zieht auf. Pfeiltasten schieben um 1, mit Umschalt um 10. " +
        "Umschalt+Klick oder ein Rahmen wählt mehrere. Beim Ziehen rastet es an Blatt und Nachbarn ein " +
        "(Alt hält es frei). Strg/Cmd+Z nimmt zurück, Umschalt+Strg/Cmd+Z wiederholt, Entf entfernt.",
      lastSlot: "Ein Bildfeld muss bleiben.", leave: "Die Vorlage hat ungespeicherte Änderungen.",
      full: "Mehr passt nicht auf eine Vorlage.",
      layers: "Ebenen", layerOverlay: "Rahmenbild + Stilrahmen", layerFrame: "Stilrahmen", shotShort: "Aufnahme",
      toFront: "Ganz nach vorn (Strg/Cmd+Umschalt+])", forward: "Eine Ebene nach vorn (Strg/Cmd+])",
      backward: "Eine Ebene nach hinten (Strg/Cmd+[)", toBack: "Ganz nach hinten (Strg/Cmd+Umschalt+[)",
      show: "Einblenden (wird gedruckt)", hide: "Ausblenden (wird nicht gedruckt)",
      lock: "Sperren", unlock: "Entsperren", layerName: "Name der Ebene",
      rotation: "Drehung (°)", resize: "Größe ändern",
      rotateHint: "Drehen — mit Umschalt in 45°-Schritten",
      preview: "Vorschau", previewStyle: "Stil der Vorschau", close: "Schließen",
      previewLoading: "Wird gerendert …",
      previewHint: "So druckt die Box diese Vorlage — gerendert vom selben Programmteil, mit Beispielbildern statt Fotos.",
      margin: "Druckrand", marginHint: "Sicheren Bereich und Schnittlinie ein- oder ausblenden",
      safeArea: "Sicherer Rand (% je Seite)", cut: "Hier wird nach dem Druck geschnitten",
      warnMargin: "liegt außerhalb des sicheren Bereichs; der Drucker kann es anschneiden.",
      warnCut: "liegt über der Schnittlinie des Streifens.",
      addShape: "+ Form", addImage: "+ Bild", noMedia: "Erst unter Box-Medien ein Bild hochladen.",
      shape: "Form", image: "Bild", kind: "Art", rect: "Rechteck", ellipse: "Ellipse", line: "Linie",
      fill: "Füllung", stroke: "Kontur", strokeWidth: "Strichstärke", radius: "Eckenradius", opacity: "Deckkraft (%)",
      file: "Datei", font: "Schrift", fontStyle: "wie der Stil der Box", color: "Farbe", lineHeight: "Zeilenabstand",
      onlyWithQr: "Nur drucken, wenn ein QR-Code gedruckt wird", fieldW: "Feldbreite", fieldH: "Feldhöhe",
      fieldHint: "0 = bis zum Rand bzw. bis zur nächsten Zeile darunter",
      fitKleiner: "Passt mit den Beispielnamen nicht in sein Feld: gedruckt in {s} px statt {g} px.",
      fitZwei: "Passt mit den Beispielnamen nicht in eine Zeile: gedruckt zweizeilig in {s} px.",
      fitVerschoben: "Steht außerhalb des sicheren Bereichs: wird an dessen Rand gedruckt.",
      fitKlein: "Passt nicht in sein Feld: nur {s} px — Feld vergrößern oder Schrift verkleinern.",
      none: "keine", custom: "eigene Farbe …", tInk: "Schriftfarbe (Stil)", tMuted: "gedämpft (Stil)",
      tAccent: "Akzent (Stil)", tBg: "Hintergrund (Stil)",
      several: "{n} Elemente ausgewählt — ausrichten, verteilen, gemeinsam verschieben (Pfeiltasten) oder entfernen.",
      alLeft: "Links ausrichten", alCenter: "Horizontal zentrieren", alRight: "Rechts ausrichten",
      alTop: "Oben ausrichten", alMiddle: "Vertikal zentrieren", alBottom: "Unten ausrichten",
      distX: "Horizontal gleichmäßig verteilen (ab 3)", distY: "Vertikal gleichmäßig verteilen (ab 3)",
      keys: "Tastenkürzel", copied: "{n} Element(e) kopiert — Strg/Cmd+V fügt ein.",
      keyList: [["Strg/Cmd+Z", "Rückgängig"], ["Umschalt+Strg/Cmd+Z, Strg/Cmd+Y", "Wiederholen"],
        ["Strg/Cmd+C", "Ausgewählte Elemente kopieren"], ["Strg/Cmd+V", "Einfügen (versetzt, ein Rückgängig-Schritt)"],
        ["Entf, Rücktaste", "Entfernen"], ["Pfeiltasten", "1 px verschieben, mit Umschalt 10 px"],
        ["Strg/Cmd+] / [", "Eine Ebene nach vorn / hinten"], ["Umschalt+Strg/Cmd+] / [", "Ganz nach vorn / hinten"],
        ["Umschalt+Klick, Rahmen ziehen", "Mehrere auswählen"], ["Alt beim Ziehen", "Ohne Einrasten"],
        ["?", "Diese Übersicht"]]
    },
    en: {
      undo: "Undo", redo: "Redo", addSlot: "+ Photo slot", addText: "+ Text", addQr: "+ QR code",
      remove: "Remove", save: "Save template", del: "Delete template", name: "Name",
      paper: "Sheet", slot: "Photo slot", text: "Text line", qr: "QR code", shot: "shows shot",
      x: "left", y: "top", w: "width", h: "height", size: "size", value: "Text",
      align: "Alignment", aLeft: "left", aCenter: "centre", aRight: "right", bold: "bold",
      saved: "Saved.", notSaved: "Not saved: ", unsaved: "unsaved changes",
      confirmDel: "Delete this template?", nothing: "Nothing selected — click an element on the sheet.",
      hint: "Drag to move, the corner resizes. Arrow keys move by 1, with Shift by 10. " +
        "Shift+click or a frame selects several. Dragging snaps to sheet and neighbours " +
        "(Alt keeps it free). Ctrl/Cmd+Z undoes, Shift+Ctrl/Cmd+Z redoes, Delete removes.",
      lastSlot: "One photo slot has to stay.", leave: "The template has unsaved changes.",
      full: "No more fits on one template.",
      layers: "Layers", layerOverlay: "Overlay picture + style frame", layerFrame: "Style frame", shotShort: "shot",
      toFront: "Bring to front (Ctrl/Cmd+Shift+])", forward: "Bring forward (Ctrl/Cmd+])",
      backward: "Send backward (Ctrl/Cmd+[)", toBack: "Send to back (Ctrl/Cmd+Shift+[)",
      show: "Show (is printed)", hide: "Hide (is not printed)",
      lock: "Lock", unlock: "Unlock", layerName: "Layer name",
      rotation: "Rotation (°)", resize: "Resize",
      rotateHint: "Rotate — with Shift in 45° steps",
      preview: "Preview", previewStyle: "Preview style", close: "Close",
      previewLoading: "Rendering …",
      previewHint: "This is how the booth prints the template — drawn by the same renderer, with sample pictures instead of photos.",
      margin: "Print margin", marginHint: "Show or hide the safe area and the cut line",
      safeArea: "Safe margin (% per side)", cut: "The sheet is cut here after printing",
      warnMargin: "lies outside the safe area; the printer may cut into it.",
      warnCut: "crosses the cut line of the strip.",
      addShape: "+ Shape", addImage: "+ Picture", noMedia: "Upload a picture under booth media first.",
      shape: "Shape", image: "Picture", kind: "Kind", rect: "Rectangle", ellipse: "Ellipse", line: "Line",
      fill: "Fill", stroke: "Outline", strokeWidth: "Stroke width", radius: "Corner radius", opacity: "Opacity (%)",
      file: "File", font: "Font", fontStyle: "as the booth's style", color: "Colour", lineHeight: "Line spacing",
      onlyWithQr: "Print only when a QR code is printed", fieldW: "Field width", fieldH: "Field height",
      fieldHint: "0 = up to the margin or the next line below",
      fitKleiner: "Does not fit its field with the sample names: printed at {s} px instead of {g} px.",
      fitZwei: "Does not fit one line with the sample names: printed in two lines at {s} px.",
      fitVerschoben: "Lies outside the safe area: printed at its edge.",
      fitKlein: "Does not fit its field: only {s} px — make the field larger or the type smaller.",
      none: "none", custom: "own colour …", tInk: "Text colour (style)", tMuted: "muted (style)",
      tAccent: "Accent (style)", tBg: "Background (style)",
      several: "{n} elements selected — align, distribute, move together (arrow keys) or remove.",
      alLeft: "Align left", alCenter: "Centre horizontally", alRight: "Align right",
      alTop: "Align top", alMiddle: "Centre vertically", alBottom: "Align bottom",
      distX: "Distribute horizontally (3 or more)", distY: "Distribute vertically (3 or more)",
      keys: "Keyboard shortcuts", copied: "{n} element(s) copied — Ctrl/Cmd+V pastes.",
      keyList: [["Ctrl/Cmd+Z", "Undo"], ["Shift+Ctrl/Cmd+Z, Ctrl/Cmd+Y", "Redo"],
        ["Ctrl/Cmd+C", "Copy the selected elements"], ["Ctrl/Cmd+V", "Paste (offset, one undo step)"],
        ["Delete, Backspace", "Remove"], ["Arrow keys", "Move by 1 px, with Shift by 10 px"],
        ["Ctrl/Cmd+] / [", "One layer forward / backward"], ["Shift+Ctrl/Cmd+] / [", "To front / to back"],
        ["Shift+click, drag a frame", "Select several"], ["Alt while dragging", "No snapping"],
        ["?", "This overview"]]
    }
  };

  var CSS = ".vle{display:grid;gap:12px}" +
    ".vle-leiste{display:flex;flex-wrap:wrap;gap:6px;align-items:center}" +
    ".vle-leiste .btn{min-height:36px;padding:0 12px}" +
    ".vle-trenner{width:1px;align-self:stretch;background:var(--color-border,var(--line,#ccc));margin:0 4px}" +
    ".vle-haupt{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}" +
    ".vle-blattbox{position:relative;flex:0 0 auto}" +
    ".vle-blattbox{padding:30px;overflow:hidden;border-radius:8px;background:var(--color-surface-2,rgba(127,127,127,.08))}" +
    ".vle-blatt{position:relative;user-select:none;-webkit-user-select:none;" +
    "box-shadow:0 2px 12px rgba(0,0,0,.35);touch-action:none;outline:none}" +
    // What sticks out of the sheet is not printed: shown, but veiled.
    ".vle-blatt::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:3;" +
    "box-shadow:0 0 0 3000px rgba(120,120,120,.55)}" +
    ".vle-dreh{position:absolute;left:50%;top:-26px;width:14px;height:14px;margin-left:-7px;border-radius:50%;" +
    "background:#fff;border:2px solid #e7ad59;cursor:grab;box-shadow:0 0 0 2px rgba(0,0,0,.25)}" +
    ".vle-dreh::after{content:'';position:absolute;left:50%;top:12px;width:2px;height:12px;margin-left:-1px;background:#e7ad59}" +
    ".vle-el{position:absolute;box-sizing:border-box;cursor:move}" +
    ".vle-an{outline:2px solid #e7ad59;outline-offset:1px}" +
    ".vle-griff{position:absolute;right:-8px;bottom:-8px;width:16px;height:16px;border-radius:4px;" +
    "background:#e7ad59;cursor:nwse-resize;box-shadow:0 0 0 2px rgba(0,0,0,.35)}" +
    ".vle-seite{flex:1 1 300px;min-width:280px;max-width:440px;display:grid;gap:10px;align-content:start}" +
    ".vle-gruppe{border:1px solid var(--color-border,var(--line,#e5e2dd));border-radius:8px;padding:10px;display:grid;gap:8px}" +
    ".vle-gruppe>b{font-size:.85rem}" +
    ".vle-zwei{display:grid;grid-template-columns:1fr 1fr;gap:8px}" +
    ".vle-feld{display:grid;gap:4px;font-size:.8rem;color:var(--color-text-muted,var(--muted,#555))}" +
    ".vle-feld .form-control{min-height:36px}" +
    ".vle-hinweis{margin:8px 0 0;font-size:.78rem;color:var(--color-text-muted,var(--muted,#777));max-width:560px}" +
    ".vle-satz{color:var(--color-warning,#b26a00)}" +
    ".vle-el[data-satz]{outline:1px dashed var(--color-warning,#b26a00);outline-offset:2px}" +
    ".vle-meldung{margin:0;font-size:.8rem;min-height:1.2em}" +
    ".vle-schmutzig{font-size:.78rem;color:var(--color-warning,#b26a00)}" +
    ".vle-ebenen-kopf{display:flex;justify-content:space-between;align-items:center;gap:8px}" +
    ".vle-ebenen-knoepfe{display:flex;gap:4px}" +
    ".vle-ebenen-knoepfe .btn{min-height:30px;min-width:30px;padding:0 6px;font-size:.9rem}" +
    ".vle-ebenen{list-style:none;margin:0;padding:0;display:grid;gap:2px;max-height:260px;overflow:auto}" +
    ".vle-ebene{display:flex;align-items:center;gap:4px;padding:4px 6px;border-radius:6px;cursor:pointer;font-size:.82rem}" +
    ".vle-ebene:hover{background:var(--color-surface-2,rgba(127,127,127,.12))}" +
    ".vle-ebene-an{background:var(--color-primary-bg,rgba(231,173,89,.2))}" +
    ".vle-ebene-ziel{box-shadow:inset 0 2px 0 var(--color-primary,#e7ad59)}" +
    ".vle-ebene-fest{color:var(--color-text-muted,var(--muted,#777));cursor:grab}" +
    ".vle-ebene-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".vle-schalter{display:inline-grid;place-items:center;width:28px;height:28px;border:0;border-radius:6px;" +
    "background:transparent;color:inherit;cursor:pointer;opacity:.55}" +
    ".vle-schalter:hover{opacity:1;background:rgba(127,127,127,.15)}" +
    ".vle-schalter-an{opacity:1;color:var(--color-primary,#e7ad59)}" +
    ".vle-ikon{min-width:36px;padding:0 8px!important;display:inline-grid;place-items:center}" +
    ".vle-linie{position:absolute;background:#ff3d7f;z-index:4;pointer-events:none}" +
    ".vle-sicher{position:absolute;z-index:4;pointer-events:none;border:1px dashed rgba(80,200,255,.9)}" +
    ".vle-schnitt{position:absolute;z-index:4;pointer-events:none;top:0;bottom:0;width:0;" +
    "border-left:1px dashed rgba(255,255,255,.9);box-shadow:0 0 0 1px rgba(0,0,0,.35)}" +
    ".vle-warnung{outline:2px solid #e5484d!important;outline-offset:2px}" +
    ".vle-warnliste{display:grid;gap:4px;border:1px solid #e5484d;border-radius:8px;padding:8px;" +
    "background:var(--color-danger-bg,rgba(229,72,77,.08))}" +
    ".vle-warnzeile{all:unset;cursor:pointer;font-size:.8rem;color:var(--color-danger,#b3261e)}" +
    ".vle-knopf-an{box-shadow:inset 0 0 0 2px var(--color-primary,#e7ad59)}" +
    ".vle-vorschau{margin:auto;border:0;border-radius:12px;padding:16px;max-width:min(92vw,760px);" +
    "background:var(--color-surface,#fff);color:inherit;box-shadow:0 20px 60px rgba(0,0,0,.4)}" +
    ".vle-vorschau::backdrop{background:rgba(0,0,0,.55)}" +
    ".vle-vorschau-kopf{display:flex;gap:10px;align-items:center;margin-bottom:12px}" +
    ".vle-tasten{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:0;min-width:min(520px,80vw)}" +
    ".vle-tasten dt{font-family:ui-monospace,monospace;white-space:nowrap}.vle-tasten dd{margin:0}" +
    ".vle-vorschau-kopf b{flex:1}.vle-vorschau-kopf .form-control{max-width:180px;min-height:36px}" +
    ".vle-vorschau img{display:block;max-width:100%;max-height:72vh;margin:0 auto;box-shadow:0 2px 12px rgba(0,0,0,.3)}" +
    ".vle-rahmen{position:absolute;z-index:4;pointer-events:none;border:1px dashed #e7ad59;background:rgba(231,173,89,.12)}";

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") e.style.cssText = attrs[k];
      else if (k === "class") e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function neueVorlage(papier) {
    var c = PAPIERE[papier].canvas;
    return {
      name: "Eigene Vorlage", paper: papier, canvas: c.slice(),
      slots: [{ x: 60, y: 60, w: c[0] - 120, h: c[1] - 400, shot: 0, radius: "@rund" }],
      texts: [
        { x: 60, y: c[1] - 260, size: 58, bold: true, align: "left", value: "{headline}", color: "@schrift" },
        { x: 60, y: c[1] - 182, size: 36, bold: false, align: "left", value: "{subline}", color: "@gedaempft" }
      ],
      qr: { x: c[0] - 260, y: c[1] - 270, size: 200 }
    };
  }

  function grenze(wert, klein, gross) { return Math.max(klein, Math.min(gross, wert)); }
  function farbe(wert, fallback) { return STIL_FARBEN[wert] || (/^#[0-9a-f]{6}$/i.test(wert || "") ? wert : fallback); }
  function beispielText(wert) {
    return String(wert || "").replace(/\{(\w+)\}/g, function (_, k) { return BEISPIEL[k] !== undefined ? BEISPIEL[k] : ""; });
  }
  function stilEinmal() {
    if (document.getElementById("vleStil")) return;
    var s = el("style", { id: "vleStil" });
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  var aktiv = null;

  function start(opt) {
    stilEinmal();
    var sprache = String(opt.sprache || document.documentElement.lang || "de").slice(0, 2);
    var T = TEXTE[sprache] || TEXTE.de;
    var wurzel = opt.wurzel;
    var v = sichereIds(JSON.parse(JSON.stringify(opt.vorlage || neueVorlage("10x15"))));
    v.order = ebenenFolge(v);
    var auswahl = v.slots[0] ? v.slots[0].id : null;
    // Several elements can be selected (Shift+click, frame); "auswahl" is the
    // one the side panel and the handles belong to.
    var markiert = auswahl ? [auswahl] : [];
    var randZeigen = true;
    var skala = 1;
    var verlauf = new Verlauf(100);
    var gespeichert = JSON.stringify(v);
    verlauf.merke(gespeichert);

    wurzel.innerHTML = "";
    var rahmen = el("div", { class: "vle" });
    var leiste = el("div", { class: "vle-leiste" });
    var haupt = el("div", { class: "vle-haupt" });
    var blattBox = el("div", { class: "vle-blattbox" });
    var blatt = el("div", { class: "vle-blatt", tabindex: "0" });
    var seite = el("div", { class: "vle-seite" });
    var blattSpalte = el("div", { style: "flex:0 0 auto" });
    blattBox.appendChild(blatt);
    blattSpalte.appendChild(blattBox);
    blattSpalte.appendChild(el("p", { class: "vle-hinweis" }, T.hint));
    haupt.appendChild(blattSpalte); haupt.appendChild(seite);
    rahmen.appendChild(leiste); rahmen.appendChild(haupt);
    wurzel.appendChild(rahmen);

    // ── finding elements ──
    function finde(id) {
      var i;
      for (i = 0; i < v.slots.length; i++) if (v.slots[i].id === id) return { art: "slot", el: v.slots[i], liste: v.slots, idx: i };
      for (i = 0; i < (v.texts || []).length; i++) if (v.texts[i].id === id) return { art: "text", el: v.texts[i], liste: v.texts, idx: i };
      for (i = 0; i < (v.images || []).length; i++) if (v.images[i].id === id) return { art: "image", el: v.images[i], liste: v.images, idx: i };
      for (i = 0; i < (v.shapes || []).length; i++) if (v.shapes[i].id === id) return { art: "shape", el: v.shapes[i], liste: v.shapes, idx: i };
      if (v.qr && id === "qr") return { art: "qr", el: v.qr, liste: null, idx: 0 };
      return null;
    }
    var medien = opt.medien || [], schriften = opt.schriften || [];
    function medienUrl(datei) {
      var m = medien.filter(function (x) { return x.file === datei; })[0];
      return m ? m.url : "";
    }
    // Uploaded fonts are loaded under a name of their own, so the sheet
    // measures and shows the very file the booth will print with.
    function schriftName(datei) { return "pbf-" + String(datei).toLowerCase().replace(/[^a-z0-9]+/g, "-"); }
    var schriftenGeladen = schriften.map(function (f) {
      if (!window.FontFace || !document.fonts) return Promise.resolve();
      var face = new FontFace(schriftName(f.file), "url(" + JSON.stringify(f.url) + ")");
      document.fonts.add(face);
      return face.load().catch(function () { return null; });
    });

    // ── history ──
    function zustand() { return JSON.stringify(v); }
    function geaendert() {
      v.order = ebenenFolge(v);
      verlauf.merke(zustand());
      zeichne();
    }
    function lade(z) {
      if (z === null) return;
      v = JSON.parse(z);
      if (auswahl && !finde(auswahl)) auswahl = null;
      zeichne();
    }
    function zurueck() { lade(verlauf.zurueck()); }
    function vor() { lade(verlauf.vor()); }

    // ── toolbar ──
    var knopfZurueck = knopf(T.undo, zurueck);
    var knopfVor = knopf(T.redo, vor);
    knopfZurueck.title = T.undo + " (Strg/Cmd+Z)";
    knopfVor.title = T.redo + " (Umschalt+Strg/Cmd+Z)";
    knopfZurueck.setAttribute("aria-label", T.undo); knopfVor.setAttribute("aria-label", T.redo);
    knopfZurueck.classList.add("vle-ikon"); knopfVor.classList.add("vle-ikon");
    knopfZurueck.innerHTML = ikon('<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>');
    knopfVor.innerHTML = ikon('<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>');
    var schmutzig = el("span", { class: "vle-schmutzig" });
    var knopfSpeichern = knopf(T.save, speichere, true);
    leiste.appendChild(knopfZurueck); leiste.appendChild(knopfVor);
    leiste.appendChild(el("span", { class: "vle-trenner" }));
    leiste.appendChild(knopf(T.addSlot, neuesBildfeld));
    leiste.appendChild(knopf(T.addText, neuerText));
    var knopfQr = knopf(T.addQr, neuerQr);
    leiste.appendChild(knopfQr);
    leiste.appendChild(knopf(T.addShape, neueForm));
    var knopfBild = knopf(T.addImage, neuesBild);
    knopfBild.disabled = !medien.length;
    knopfBild.title = medien.length ? T.addImage : T.noMedia;
    leiste.appendChild(knopfBild);
    leiste.appendChild(el("span", { class: "vle-trenner" }));
    leiste.appendChild(knopf(T.remove, entferne));
    leiste.appendChild(el("span", { class: "vle-trenner" }));
    var ausrichtKnoepfe = [];
    [["links", T.alLeft, '<path d="M4 3v18"/><rect x="7" y="6" width="12" height="4" rx="1"/><rect x="7" y="14" width="7" height="4" rx="1"/>'],
     ["hmitte", T.alCenter, '<path d="M12 3v18"/><rect x="5" y="6" width="14" height="4" rx="1"/><rect x="8" y="14" width="8" height="4" rx="1"/>'],
     ["rechts", T.alRight, '<path d="M20 3v18"/><rect x="5" y="6" width="12" height="4" rx="1"/><rect x="10" y="14" width="7" height="4" rx="1"/>'],
     ["oben", T.alTop, '<path d="M3 4h18"/><rect x="6" y="7" width="4" height="12" rx="1"/><rect x="14" y="7" width="4" height="7" rx="1"/>'],
     ["vmitte", T.alMiddle, '<path d="M3 12h18"/><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="8" width="4" height="8" rx="1"/>'],
     ["unten", T.alBottom, '<path d="M3 20h18"/><rect x="6" y="5" width="4" height="12" rx="1"/><rect x="14" y="10" width="4" height="7" rx="1"/>'],
     ["vx", T.distX, '<path d="M3 3v18M21 3v18"/><rect x="9" y="7" width="6" height="10" rx="1"/>'],
     ["vy", T.distY, '<path d="M3 3h18M3 21h18"/><rect x="7" y="9" width="10" height="6" rx="1"/>']
    ].forEach(function (k) {
      var b = el("button", { type: "button", class: "btn btn-secondary vle-ikon", title: k[1], "aria-label": k[1] });
      b.innerHTML = ikon(k[2]);
      if (k[0] === "vx" || k[0] === "vy") b.dataset.verteilen = "1";
      b.addEventListener("click", function () { richte(k[0]); });
      ausrichtKnoepfe.push(b);
      leiste.appendChild(b);
    });
    if (opt.vorschau) leiste.appendChild(knopf(T.preview, zeigeVorschau));
    var knopfRand = knopf(T.margin, function () { randZeigen = !randZeigen; zeichne(true); });
    knopfRand.title = T.marginHint;
    leiste.appendChild(knopfRand);
    var knopfTasten = knopf("?", zeigeTasten);
    knopfTasten.title = T.keys + " (?)"; knopfTasten.setAttribute("aria-label", T.keys);
    leiste.appendChild(knopfTasten);
    leiste.appendChild(el("span", { style: "flex:1" }));
    leiste.appendChild(schmutzig);
    leiste.appendChild(knopfSpeichern);

    function neuesBildfeld() {
      if (v.slots.length >= 12) return melde(T.full, true);
      var letzte = v.slots[v.slots.length - 1] || { x: 20, y: 20, w: 400, h: 300 };
      var s = { x: Math.min(letzte.x + 40, v.canvas[0] - letzte.w), y: Math.min(letzte.y + 40, v.canvas[1] - letzte.h),
                w: letzte.w, h: letzte.h, shot: v.slots.length, radius: "@rund" };
      v.slots.push(s);
      sichereIds(v);
      auswahl = s.id;
      geaendert();
    }
    function neuerText() {
      v.texts = v.texts || [];
      if (v.texts.length >= 6) return melde(T.full, true);
      var t = { x: 60, y: v.canvas[1] - 120, size: 40, bold: false, align: "left", value: "{event_name}", color: "@schrift" };
      v.texts.push(t);
      sichereIds(v);
      auswahl = t.id;
      geaendert();
    }
    function neuerQr() {
      if (v.qr) { auswahl = "qr"; return zeichne(); }
      v.qr = { x: v.canvas[0] - 260, y: v.canvas[1] - 270, size: 200 };
      sichereIds(v);
      auswahl = "qr";
      geaendert();
    }
    // The preview is rendered by the server with the booth's own renderer;
    // the editor only shows the picture it gets back.
    var STILE = ["clean", "hell", "geburtstag", "hochzeit", "feier", "firma"];
    function zeigeVorschau() {
      var dlg = el("dialog", { class: "vle-vorschau" });
      var kopf = el("div", { class: "vle-vorschau-kopf" });
      var wahl = el("select", { class: "form-control", "aria-label": T.previewStyle });
      STILE.forEach(function (s) { wahl.appendChild(el("option", { value: s }, s)); });
      var zu = knopf(T.close, function () { dlg.close(); });
      kopf.appendChild(el("b", {}, T.preview));
      kopf.appendChild(wahl);
      kopf.appendChild(zu);
      var bild = el("img", { alt: T.preview });
      var info = el("p", { class: "vle-hinweis" }, T.previewHint);
      dlg.appendChild(kopf); dlg.appendChild(bild); dlg.appendChild(info);
      document.body.appendChild(dlg);
      dlg.addEventListener("close", function () { if (bild.src) URL.revokeObjectURL(bild.src); dlg.remove(); });
      function lade() {
        info.textContent = T.previewLoading;
        Promise.resolve(opt.vorschau(JSON.parse(zustand()), wahl.value)).then(function (url) {
          if (bild.src) URL.revokeObjectURL(bild.src);
          bild.src = url;
          info.textContent = T.previewHint;
        }).catch(function (e) { info.textContent = T.notSaved + (e && e.message || e); });
      }
      wahl.addEventListener("change", lade);
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
      lade();
    }
    function neueForm() {
      v.shapes = v.shapes || [];
      if (v.shapes.length >= 20) return melde(T.full, true);
      var f = { kind: "rect", x: Math.round(v.canvas[0] / 2 - 200), y: Math.round(v.canvas[1] / 2 - 100),
                w: 400, h: 200, fill: "@akzent", stroke: "", stroke_width: 0, radius: 0, opacity: 100 };
      v.shapes.push(f);
      sichereIds(v);
      auswahl = f.id;
      geaendert();
    }
    function neuesBild() {
      if (!medien.length) return melde(T.noMedia, true);
      v.images = v.images || [];
      if (v.images.length >= 10) return melde(T.full, true);
      var b = { file: medien[0].file, x: Math.round(v.canvas[0] / 2 - 200), y: Math.round(v.canvas[1] / 2 - 200),
                w: 400, h: 400, opacity: 100 };
      v.images.push(b);
      sichereIds(v);
      auswahl = b.id;
      geaendert();
    }
    function entferne() {
      var weg = markiert.filter(function (id) { var f = finde(id); return f && !f.el.locked; });
      if (!weg.length) return;
      var slotsWeg = weg.filter(function (id) { return finde(id).art === "slot"; }).length;
      if (slotsWeg && slotsWeg >= v.slots.length) return melde(T.lastSlot, true);
      v.slots = v.slots.filter(function (s) { return weg.indexOf(s.id) < 0; });
      v.slots.forEach(function (s) { s.shot = Math.min(s.shot, v.slots.length - 1); });
      v.texts = (v.texts || []).filter(function (t) { return weg.indexOf(t.id) < 0; });
      if (v.images) v.images = v.images.filter(function (b) { return weg.indexOf(b.id) < 0; });
      if (v.shapes) v.shapes = v.shapes.filter(function (f) { return weg.indexOf(f.id) < 0; });
      if (weg.indexOf("qr") >= 0) v.qr = null;
      auswahl = null;
      geaendert();
    }

    // ── the sheet ──
    function zeichne(ohnePanel) {
      var maxBreite = Math.max(260, Math.min(560, (wurzel.clientWidth || 900) - 480));
      skala = Math.min(maxBreite / v.canvas[0], 640 / v.canvas[1]);
      blatt.style.width = Math.round(v.canvas[0] * skala) + "px";
      blatt.style.height = Math.round(v.canvas[1] * skala) + "px";
      blatt.style.background = farbe(v.background || "@hintergrund", "#0f0d09");
      blatt.innerHTML = "";
      // Code that picks one element only sets "auswahl"; the set follows.
      markiert = markiert.filter(function (id) { return finde(id); });
      if (auswahl && !finde(auswahl)) auswahl = null;
      if (!auswahl) markiert = [];
      else if (markiert.indexOf(auswahl) < 0) markiert = [auswahl];
      // DOM order is paint order: the layer list decides what lies on top.
      v.order = ebenenFolge(v);
      v.order.forEach(function (id) {
        var f = finde(id);
        if (!f || f.el.hidden) return;
        var knoten = f.art === "slot" ? bildfeldKnoten(f.el) : f.art === "text" ? textKnoten(f.el)
          : f.art === "image" ? bildKnoten(f.el) : f.art === "shape" ? formKnoten(f.el) : qrKnoten(f.el);
        // A locked layer lets clicks through to what lies beneath it.
        if (f.el.locked) { knoten.style.pointerEvents = "none"; knoten.style.cursor = "default"; }
        blatt.appendChild(knoten);
      });
      knopfZurueck.disabled = !verlauf.kannZurueck();
      knopfVor.disabled = !verlauf.kannVor();
      knopfQr.disabled = !!v.qr;
      ausrichtKnoepfe.forEach(function (b) {
        b.disabled = b.dataset.verteilen ? markiert.length < 3 : !markiert.length;
      });
      zeichneLinien();
      warnungen = pruefeRand();
      warnungen.forEach(function (w) {
        var k = blatt.querySelector('[data-id="' + w.id + '"]');
        if (k) k.classList.add("vle-warnung");
      });
      knopfRand.setAttribute("aria-pressed", randZeigen ? "true" : "false");
      knopfRand.classList.toggle("vle-knopf-an", randZeigen);
      if (randZeigen) {
        var sb = sichererBereich(v);
        blatt.appendChild(el("div", { class: "vle-sicher", title: T.safeArea, style: "left:" + sb.x * skala + "px;top:" +
          sb.y * skala + "px;width:" + sb.w * skala + "px;height:" + sb.h * skala + "px" }));
        schnitte(v).forEach(function (x) {
          blatt.appendChild(el("div", { class: "vle-schnitt", title: T.cut, style: "left:" + x * skala + "px" }));
        });
      }
      schmutzig.textContent = zustand() === gespeichert ? "" : "● " + T.unsaved;
      if (!ohnePanel) felder();
    }

    // Every element is a box turned about its middle — the point the
    // renderer turns it about (compose._lege). For text the box is the line:
    // advance width by ascent plus descent, measured like Pillow measures.
    function kasten(e, id, x, y, w, h, winkel) {
      e.className = "vle-el" + (markiert.indexOf(id) >= 0 ? " vle-an" : "");
      e.dataset.id = id;
      e.style.left = x * skala + "px";
      e.style.top = y * skala + "px";
      e.style.width = w * skala + "px";
      e.style.height = h * skala + "px";
      if (winkel) e.style.transform = "rotate(" + winkel + "deg)";
      return e;
    }
    function griff(e, id, mitGroesse) {
      if (id !== auswahl || markiert.length > 1) return;
      if (mitGroesse) {
        var g = el("div", { class: "vle-griff", title: T.resize });
        g.dataset.griff = id;
        e.appendChild(g);
      }
      var d = el("div", { class: "vle-dreh", title: T.rotateHint });
      d.dataset.dreh = id;
      e.appendChild(d);
    }
    function box(f) {
      var o = f.el;
      if (f.art === "qr") return { x: o.x, y: o.y, w: o.size, h: o.size };
      if (f.art === "text") {
        var m = textMass(o);
        var links = m.align === "center" ? m.x - m.w / 2 : m.align === "right" ? m.x - m.w : m.x;
        return { x: links, y: o.y, w: m.w, h: m.h };
      }
      return { x: o.x, y: o.y, w: o.w, h: o.h };
    }
    function schrift(t) {
      var fam = t.font || (t.display ? "display" : "system");
      if (/\.(ttf|otf)$/i.test(fam)) return { css: "'" + schriftName(fam) + "', Inter, sans-serif", gewicht: 400, datei: true };
      if (fam === "display") return { css: "Archivo, Inter, system-ui, sans-serif", gewicht: t.bold ? 700 : 600 };
      if (fam === "serif") return { css: "Georgia, 'DejaVu Serif', serif", gewicht: t.bold ? 700 : 400 };
      return { css: "Inter, system-ui, sans-serif", gewicht: t.bold ? 600 : 400 };
    }
    var messer = document.createElement("canvas").getContext("2d");
    // The renderer sets lines one by one, baseline to baseline
    // round(size * line_height) apart (compose._text); so does the sheet.
    // A line too long for its field is shown as the renderer prints it:
    // smaller or in two lines (textSatz, the same rule as vorlagen_render).
    function textMass(t) {
      var s = schrift(t);
      var stellen = function (groesse) { messer.font = s.gewicht + " " + groesse + "px " + s.css; };
      var mass = {
        breite: function (z, groesse) { stellen(groesse); return messer.measureText(z).width; },
        metrik: function (groesse) {
          stellen(groesse);
          var m = messer.measureText("M");
          return [m.fontBoundingBoxAscent || groesse * 0.97, m.fontBoundingBoxDescent || groesse * 0.24];
        }
      };
      var satz = textSatz(t, beispielText(t.value), v, mass);
      var groesse = satz.size, m = mass.metrik(groesse), zeilen = satz.zeilen;
      var abstand = zeilen.length > 1 ? (satz.abstand || Math.round(groesse * Math.max(0.8, Math.min(3, t.line_height || 1.2)))) : 0;
      return { w: Math.max(1, satz.breiteGesetzt), h: (zeilen.length - 1) * abstand + m[0] + m[1],
               zeile: m[0] + m[1], abstand: abstand, zeilen: zeilen, size: groesse, x: satz.x,
               align: satz.align, satz: satz };
    }
    function bildfeldKnoten(s) {
      var e = kasten(el("div"), s.id, s.x, s.y, s.w, s.h, s.rotation);
      var r = s.radius === "@rund" ? 14 : Number(s.radius || 0);
      e.style.borderRadius = r * skala + "px";
      e.style.background = "linear-gradient(180deg,rgba(231,173,89,.55),rgba(231,173,89,.18))";
      e.style.display = "flex"; e.style.alignItems = "center"; e.style.justifyContent = "center";
      e.style.color = "#fff"; e.style.fontWeight = "700"; e.style.fontSize = "16px";
      e.textContent = String(s.shot + 1);
      griff(e, s.id, true);
      return e;
    }
    function textKnoten(t) {
      var b = box({ art: "text", el: t }), m = textMass(t);
      var s = schrift(t);
      var e = kasten(el("div"), t.id, b.x, b.y, b.w, b.h, t.rotation);
      e.style.whiteSpace = "pre";
      e.style.fontFamily = s.css;
      e.style.fontWeight = String(s.gewicht);
      e.style.fontSynthesis = "none";
      e.style.fontSize = m.size * skala + "px";
      e.style.color = farbe(t.color, "#f5f3f0");
      if (t.opacity !== undefined && t.opacity < 100) e.style.opacity = String(t.opacity / 100);
      // Fitted to its field: the sheet says so, the panel says by how much.
      if (m.satz.geaendert) e.dataset.satz = m.satz.stufe;
      m.zeilen.forEach(function (z, i) {
        var zeile = el("div", { style: "position:absolute;left:0;right:0;top:" + i * m.abstand * skala + "px;height:" +
          m.zeile * skala + "px;line-height:" + m.zeile * skala + "px;text-align:" + (m.align || "left") }, z);
        e.appendChild(zeile);
      });
      griff(e, t.id, false);
      return e;
    }
    function bildKnoten(b) {
      var e = kasten(el("div"), b.id, b.x, b.y, b.w, b.h, b.rotation);
      var url = medienUrl(b.file);
      e.style.background = url ? "center / contain no-repeat url(" + JSON.stringify(url) + ")" : "rgba(255,255,255,.12)";
      if (!url) { e.style.outline = "1px dashed rgba(255,255,255,.5)"; e.textContent = b.file; e.style.color = "#fff"; e.style.fontSize = "11px"; }
      if (b.opacity !== undefined && b.opacity < 100) e.style.opacity = String(b.opacity / 100);
      griff(e, b.id, true);
      return e;
    }
    function formKnoten(f) {
      var e = kasten(el("div"), f.id, f.x, f.y, f.w, f.h, f.rotation);
      var fuellung = f.fill ? farbe(f.fill, "transparent") : "transparent";
      var kontur = f.stroke ? farbe(f.stroke, "transparent") : "";
      if (f.kind === "line") {
        var dicke = Math.max(1, f.stroke_width || 0);
        var oben = Math.round(f.h / 2 - dicke / 2);
        e.appendChild(el("div", { style: "position:absolute;left:0;right:0;top:" + oben * skala + "px;height:" +
          Math.max(1, dicke * skala) + "px;background:" + (kontur || (f.fill ? fuellung : "#000")) }));
      } else {
        e.style.background = fuellung;
        if (kontur && f.stroke_width) e.style.border = (f.stroke_width * skala) + "px solid " + kontur;
        e.style.borderRadius = f.kind === "ellipse" ? "50%" : (f.radius || 0) * skala + "px";
        if (!f.fill && !(kontur && f.stroke_width)) e.style.outline = "1px dashed rgba(255,255,255,.45)";
      }
      if (f.opacity !== undefined && f.opacity < 100) e.style.opacity = String(f.opacity / 100);
      griff(e, f.id, true);
      return e;
    }
    function qrKnoten(q) {
      var e = kasten(el("div"), "qr", q.x, q.y, q.size, q.size, q.rotation);
      e.style.background = "#fff";
      e.style.backgroundImage = "repeating-linear-gradient(90deg,#000 0 3px,transparent 3px 6px)," +
        "repeating-linear-gradient(0deg,#000 0 3px,transparent 3px 6px)";
      griff(e, "qr", true);
      return e;
    }

    // ── dragging ──
    var zieht = null;
    var linien = { xs: [], ys: [] }, rahmenZug = null;
    function blattPunkt(e) {
      var r = blatt.getBoundingClientRect();
      return { x: (e.clientX - r.left) / skala, y: (e.clientY - r.top) / skala };
    }
    function sichtbareBox(id) {
      var f = finde(id), b = box(f);
      return { id: id, x: b.x, y: b.y, w: b.w, h: b.h, r: f.el.rotation || 0 };
    }
    blattBox.addEventListener("pointerdown", function (e) {
      var ziel = e.target.closest("[data-griff],[data-dreh],[data-id]");
      e.preventDefault();
      blatt.focus({ preventScroll: true });
      try { blattBox.setPointerCapture(e.pointerId); } catch (_) { /* synthetic events have no pointer */ }
      if (!ziel) {
        // Empty sheet: a frame selects what it touches; a plain click clears.
        rahmenZug = { a: blattPunkt(e), b: blattPunkt(e), dazu: e.shiftKey ? markiert.slice() : [] };
        return;
      }
      var modus = ziel.dataset.griff !== undefined ? "groesse" : ziel.dataset.dreh !== undefined ? "dreh" : "zug";
      var id = ziel.dataset.griff || ziel.dataset.dreh || ziel.dataset.id;
      var f = finde(id);
      if (!f) return;
      if (modus === "zug" && e.shiftKey) {
        var i = markiert.indexOf(id);
        if (i >= 0) markiert.splice(i, 1); else markiert.push(id);
        auswahl = markiert.length ? (i >= 0 ? markiert[markiert.length - 1] : id) : null;
        zeichne();
        return;
      }
      if (markiert.indexOf(id) < 0 || modus !== "zug") { markiert = [id]; }
      auswahl = id;
      verlauf.beginne();
      var b = box(f), r = blatt.getBoundingClientRect();
      var mit = markiert.filter(function (m) { var g = finde(m); return g && !g.el.locked; });
      var anfang = {};
      mit.forEach(function (m) { anfang[m] = Object.assign({}, finde(m).el); });
      zieht = { f: f, modus: modus, sx: e.clientX, sy: e.clientY, anfang: Object.assign({}, f.el),
                mit: mit, anfaenge: anfang, huelle: mit.length ? huelle(mit.map(sichtbareBox)) : null,
                // the middle in screen pixels — the pivot of the rotation handle
                mx: r.left + (b.x + b.w / 2) * skala, my: r.top + (b.y + b.h / 2) * skala };
      // Snap targets: the sheet and every visible element not being moved.
      var andere = v.order.filter(function (o) {
        var g = finde(o);
        return g && !g.el.hidden && mit.indexOf(o) < 0;
      }).map(sichtbareBox);
      zieht.fang = fanglinien({ x: 0, y: 0, w: v.canvas[0], h: v.canvas[1] }, andere);
      zeichne();
    });
    blattBox.addEventListener("pointermove", function (e) {
      if (rahmenZug) {
        rahmenZug.b = blattPunkt(e);
        zeichneRahmen();
        return;
      }
      if (!zieht) return;
      var dx = (e.clientX - zieht.sx) / skala, dy = (e.clientY - zieht.sy) / skala;
      var a = zieht.anfang, o = zieht.f.el, art = zieht.f.art;
      var W = v.canvas[0], H = v.canvas[1];
      if (zieht.modus === "dreh") {
        var w = rasteWinkel(zeigerWinkel(zieht.mx, zieht.my, e.clientX, e.clientY), e.shiftKey);
        if (w) o.rotation = w; else delete o.rotation;
      } else if (zieht.modus === "groesse") {
        if (art === "qr") {
          // A QR code stays square: the larger of the two pulls decides.
          var quadrat = { x: a.x, y: a.y, w: a.size, h: a.size };
          var q = groesseGedreht(quadrat, dx, dy, a.rotation, 80);
          var seite = Math.round(grenze(Math.max(q.w, q.h), 80, Math.min(W, H) / 2));
          var qn = neueGroesse(quadrat, seite, seite, a.rotation);
          o.size = seite; o.x = qn.x; o.y = qn.y;
        } else {
          var n = groesseGedreht(a, dx, dy, a.rotation, art === "shape" ? 2 : art === "image" ? 10 : 80);
          if (!a.rotation) { n.w = Math.min(n.w, W - a.x); n.h = Math.min(n.h, H - a.y); }
          o.x = n.x; o.y = n.y; o.w = n.w; o.h = n.h;
        }
      } else if (zieht.mit.length) {
        // Move the whole selection; its hull snaps to sheet and neighbours
        // unless Alt is held.
        linien = { xs: [], ys: [] };
        if (!e.altKey && zieht.huelle) {
          var h0 = zieht.huelle;
          var fang = einrasten({ x: h0.x + dx, y: h0.y + dy, w: h0.w, h: h0.h }, zieht.fang, 6 / skala);
          dx += fang.dx; dy += fang.dy;
          linien = { xs: fang.linienX, ys: fang.linienY };
        }
        zieht.mit.forEach(function (m) { verschiebeEl(finde(m), zieht.anfaenge[m], Math.round(dx), Math.round(dy)); });
      }
      zeichne(true);
    });
    // The booth's check keeps photo slots and the QR code on the sheet; the
    // editor keeps them there too, so what is saved is what was shown.
    function verschiebeEl(f, a, dx, dy) {
      var o = f.el, W = v.canvas[0], H = v.canvas[1];
      if (f.art === "text") {
        o.x = Math.round(grenze(a.x + dx, 0, W));
        o.y = Math.round(grenze(a.y + dy, 0, H - 20));
      } else {
        var bw = f.art === "qr" ? o.size : o.w, bh = f.art === "qr" ? o.size : o.h;
        o.x = Math.round(grenze(a.x + dx, 0, W - bw));
        o.y = Math.round(grenze(a.y + dy, 0, H - bh));
      }
    }
    function loslassen() {
      if (rahmenZug) {
        var a = rahmenZug.a, b = rahmenZug.b;
        var x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x), y1 = Math.min(a.y, b.y), y2 = Math.max(a.y, b.y);
        var getroffen = rahmenZug.dazu.slice();
        if (x2 - x1 > 3 / skala || y2 - y1 > 3 / skala) {
          v.order.forEach(function (id) {
            var f = finde(id);
            if (!f || f.el.hidden || f.el.locked) return;
            var s = sichtbar(sichtbareBox(id));
            if (s.x < x2 && s.x + s.w > x1 && s.y < y2 && s.y + s.h > y1 && getroffen.indexOf(id) < 0) getroffen.push(id);
          });
        }
        rahmenZug = null;
        markiert = getroffen;
        auswahl = getroffen.length ? getroffen[getroffen.length - 1] : null;
        zeichne();
        return;
      }
      if (!zieht) return;
      zieht = null;
      linien = { xs: [], ys: [] };
      verlauf.schliesse(zustand());
      zeichne();
    }
    function zeichneRahmen() {
      var alt = blatt.querySelector(".vle-rahmen");
      if (alt) alt.remove();
      if (!rahmenZug) return;
      var a = rahmenZug.a, b = rahmenZug.b;
      var r = el("div", { class: "vle-rahmen" });
      r.style.left = Math.min(a.x, b.x) * skala + "px";
      r.style.top = Math.min(a.y, b.y) * skala + "px";
      r.style.width = Math.abs(a.x - b.x) * skala + "px";
      r.style.height = Math.abs(a.y - b.y) * skala + "px";
      blatt.appendChild(r);
    }
    var warnungen = [];
    // Text is what suffers from a cut edge: a photo losing a millimetre goes
    // unnoticed, a name losing its last letter does not.
    function pruefeRand() {
      var bereich = sichererBereich(v), xs = schnitte(v), aus = [];
      (v.texts || []).forEach(function (t) {
        if (t.hidden || !String(t.value || "").trim()) return;
        var b = sichtbareBox(t.id);
        if (ausserhalbSicher(b, bereich)) aus.push({ id: t.id, grund: T.warnMargin });
        else if (kreuztSchnitt(b, xs)) aus.push({ id: t.id, grund: T.warnCut });
      });
      return aus;
    }
    function zeichneLinien() {
      linien.xs.forEach(function (x) {
        blatt.appendChild(el("div", { class: "vle-linie", style: "left:" + x * skala + "px;top:0;bottom:0;width:1px" }));
      });
      linien.ys.forEach(function (y) {
        blatt.appendChild(el("div", { class: "vle-linie", style: "top:" + y * skala + "px;left:0;right:0;height:1px" }));
      });
    }
    blattBox.addEventListener("pointerup", loslassen);
    blattBox.addEventListener("pointercancel", loslassen);

    // ── align and distribute ──
    function richte(wie) {
      var ids = markiert.filter(function (id) { var f = finde(id); return f && !f.el.locked; });
      if (!ids.length) return;
      var boxen = ids.map(sichtbareBox);
      var bezug = markiert.length > 1 ? huelle(markiert.map(sichtbareBox)) : { x: 0, y: 0, w: v.canvas[0], h: v.canvas[1] };
      var wege = wie === "vx" ? verteilen(boxen, "x") : wie === "vy" ? verteilen(boxen, "y") : ausrichten(boxen, wie, bezug);
      ids.forEach(function (id) {
        var f = finde(id), d = wege[id] || [0, 0];
        verschiebeEl(f, Object.assign({}, f.el), d[0], d[1]);
      });
      geaendert();
    }

    // ── copy and paste ──
    // Within one template: what is copied are the elements themselves, not
    // the system clipboard, so nothing else on the page is affected. Pasting
    // is one undo step however many elements come in. The QR code exists
    // once per template and is not copied.
    var ablage = [];
    function kopiere() {
      ablage = markiert.map(finde).filter(function (f) { return f && KOPIER_LISTEN[f.art]; })
        .map(function (f) { return { art: f.art, el: JSON.parse(JSON.stringify(f.el)) }; });
      if (ablage.length) melde(T.copied.replace("{n}", ablage.length));
    }
    function einfuegen() {
      if (!ablage.length) return;
      var gewollt = ablage.length;
      var neu = kopienEinfuegen(v, ablage);
      if (!neu.length) return melde(T.full, true);
      markiert = neu.map(function (n) { return n.el.id; });
      auswahl = markiert[0];
      // The next paste lands another step further, not on top of this one.
      ablage = neu.map(function (n) { return { art: n.art, el: JSON.parse(JSON.stringify(n.el)) }; });
      geaendert();                               // one undo step for the whole paste
      if (neu.length < gewollt) melde(T.full, true);
    }
    function zeigeTasten() {
      var dlg = el("dialog", { class: "vle-vorschau", "aria-label": T.keys });
      var kopf = el("div", { class: "vle-vorschau-kopf" });
      kopf.appendChild(el("b", {}, T.keys));
      kopf.appendChild(knopf(T.close, function () { dlg.close(); }));
      var liste = el("dl", { class: "vle-tasten" });
      T.keyList.forEach(function (z) { liste.appendChild(el("dt", {}, z[0])); liste.appendChild(el("dd", {}, z[1])); });
      dlg.appendChild(kopf); dlg.appendChild(liste);
      document.body.appendChild(dlg);
      dlg.addEventListener("close", function () { dlg.remove(); });
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
    }

    // ── keyboard ──
    function tasten(e) {
      var imFeld = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "");
      var mod = e.metaKey || e.ctrlKey;
      if (mod && String(e.key).toLowerCase() === "z" && !imFeld) {
        e.preventDefault();
        if (e.shiftKey) vor(); else zurueck();
        return;
      }
      if (mod && String(e.key).toLowerCase() === "y" && !imFeld) { e.preventDefault(); vor(); return; }
      if (mod && !imFeld && auswahl && (e.code === "BracketRight" || e.code === "BracketLeft")) {
        e.preventDefault();
        var vorn = e.code === "BracketRight";
        v.order = (e.shiftKey ? (vorn ? ganzVorn : ganzHinten) : (vorn ? nachVorn : nachHinten))(v.order, auswahl);
        geaendert();
        return;
      }
      if (imFeld || !wurzel.contains(document.activeElement)) return;
      if (mod && String(e.key).toLowerCase() === "c") { e.preventDefault(); kopiere(); return; }
      if (mod && String(e.key).toLowerCase() === "v") { e.preventDefault(); einfuegen(); return; }
      if (e.key === "?") { e.preventDefault(); zeigeTasten(); return; }
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); entferne(); return; }
      var schritt = e.shiftKey ? 10 : 1;
      var d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (!d || !markiert.length) return;
      e.preventDefault();
      markiert.forEach(function (id) {
        var f = finde(id);
        if (f && !f.el.locked) verschiebeEl(f, Object.assign({}, f.el), d[0] * schritt, d[1] * schritt);
      });
      geaendert();
    }
    // Leaving with unsaved work asks first — a lost evening of layout work is
    // worse than one extra dialog.
    function verlassen(e) {
      if (zustand() === gespeichert) return;
      e.preventDefault();
      e.returnValue = T.leave;
      return T.leave;
    }
    // The booth page opens one editor after another in the same document; the
    // previous one must stop listening, or Ctrl+Z would edit a ghost.
    if (aktiv) aktiv();
    document.addEventListener("keydown", tasten);
    window.addEventListener("beforeunload", verlassen);
    aktiv = function () {
      document.removeEventListener("keydown", tasten);
      window.removeEventListener("beforeunload", verlassen);
    };

    // ── side panel ──
    // Typing updates the sheet at once; the step for undo is taken when the
    // field is left ("change"), so a word is one step and not one per letter.
    function feldFertig() {
      verlauf.merke(zustand());
      knopfZurueck.disabled = !verlauf.kannZurueck();
      knopfVor.disabled = !verlauf.kannVor();
      schmutzig.textContent = zustand() === gespeichert ? "" : "● " + T.unsaved;
    }
    // Redraw without rebuilding the panel, so the focused field stays focused.
    function nurBlatt() { zeichne(true); }

    function felder() {
      seite.innerHTML = "";
      var kopf = el("div", { class: "vle-gruppe" });
      kopf.appendChild(beschriftet(T.name, eingabe(v.name, function (w) { v.name = w; })));
      var papierWahl = el("select", { class: "form-control" });
      Object.keys(PAPIERE).forEach(function (k) {
        var o = el("option", { value: k }, PAPIERE[k].name);
        if (k === v.paper) o.selected = true;
        papierWahl.appendChild(o);
      });
      papierWahl.addEventListener("change", function () { wechslePapier(papierWahl.value); });
      kopf.appendChild(beschriftet(T.paper, papierWahl));
      kopf.appendChild(beschriftet(T.safeArea, dezimal(v.safe_area === undefined ? 3 : v.safe_area, 0, 15, 0.5,
        function (n) { v.safe_area = n; })));
      seite.appendChild(kopf);
      if (warnungen.length) {
        var w = el("div", { class: "vle-warnliste", role: "status" });
        warnungen.forEach(function (x) {
          var zeile = el("button", { type: "button", class: "vle-warnzeile" },
            "„" + ebenenName(x.id) + "“ — " + x.grund);
          zeile.addEventListener("click", function () { auswahl = x.id; markiert = [x.id]; zeichne(); });
          w.appendChild(zeile);
        });
        seite.appendChild(w);
      }
      seite.appendChild(ebenenPanel());

      var f = auswahl && finde(auswahl);
      if (markiert.length > 1) {
        seite.appendChild(el("p", { class: "vle-hinweis", style: "margin:0" },
          T.several.replace("{n}", markiert.length)));
      } else if (!f) {
        seite.appendChild(el("p", { class: "vle-hinweis", style: "margin:0" }, T.nothing));
      } else if (f.art === "slot") {
        var s = f.el, box = gruppe(T.slot + " " + (f.idx + 1));
        box.appendChild(namensFeld(s));
        box.appendChild(beschriftet(T.shot, zahl(s.shot + 1, 1, 12, function (n) { s.shot = n - 1; })));
        box.appendChild(lage(s, ["x", "y", "w", "h"]));
        box.appendChild(drehFeld(s));
        seite.appendChild(box);
      } else if (f.art === "text") {
        var t = f.el, tb = gruppe(T.text + " " + (f.idx + 1));
        tb.appendChild(namensFeld(t));
        tb.appendChild(beschriftet(T.value, textfeld(t.value, function (w) {
          t.value = w;
          // A second line needs a spacing both sides agree on.
          if (/\n/.test(w) && !t.line_height) t.line_height = 1.2;
        })));
        var schriftWahl = [["", T.fontStyle], ["system", "Inter"], ["display", "Archivo"], ["serif", "Serif (Georgia)"]]
          .concat(schriften.map(function (f) { return [f.file, f.name || f.file]; }));
        tb.appendChild(beschriftet(T.font, auswahlFeld(t.font || "", schriftWahl, function (w) {
          if (w) t.font = w; else delete t.font;
        })));
        tb.appendChild(farbFeld(T.color, t.color || "@schrift", false, function (w) { t.color = w; }));
        var zeile2 = el("div", { class: "vle-zwei" });
        zeile2.appendChild(beschriftet(T.lineHeight, dezimal(t.line_height || 1.2, 0.8, 3, 0.1, function (n) { t.line_height = n; })));
        zeile2.appendChild(beschriftet(T.opacity, zahl(t.opacity === undefined ? 100 : t.opacity, 0, 100, function (n) {
          if (n < 100) t.opacity = n; else delete t.opacity;
        })));
        tb.appendChild(zeile2);
        var zeile = el("div", { class: "vle-zwei" });
        zeile.appendChild(beschriftet(T.size, zahl(t.size, 12, 200, function (n) { t.size = n; })));
        zeile.appendChild(beschriftet(T.align, auswahlFeld(t.align || "left",
          [["left", T.aLeft], ["center", T.aCenter], ["right", T.aRight]], function (w) { t.align = w; })));
        tb.appendChild(zeile);
        tb.appendChild(haken(T.bold, !!t.bold, function (w) { t.bold = w; }));
        var feldZeile = el("div", { class: "vle-zwei" });
        [["w", T.fieldW, v.canvas[0]], ["h", T.fieldH, v.canvas[1]]].forEach(function (k) {
          feldZeile.appendChild(beschriftet(k[1], zahl(t[k[0]] || 0, 0, k[2], function (n) {
            if (n >= 20) t[k[0]] = n; else delete t[k[0]];
          })));
        });
        tb.appendChild(feldZeile);
        tb.appendChild(el("p", { class: "vle-hinweis", style: "margin:0" }, T.fieldHint));
        var satz = textMass(t).satz;
        if (satz.geaendert) {
          var fitText = { kleiner: T.fitKleiner, zweizeilig: T.fitZwei, verschoben: T.fitVerschoben, sehr_klein: T.fitKlein }[satz.stufe];
          tb.appendChild(el("p", { class: "vle-hinweis vle-satz", role: "status" },
            fitText.replace("{s}", satz.size).replace("{g}", t.size)));
        }
        tb.appendChild(qrHaken(t));
        tb.appendChild(lage(t, ["x", "y"]));
        tb.appendChild(drehFeld(t));
        seite.appendChild(tb);
      } else if (f.art === "shape") {
        var fo = f.el, fb = gruppe(T.shape);
        fb.appendChild(namensFeld(fo));
        fb.appendChild(beschriftet(T.kind, auswahlFeld(fo.kind || "rect",
          [["rect", T.rect], ["ellipse", T.ellipse], ["line", T.line]], function (w) { fo.kind = w; })));
        if (fo.kind !== "line") fb.appendChild(farbFeld(T.fill, fo.fill || "", true, function (w) { fo.fill = w; }));
        fb.appendChild(farbFeld(fo.kind === "line" ? T.color : T.stroke, fo.stroke || "", fo.kind !== "line",
          function (w) { fo.stroke = w; }));
        var z3 = el("div", { class: "vle-zwei" });
        z3.appendChild(beschriftet(T.strokeWidth, zahl(fo.stroke_width || 0, 0, 200, function (n) { fo.stroke_width = n; })));
        if (fo.kind === "rect") z3.appendChild(beschriftet(T.radius, zahl(fo.radius || 0, 0, 2000, function (n) { fo.radius = n; })));
        fb.appendChild(z3);
        fb.appendChild(beschriftet(T.opacity, zahl(fo.opacity === undefined ? 100 : fo.opacity, 0, 100,
          function (n) { fo.opacity = n; })));
        fb.appendChild(qrHaken(fo));
        fb.appendChild(lage(fo, ["x", "y", "w", "h"]));
        fb.appendChild(drehFeld(fo));
        seite.appendChild(fb);
      } else if (f.art === "image") {
        var bi = f.el, bb = gruppe(T.image);
        bb.appendChild(namensFeld(bi));
        bb.appendChild(beschriftet(T.file, auswahlFeld(bi.file, medien.map(function (m) { return [m.file, m.name || m.file]; }),
          function (w) { bi.file = w; })));
        bb.appendChild(beschriftet(T.opacity, zahl(bi.opacity === undefined ? 100 : bi.opacity, 0, 100,
          function (n) { bi.opacity = n; })));
        bb.appendChild(qrHaken(bi));
        bb.appendChild(lage(bi, ["x", "y", "w", "h"]));
        bb.appendChild(drehFeld(bi));
        seite.appendChild(bb);
      } else if (f.art === "qr") {
        var q = f.el, qb = gruppe(T.qr);
        qb.appendChild(lage(q, ["x", "y"]));
        qb.appendChild(drehFeld(q));
        qb.appendChild(beschriftet(T.size, zahl(q.size, 80, 2000, function (n) { q.size = n; })));
        seite.appendChild(qb);
      }

      if (opt.loeschen) {
        seite.appendChild(knopf(T.del, function () {
          if (!window.confirm(T.confirmDel)) return;
          Promise.resolve(opt.loeschen()).catch(function (e) { melde(String(e), true); });
        }));
      }
      seite.appendChild(meldung);
    }
    var meldung = el("p", { class: "vle-meldung" });

    // ── layers ──
    function ebenenName(id) {
      if (id === "overlay") return v.overlay ? T.layerOverlay : T.layerFrame;
      var f = finde(id);
      if (!f) return id;
      if (f.el.name) return f.el.name;
      if (f.art === "slot") return T.slot + " · " + T.shotShort + " " + (f.el.shot + 1);
      if (f.art === "text") return beispielText(f.el.value).split("\n")[0].slice(0, 28) || T.text;
      if (f.art === "shape") return ({ rect: T.rect, ellipse: T.ellipse, line: T.line })[f.el.kind] || T.shape;
      if (f.art === "image") return T.image + " · " + f.el.file;
      return T.qr;
    }
    function ordne(neu) {
      v.order = neu;
      geaendert();
    }
    function ebenenPanel() {
      var g = el("div", { class: "vle-gruppe" });
      var kopfzeile = el("div", { class: "vle-ebenen-kopf" });
      kopfzeile.appendChild(el("b", {}, T.layers));
      var knoepfe = el("div", { class: "vle-ebenen-knoepfe" });
      kopfzeile.appendChild(knoepfe);
      g.appendChild(kopfzeile);
      [["⤒", T.toFront, ganzVorn], ["↑", T.forward, nachVorn], ["↓", T.backward, nachHinten],
       ["⤓", T.toBack, ganzHinten]].forEach(function (k) {
        var b = knopf(k[0], function () { if (auswahl) ordne(k[2](v.order, auswahl)); });
        b.title = k[1];
        b.setAttribute("aria-label", k[1]);
        b.disabled = !auswahl;
        knoepfe.appendChild(b);
      });
      var liste = el("ol", { class: "vle-ebenen" });
      v.order.slice().reverse().forEach(function (id) {
        var f = finde(id);
        var zeile = el("li", { class: "vle-ebene" + (markiert.indexOf(id) >= 0 ? " vle-ebene-an" : ""), draggable: "true" });
        zeile.dataset.ebene = id;
        var name = el("span", { class: "vle-ebene-name" }, ebenenName(id));
        zeile.appendChild(name);
        if (f) {
          zeile.appendChild(schalter(f.el.hidden ? AUGE_ZU : AUGE, f.el.hidden ? T.show : T.hide, !!f.el.hidden,
            function () { if (f.el.hidden) delete f.el.hidden; else f.el.hidden = true; geaendert(); }));
          zeile.appendChild(schalter(f.el.locked ? SCHLOSS_ZU : SCHLOSS, f.el.locked ? T.unlock : T.lock, !!f.el.locked,
            function () { if (f.el.locked) delete f.el.locked; else f.el.locked = true; geaendert(); }));
          zeile.addEventListener("click", function (e) {
            if (e.shiftKey) {
              var i = markiert.indexOf(id);
              if (i >= 0) markiert.splice(i, 1); else markiert.push(id);
              auswahl = markiert.length ? markiert[markiert.length - 1] : null;
            } else {
              auswahl = id; markiert = [id];
            }
            zeichne();
          });
        } else {
          zeile.classList.add("vle-ebene-fest");
        }
        zeile.addEventListener("dragstart", function (e) {
          e.dataTransfer.setData("text/plain", id);
          e.dataTransfer.effectAllowed = "move";
        });
        zeile.addEventListener("dragover", function (e) { e.preventDefault(); zeile.classList.add("vle-ebene-ziel"); });
        zeile.addEventListener("dragleave", function () { zeile.classList.remove("vle-ebene-ziel"); });
        zeile.addEventListener("drop", function (e) {
          e.preventDefault();
          var wer = e.dataTransfer.getData("text/plain");
          if (wer && wer !== id) ordne(verschiebe(v.order, wer, v.order.indexOf(id)));
        });
        liste.appendChild(zeile);
      });
      g.appendChild(liste);
      return g;
    }
    function schalter(svg, titel, an, bei) {
      var b = el("button", { type: "button", class: "vle-schalter" + (an ? " vle-schalter-an" : ""),
                             title: titel, "aria-label": titel, "aria-pressed": an ? "true" : "false" });
      b.innerHTML = svg;
      b.addEventListener("click", function (e) { e.stopPropagation(); bei(); });
      return b;
    }

    function wechslePapier(papier) {
      var alt = v.canvas.slice();
      v.paper = papier;
      v.canvas = PAPIERE[papier].canvas.slice();
      var fx = v.canvas[0] / alt[0], fy = v.canvas[1] / alt[1];
      v.slots.forEach(function (s) {
        s.x = Math.round(s.x * fx); s.y = Math.round(s.y * fy);
        s.w = Math.round(s.w * fx); s.h = Math.round(s.h * fy);
      });
      (v.texts || []).forEach(function (t) { t.x = Math.round(t.x * fx); t.y = Math.round(t.y * fy); });
      (v.images || []).concat(v.shapes || []).forEach(function (o) {
        o.x = Math.round(o.x * fx); o.y = Math.round(o.y * fy);
        o.w = Math.round(o.w * fx); o.h = Math.round(o.h * fy);
      });
      if (v.qr) { v.qr.x = Math.round(v.qr.x * fx); v.qr.y = Math.round(v.qr.y * fy); }
      geaendert();
    }

    function drehFeld(o) {
      return beschriftet(T.rotation, zahl(o.rotation || 0, -360, 360, function (n) {
        var w = ((n % 360) + 360) % 360;
        if (w) o.rotation = w; else delete o.rotation;
      }));
    }
    function namensFeld(o) {
      var feld = beschriftet(T.layerName, eingabe(o.name || "", function (w) {
        if (w.trim()) o.name = w.slice(0, 40); else delete o.name;
      }));
      // The layer list shows the name; refresh it once the field is left.
      feld.querySelector("input").addEventListener("change", function () { zeichne(); });
      return feld;
    }
    function textfeld(wert, bei) {
      var i = el("textarea", { class: "form-control", rows: "2" });
      i.value = wert || "";
      i.addEventListener("input", function () { bei(i.value); nurBlatt(); });
      i.addEventListener("change", feldFertig);
      return i;
    }
    function dezimal(wert, klein, gross, schritt, bei) {
      var i = el("input", { type: "number", class: "form-control", min: klein, max: gross, step: schritt });
      i.value = wert;
      i.addEventListener("input", function () {
        var n = Number(i.value);
        if (i.value !== "" && !isNaN(n)) { bei(Math.round(Math.max(klein, Math.min(gross, n)) * 100) / 100); nurBlatt(); }
      });
      i.addEventListener("change", feldFertig);
      return i;
    }
    // A colour is a style token (the booth's style decides) or a fixed one.
    function farbFeld(titel, wert, leerErlaubt, bei) {
      var paare = [["@schrift", T.tInk], ["@gedaempft", T.tMuted], ["@akzent", T.tAccent], ["@hintergrund", T.tBg]];
      if (leerErlaubt) paare.unshift(["", T.none]);
      paare.push(["#", T.custom]);
      var eigen = /^#[0-9a-f]{6}$/i.test(wert || "");
      var reihe = el("div", { style: "display:flex;gap:6px;align-items:center" });
      var s = el("select", { class: "form-control" });
      paare.forEach(function (p) {
        var o = el("option", { value: p[0] }, p[1]);
        if (p[0] === (eigen ? "#" : (wert || ""))) o.selected = true;
        s.appendChild(o);
      });
      var c = el("input", { type: "color", style: "width:44px;height:36px;padding:0;border:0;background:none" });
      c.value = eigen ? wert : "#ffffff";
      c.hidden = !eigen;
      s.addEventListener("change", function () {
        c.hidden = s.value !== "#";
        bei(s.value === "#" ? c.value : s.value);
        geaendert();
      });
      c.addEventListener("input", function () { bei(c.value); nurBlatt(); });
      c.addEventListener("change", feldFertig);
      reihe.appendChild(s); reihe.appendChild(c);
      return beschriftet(titel, reihe);
    }
    function gruppe(titel) {
      var g = el("div", { class: "vle-gruppe" });
      g.appendChild(el("b", {}, titel));
      return g;
    }
    function lage(o, keys) {
      var g = el("div", { class: "vle-zwei" });
      keys.forEach(function (k) {
        g.appendChild(beschriftet(T[k], zahl(o[k], k === "w" || k === "h" ? 80 : -4000, 4000,
          function (n) { o[k] = n; })));
      });
      return g;
    }
    function beschriftet(text, feld) {
      var d = el("label", { class: "vle-feld" });
      d.appendChild(el("span", {}, text));
      d.appendChild(feld);
      return d;
    }
    function eingabe(wert, bei) {
      var i = el("input", { type: "text", class: "form-control" });
      i.value = wert || "";
      i.addEventListener("input", function () { bei(i.value); nurBlatt(); });
      i.addEventListener("change", feldFertig);
      return i;
    }
    function zahl(wert, klein, gross, bei) {
      var i = el("input", { type: "number", class: "form-control", min: klein, max: gross });
      i.value = wert;
      i.addEventListener("input", function () {
        var n = Number(i.value);
        if (i.value !== "" && !isNaN(n)) { bei(Math.max(klein, Math.min(gross, Math.round(n)))); nurBlatt(); }
      });
      i.addEventListener("change", feldFertig);
      return i;
    }
    function auswahlFeld(wert, paare, bei) {
      var s = el("select", { class: "form-control" });
      paare.forEach(function (p) {
        var o = el("option", { value: p[0] }, p[1]);
        if (p[0] === wert) o.selected = true;
        s.appendChild(o);
      });
      s.addEventListener("change", function () { bei(s.value); geaendert(); });
      return s;
    }
    // "nur_mit_qr": the "scan the code" caption, a frame around the code —
    // printed only when the sheet carries a QR code (vorlagen_render).
    function qrHaken(e) {
      return haken(T.onlyWithQr, !!e.nur_mit_qr, function (w) { if (w) e.nur_mit_qr = true; else delete e.nur_mit_qr; });
    }
    function haken(text, wert, bei) {
      var l = el("label", { class: "vle-feld", style: "display:flex;gap:8px;align-items:center" });
      var c = el("input", { type: "checkbox" });
      c.checked = wert;
      c.addEventListener("change", function () { bei(c.checked); geaendert(); });
      l.appendChild(c);
      l.appendChild(el("span", {}, text));
      return l;
    }
    function knopf(text, bei, primaer) {
      var b = el("button", { type: "button", class: "btn " + (primaer ? "btn-primary" : "btn-secondary") }, text);
      b.addEventListener("click", bei);
      return b;
    }
    function melde(text, schlimm) {
      meldung.textContent = text;
      meldung.style.color = schlimm ? "#b3261e" : "#2e7d32";
    }

    function speichere() {
      knopfSpeichern.disabled = true;
      var abgabe = zustand();
      Promise.resolve(opt.speichern(JSON.parse(abgabe)))
        .then(function (antwort) {
          melde((antwort && antwort.hinweise || []).join(" ") || T.saved);
          if (antwort && antwort.template) {
            v = sichereIds(antwort.template);
            v.order = ebenenFolge(v);
            if (auswahl && !finde(auswahl)) auswahl = null;
          }
          gespeichert = zustand();
          verlauf.merke(gespeichert);
          zeichne();
        })
        .catch(function (e) { melde(T.notSaved + (e && e.message || e), true); })
        .then(function () { knopfSpeichern.disabled = false; });
    }

    zeichne();
    // Text boxes are measured with the brand fonts; once they have loaded
    // the sheet is drawn again so boxes and turn points are exact.
    // A declared font is only fetched once something uses it, and the canvas
    // that measures does not count — so ask for the print cuts explicitly.
    if (document.fonts && document.fonts.load) {
      Promise.all(["400 20px Inter", "600 20px Inter", "600 20px Archivo", "700 20px Archivo"].map(function (f) {
        return document.fonts.load(f).catch(function () { return null; });
      }).concat(schriftenGeladen)).then(function () { zeichne(true); });
    }
    return { vorlage: function () { return v; }, zeichne: zeichne, verlauf: verlauf };
  }

  window.pbVorlagenEditor = { start: start, neueVorlage: neueVorlage, PAPIERE: PAPIERE };
})();
