/* The template editor page in the web admin: wires the shared editor
 * (vorlagen-editor.js, the same file the booth uses) to the gallery's
 * endpoints. Everything the page knows comes from the JSON block
 * #pbEditorDaten, so no inline script is needed. */
(function () {
  "use strict";
  var daten = JSON.parse(document.getElementById("pbEditorDaten").textContent || "{}");
  var editor = null;
  /* The template as last saved, to tell whether a test print would show
   * what is on screen. Set after the editor took the server's answer. */
  var gespeichertStand = null;

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": daten.csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) {
        throw new Error(d.error || ("Server antwortet " + r.status));
      });
      return r.json();
    });
  }

  editor = window.pbVorlagenEditor.start({
    wurzel: document.getElementById("editor"),
    vorlage: daten.vorlage || undefined,
    sprache: daten.sprache,
    medien: daten.medien || [],
    schriften: daten.schriften || [],
    vorschau: function (v, stil) {
      return fetch(daten.vorschauUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": daten.csrf },
        body: JSON.stringify({ template: v, stil: stil })
      }).then(function (r) {
        if (!r.ok) throw new Error("Server antwortet " + r.status);
        return r.blob();
      }).then(function (b) { return URL.createObjectURL(b); });
    },
    speichern: function (v) {
      return post(daten.speichernUrl, v).then(function (d) {
        // After the first save the address names the file, so a reload does
        // not create a second template.
        if (d.asset && d.asset.file && !daten.datei) {
          daten.datei = d.asset.file;
          history.replaceState(null, "", "?datei=" + encodeURIComponent(d.asset.file));
        }
        setTimeout(function () { gespeichertStand = JSON.stringify(editor.vorlage()); }, 0);
        return d;
      });
    }
  });

  testdruckEinrichten();

  /* Test print at a booth. Only the saved version goes out (the booth
   * receives what the gallery stored), so unsaved changes block the button
   * with a hint instead of printing something else than what is on screen. */
  function testdruckEinrichten() {
    var feld = document.getElementById("pbTestdruck");
    if (!feld || !daten.testdruckUrl) return;
    var tx = daten.texte || {};
    if (daten.datei) gespeichertStand = JSON.stringify(editor.vorlage());
    if (!(daten.boxen || []).length) {
      feld.textContent = tx.no_booth || "";
      return;
    }
    var knopf = el("button", { type: "button", "class": "btn btn-secondary btn-sm" }, tx.button);
    var wahl = el("select", { "aria-label": tx.booth, "class": "form-control", style: "width:auto;display:inline-block" });
    daten.boxen.forEach(function (b) { wahl.appendChild(el("option", { value: String(b.id) }, b.name)); });
    var marke = el("input", { type: "checkbox", checked: "checked", id: "pbTestdruckMarke" });
    var markeLabel = el("label", { "for": "pbTestdruckMarke", style: "margin:0 12px 0 4px" }, tx.mark);
    var meldung = el("p", { role: "status", "aria-live": "polite", style: "margin:8px 0 0" }, "");
    [wahl, document.createTextNode(" "), marke, markeLabel, knopf, meldung].forEach(function (n) { feld.appendChild(n); });

    knopf.addEventListener("click", function () {
      if (!daten.datei || gespeichertStand !== JSON.stringify(editor.vorlage())) {
        meldung.textContent = tx.unsaved;
        return;
      }
      knopf.disabled = true;
      meldung.textContent = tx.sent;
      post(daten.testdruckUrl, { device_id: Number(wahl.value), datei: daten.datei, mark: marke.checked })
        .then(function (d) { warten(d.result_url, 0); })
        .catch(function (e) { meldung.textContent = tx.failed + ": " + e.message; knopf.disabled = false; });
    });

    function warten(url, runde) {
      fetch(url, { headers: { "Accept": "application/json" } }).then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.done) {
            var titel = d.state === "expired" ? tx.expired : (d.success ? tx.done : tx.failed);
            meldung.textContent = titel + (d.result && d.state !== "expired" ? ": " + d.result : "");
            knopf.disabled = false;
            return;
          }
          if (d.delivered) meldung.textContent = tx.waiting;
          // The booth polls every 5 s while a job is open; ten minutes and the job expires.
          if (runde < 260) setTimeout(function () { warten(url, runde + 1); }, 2500);
          else knopf.disabled = false;
        })
        .catch(function () { setTimeout(function () { warten(url, runde + 1); }, 5000); });
    }
  }

  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text) n.textContent = text;
    return n;
  }

  var duplizieren = document.getElementById("vorlageDuplizieren");
  if (duplizieren) {
    duplizieren.addEventListener("click", function () {
      duplizieren.disabled = true;
      post(duplizieren.dataset.url, { datei: daten.datei })
        .then(function (d) { window.location.href = d.editor_url; })
        .catch(function (e) { duplizieren.disabled = false; window.alert(e.message); });
    });
  }

  var loeschen = document.getElementById("vorlageLoeschen");
  if (loeschen) {
    loeschen.addEventListener("submit", function (e) {
      if (!window.confirm(loeschen.dataset.frage)) e.preventDefault();
    });
  }
  telefonEinrichten();

  /* On a phone: the sheet is shown, not dragged; the layer list and the
   * properties of the selected layer sit in a panel at the bottom of the
   * screen; the toolbar keeps undo, redo, preview and save. The editor itself
   * (shared with the booth, byte for byte) is not changed: this only moves
   * its side panel and marks the toolbar buttons that stay. */
  function telefonEinrichten() {
    var wurzel = document.getElementById("editor");
    var leiste = wurzel && wurzel.querySelector(".vle-leiste");
    var seite = wurzel && wurzel.querySelector(".vle-seite");
    if (!leiste || !seite) return;
    var haupt = seite.parentNode;
    // The sheet is a picture of the print: its tiny lettering is not page text.
    var papier = wurzel.querySelector(".vle-blatt");
    if (papier) papier.setAttribute("data-check-skip", "");
    var tx = {};
    try { tx = JSON.parse(document.getElementById("pbEditorPhone").textContent || "{}"); } catch (e) { tx = {}; }
    var klein = window.matchMedia("(max-width: 768px)");

    // Undo and redo come first, save comes last; preview stands just before
    // the margin toggle (the one button with aria-pressed).
    var kinder = leiste.children;
    if (kinder[0]) kinder[0].classList.add("ed-keep");
    if (kinder[1]) kinder[1].classList.add("ed-keep");
    var rand = leiste.querySelector("[aria-pressed]");
    if (rand && rand.previousElementSibling && rand.previousElementSibling.tagName === "BUTTON" &&
        !rand.previousElementSibling.classList.contains("vle-ikon")) rand.previousElementSibling.classList.add("ed-keep");
    var schmutzig = leiste.querySelector(".vle-schmutzig");
    if (schmutzig) schmutzig.classList.add("ed-keep");
    var letzter = leiste.lastElementChild;
    if (letzter) letzter.classList.add("ed-keep", "ed-keep-save");

    var blatt = document.createElement("section");
    blatt.className = "ed-sheet";
    blatt.setAttribute("aria-label", tx.panel || "");
    var kopf = document.createElement("button");
    kopf.type = "button";
    kopf.className = "ed-sheet-head";
    kopf.setAttribute("aria-expanded", "false");
    var titel = document.createElement("span");
    titel.textContent = tx.panel || "";
    var hinweis = document.createElement("small");
    hinweis.textContent = tx.hint || "";
    kopf.appendChild(titel);
    kopf.appendChild(hinweis);
    blatt.appendChild(kopf);
    kopf.addEventListener("click", function () {
      var auf = !blatt.classList.contains("is-open");
      blatt.classList.toggle("is-open", auf);
      kopf.setAttribute("aria-expanded", auf ? "true" : "false");
    });

    var zoomKnoepfe = document.querySelectorAll(".ed-zoom [data-zoom]");
    Array.prototype.forEach.call(zoomKnoepfe, function (b) {
      b.addEventListener("click", function () {
        var zwei = b.dataset.zoom === "2";
        wurzel.classList.toggle("ed-zoomed", zwei);
        Array.prototype.forEach.call(zoomKnoepfe, function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      });
    });

    function umstellen() {
      if (klein.matches) {
        wurzel.classList.add("ed-phone");
        if (seite.parentNode !== blatt) blatt.appendChild(seite);
        if (!blatt.parentNode) document.body.appendChild(blatt);
        document.body.classList.add("ed-has-sheet");
      } else {
        wurzel.classList.remove("ed-phone", "ed-zoomed");
        if (seite.parentNode !== haupt) haupt.appendChild(seite);
        if (blatt.parentNode) blatt.parentNode.removeChild(blatt);
        document.body.classList.remove("ed-has-sheet");
      }
    }
    umstellen();
    klein.addEventListener("change", umstellen);
  }

  window.pbEditorSeite = { editor: editor, daten: daten };
})();
