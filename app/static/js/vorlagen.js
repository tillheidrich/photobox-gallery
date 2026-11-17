/* Vorschau der Druckvorlagen in der Verwaltung.
 *
 * Gezeichnet wird nicht gerendert: Die Box baut den echten Abzug mit Pillow,
 * hier entsteht nur ein maßstäbliches Schema aus denselben Zahlen
 * (app/static/data/collage_templates.json, erzeugt von tools/build_collage_catalog.py).
 * Das genügt, um Anordnung und Farben zu beurteilen, und spart eine zweite
 * Renderstrecke, die mit der ersten auseinanderlaufen würde.
 */
(function () {
  "use strict";

  var daten = null;

  function laden() {
    if (daten) return Promise.resolve(daten);
    return fetch("/static/data/collage_templates.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (d) { daten = d; return d; });
  }

  function stil(form, praefix) {
    var presets = (daten && daten.presets) || {};
    function feld(name) {
      var el = form && form.querySelector('[name="' + praefix + name + '"]');
      return el ? String(el.value || "").trim() : "";
    }
    var preset = feld("preset") || "clean";
    var st = Object.assign({}, presets[preset] || presets.clean || {});
    ["background", "ink", "muted", "accent", "headline", "subline", "font"].forEach(function (k) {
      var v = feld(k);
      if (v) st[k] = v;
    });
    ["radius", "frame"].forEach(function (k) {
      var v = feld(k);
      if (v !== "") st[k] = Number(v) || 0;
    });
    return st;
  }

  var TOKEN = { "@hintergrund": "background", "@schrift": "ink", "@gedaempft": "muted", "@akzent": "accent" };
  function farbe(wert, st, fallback) {
    var key = TOKEN[wert];
    if (key) return st[key] || fallback;
    return wert || fallback;
  }

  function karte(t, st, breite) {
    var skala = breite / t.canvas[0];
    var box = document.createElement("div");
    box.className = "vl-karte";
    // A miniature is a picture: screen readers skip it, the phone check does not count its tiny text.
    box.setAttribute("aria-hidden", "true");
    box.setAttribute("data-check-skip", "");
    box.style.cssText = "position:relative;overflow:hidden;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.25);" +
      "width:" + breite + "px;height:" + Math.round(t.canvas[1] * skala) + "px;" +
      "background:" + (st.background || "#111");
    if (st.frame) {
      var r = document.createElement("div");
      var ein = Math.max(1, st.frame * skala);
      r.style.cssText = "position:absolute;inset:" + ein + "px;border:" + Math.max(1, ein / 4) +
        "px solid " + (st.accent || "#888") + ";pointer-events:none";
      box.appendChild(r);
    }
    t.slots.forEach(function (s) {
      var d = document.createElement("div");
      d.style.cssText = "position:absolute;left:" + s.x * skala + "px;top:" + s.y * skala + "px;" +
        "width:" + s.w * skala + "px;height:" + s.h * skala + "px;" +
        "border-radius:" + Math.max(0, (st.radius || 0) * skala) + "px;" +
        "background:linear-gradient(135deg, rgba(255,255,255,.22), rgba(255,255,255,.07));" +
        "display:flex;align-items:center;justify-content:center;color:" + (st.ink || "#fff") +
        ";font-size:" + Math.max(8, 44 * skala) + "px;opacity:.9";
      d.textContent = (s.shot + 1);
      box.appendChild(d);
    });
    (t.texts || []).forEach(function (tx) {
      var d = document.createElement("div");
      var links = tx.align === "center" ? (tx.x - t.canvas[0] / 2) : (tx.align === "right" ? 0 : tx.x);
      d.style.cssText = "position:absolute;top:" + tx.y * skala + "px;" +
        (tx.align === "center" ? "left:0;right:0;text-align:center;" :
         tx.align === "right" ? "right:" + (t.canvas[0] - tx.x) * skala + "px;text-align:right;" :
         "left:" + links * skala + "px;") +
        "font-size:" + Math.max(6, tx.size * skala) + "px;line-height:1.1;white-space:nowrap;" +
        "font-weight:" + (tx.bold ? 700 : 400) + ";color:" + farbe(tx.color, st, st.ink || "#fff");
      var wert = String(tx.value || "")
        .replace("{headline}", st.headline || "{event_name}")
        .replace("{subline}", st.subline || "{date}")
        .replace("{event_name}", "Mia & Tom").replace("{date}", "20.09.2026");
      d.textContent = wert;
      box.appendChild(d);
    });
    if (t.qr) {
      var q = document.createElement("div");
      q.style.cssText = "position:absolute;left:" + t.qr.x * skala + "px;top:" + t.qr.y * skala + "px;" +
        "width:" + t.qr.size * skala + "px;height:" + t.qr.size * skala + "px;background:#fff;" +
        "background-image:repeating-linear-gradient(90deg,#000 0 2px,transparent 2px 4px)," +
        "repeating-linear-gradient(0deg,#000 0 2px,transparent 2px 4px);opacity:.85";
      box.appendChild(q);
    }
    return box;
  }

  function zeichne(behaelter, opt) {
    opt = opt || {};
    laden().then(function (d) {
      var st = stil(opt.form || document, opt.praefix || "cs_");
      var nur = opt.auswahl && opt.auswahl.length ? opt.auswahl : null;
      behaelter.innerHTML = "";
      d.templates.forEach(function (t) {
        if (nur && nur.indexOf(t.id) < 0) return;
        var zelle = document.createElement("figure");
        zelle.style.cssText = "margin:0;display:flex;flex-direction:column;gap:6px;align-items:center";
        zelle.appendChild(karte(t, st, opt.breite || 150));
        var bu = document.createElement("figcaption");
        bu.style.cssText = "font-size:.72rem;color:#777;text-align:center;max-width:170px";
        bu.textContent = t.name + " · " + t.shots + (t.shots === 1 ? " Foto" : " Fotos");
        zelle.appendChild(bu);
        // Die Seite darf jeder Karte einen Weg mitgeben (etwa „kopieren").
        if (typeof opt.link === "function") {
          var ziel = opt.link(t);
          if (ziel) {
            var a = document.createElement("a");
            a.href = ziel.href; a.textContent = ziel.text || "öffnen";
            a.style.cssText = "font-size:.72rem";
            zelle.appendChild(a);
          }
        }
        behaelter.appendChild(zelle);
      });
    });
  }

  window.pbVorlagen = { zeichne: zeichne, laden: laden };
})();
