// Search over a long settings form, the same way the booth's own settings page
// searches (booth/boothcore/web/static/settings.js), but at the gallery: type a
// word, sections and settings that do not match fold away, what matches stays
// under its section title with the words marked.
//
// Markup: admin/_settings_search.html puts the bar on the page. It names what
// to search in data attributes:
//   data-ss-scope    CSS selector of the element that holds the sections
//   data-ss-card     what a section is (default ".card")
//   data-ss-head     its heading inside it (default "h2")
//   data-ss-field    what one setting is (label + help + control)
//   data-ss-chips    "1": build a row of section chips (jump list) in the bar
//
// Rules this file keeps (tests/test_settings_search.py):
// - A hidden setting is only hidden with a class. Nothing is disabled, removed
//   or moved out of its form, so saving while a filter is on sends every field
//   exactly as it stood.
// - The search field has no name and sits outside every form.
// - Words are matched without case and without umlauts (ä = ae, ß = ss);
//   several words all have to match (AND), against the label, the help text,
//   the section title, the field's name and its placeholder.
(function () {
  "use strict";

  var SKIP = "option, textarea, script, style, select, .ss-bar";
  var CONTROL = "input:not([type=hidden]), select, textarea";

  // One character in, its folded form out. The map keeps where every folded
  // character came from, so a match can be marked in the original text.
  function fold(ch) {
    var c = ch.toLowerCase();
    if (c === "ä") return "ae";
    if (c === "ö") return "oe";
    if (c === "ü") return "ue";
    if (c === "ß") return "ss";
    return c.normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function folded(s) {
    var out = "", map = [];
    for (var i = 0; i < s.length; i++) {
      var f = fold(s.charAt(i));
      for (var k = 0; k < f.length; k++) { out += f.charAt(k); map.push(i); }
    }
    return { text: out, map: map };
  }
  function norm(s) { return folded(String(s || "")).text; }

  function textNodes(el, skip) {
    var out = [];
    var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    for (var n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      if (skip && n.parentNode.closest(skip)) continue;
      out.push(n);
    }
    return out;
  }

  function mount(bar) {
    var scope = document.querySelector(bar.dataset.ssScope || "form");
    if (!scope) return;
    var cardSel = bar.dataset.ssCard || ".card";
    var headSel = bar.dataset.ssHead || "h2";
    var fieldSel = bar.dataset.ssField || ".form-group";
    var input = bar.querySelector(".ss-input");
    var clearBtn = bar.querySelector(".ss-clear");
    var count = bar.querySelector(".ss-count");
    var empty = bar.querySelector(".ss-empty");
    var resetBtn = bar.querySelector(".ss-reset");
    var chipsBox = bar.querySelector(".ss-chips");
    var say = {
      one: bar.dataset.ssOne || "1 Treffer",
      many: bar.dataset.ssMany || "{n} Treffer",
      none: bar.dataset.ssNone || "Nichts gefunden."
    };

    var sections = [];
    var chipOf = new Map();   // section element -> its chip
    function collect() {
      sections = Array.prototype.filter.call(scope.querySelectorAll(cardSel), function (c) {
        return !(c.parentElement && c.parentElement.closest(cardSel)) && !c.closest(".ss-bar");
      }).map(function (sec) {
        var head = sec.querySelector(headSel);
        return { el: sec, head: head, blocks: [] };
      });
    }
    collect();

    // Anchors of the sections, spelled the way admin-phone.js spells them, so
    // a link to #abschnitt-2 means the same section on a desk and on a phone.
    function sectionId(s, i) {
      if (!s.el.id) s.el.id = s.head && s.head.id ? s.head.id + "-sec" : "abschnitt-" + (i + 1);
      return s.el.id;
    }

    // ---- the settings of a section -------------------------------------
    // A setting is the smallest wrapper that carries a label and its control.
    // A control outside every wrapper is its own setting (or its label's).
    function blocksOf(sec) {
      var seen = [];
      Array.prototype.forEach.call(sec.querySelectorAll(CONTROL), function (el) {
        if (el.closest("[data-not-dirty], .ss-bar")) return;
        var b = el.closest(fieldSel);
        if (!b || b === sec || !sec.contains(b)) b = el.closest("label") || el;
        if (b === sec || seen.indexOf(b) >= 0) return;
        seen.push(b);
      });
      // Wrappers that hold several controls count as settings too, so their own
      // label ("Format") finds all that sits in them.
      Array.prototype.forEach.call(sec.querySelectorAll(fieldSel), function (b) {
        if (b !== sec && seen.indexOf(b) < 0 && b.querySelector(CONTROL)) seen.push(b);
      });
      return seen;
    }

    // Text of a block without the text of blocks inside it, plus the names of
    // its own controls.
    function ownHaystack(b, blocks) {
      var inner = blocks.filter(function (o) { return o !== b && b.contains(o); });
      var parts = [];
      textNodes(b, SKIP).forEach(function (n) {
        if (inner.some(function (o) { return o.contains(n); })) return;
        parts.push(n.nodeValue);
      });
      Array.prototype.forEach.call(b.querySelectorAll(CONTROL), function (el) {
        if (inner.some(function (o) { return o.contains(el); })) return;
        parts.push(el.name || "", String(el.name || "").replace(/[_\-.\[\]]+/g, " "), el.placeholder || "");
      });
      if (b.matches(CONTROL)) parts.push(b.name || "", String(b.name || "").replace(/[_\-.\[\]]+/g, " "));
      return norm(parts.join(" "));
    }

    function prepare() {
      sections.forEach(function (s) {
        s.title = norm(s.head ? s.head.textContent : "");
        s.blocks = blocksOf(s.el);
        s.own = s.blocks.map(function (b) { return ownHaystack(b, s.blocks); });
      });
    }

    // ---- marks ----------------------------------------------------------
    function clearMarks() {
      Array.prototype.forEach.call(scope.querySelectorAll("mark.ss-mark"), function (m) {
        var p = m.parentNode;
        p.replaceChild(document.createTextNode(m.textContent), m);
        p.normalize();
      });
    }
    function markIn(el, terms) {
      textNodes(el, SKIP + ", mark").forEach(function (n) {
        var f = folded(n.nodeValue), ranges = [];
        terms.forEach(function (t) {
          for (var at = f.text.indexOf(t); at >= 0; at = f.text.indexOf(t, at + t.length)) {
            ranges.push([f.map[at], f.map[at + t.length - 1] + 1]);
          }
        });
        if (!ranges.length) return;
        ranges.sort(function (a, b) { return a[0] - b[0]; });
        var merged = [ranges[0].slice()];
        ranges.slice(1).forEach(function (r) {
          var last = merged[merged.length - 1];
          if (r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push(r.slice());
        });
        var frag = document.createDocumentFragment(), pos = 0, s = n.nodeValue;
        merged.forEach(function (r) {
          if (r[0] > pos) frag.appendChild(document.createTextNode(s.slice(pos, r[0])));
          var m = document.createElement("mark");
          m.className = "ss-mark";
          m.textContent = s.slice(r[0], r[1]);
          frag.appendChild(m);
          pos = r[1];
        });
        if (pos < s.length) frag.appendChild(document.createTextNode(s.slice(pos)));
        n.parentNode.replaceChild(frag, n);
      });
    }

    // ---- the run --------------------------------------------------------
    var openedDetails = [];
    function restoreFolds() {
      Array.prototype.forEach.call(scope.querySelectorAll(".ss-miss"), function (x) { x.classList.remove("ss-miss"); });
      openedDetails.forEach(function (d) { d.open = false; });
      openedDetails = [];
      sections.forEach(function (s) {
        // admin-phone.js folds sections on a phone: back to what the toggle says.
        var tg = s.el.querySelector(".adm-sec-toggle");
        if (tg) s.el.classList.toggle("is-collapsed", tg.getAttribute("aria-expanded") !== "true");
      });
      if (chipsBox) Array.prototype.forEach.call(chipsBox.children, function (a) { a.hidden = false; });
    }

    function announce(n) {
      count.textContent = n === 0 ? say.none : (n === 1 ? say.one : say.many.replace("{n}", n));
      empty.hidden = n !== 0;
    }

    function run() {
      var raw = input.value;
      var terms = norm(raw).split(/\s+/).filter(Boolean);
      clearMarks();
      restoreFolds();
      var on = terms.length > 0;
      scope.classList.toggle("ss-active", on);
      bar.classList.toggle("is-searching", on);
      clearBtn.hidden = !raw;
      if (!on) { count.textContent = ""; empty.hidden = true; return; }
      collect();
      prepare();
      var total = 0;
      sections.forEach(function (s) {
        var all = function (hay) { return terms.every(function (t) { return hay.indexOf(t) >= 0; }); };
        var titleHit = all(s.title);
        // A setting also matches through the words of the blocks it sits in
        // and the section's title.
        var hit = s.blocks.map(function (b, i) {
          var hay = s.own[i] + " " + s.title;
          s.blocks.forEach(function (o, j) { if (o !== b && o.contains(b)) hay += " " + s.own[j]; });
          return all(hay);
        });
        var shown = [];
        s.blocks.forEach(function (b, i) {
          if (!hit[i]) return;
          shown.push(b);
          for (var p = b.parentElement; p && s.el.contains(p) && p !== s.el; p = p.parentElement) {
            var j = s.blocks.indexOf(p);
            if (j >= 0 && shown.indexOf(p) < 0) shown.push(p);
          }
        });
        // Settings, not wrappers: a block with a hit inside it counts through that hit.
        var leaves = s.blocks.filter(function (b, i) {
          return hit[i] && !s.blocks.some(function (o, j) { return o !== b && hit[j] && b.contains(o); });
        });
        var n = leaves.length;
        var has = n > 0 || titleHit;
        s.el.classList.toggle("ss-miss", !has);
        if (chipOf.has(s.el)) chipOf.get(s.el).hidden = !has;
        if (!has) return;
        total += n;
        s.el.classList.remove("is-collapsed");
        s.blocks.forEach(function (b) {
          if (shown.indexOf(b) < 0 && !titleHit) b.classList.add("ss-miss");
        });
        // What else stands on the way to a hit (hints, sub-headings, previews)
        // goes while searching, unless it carries a hit or the title.
        if (!titleHit) {
          var keep = shown;
          var walk = function (node) {
            Array.prototype.forEach.call(node.children, function (c) {
              if (c === s.head || (s.head && (c.contains(s.head) || s.head.contains(c)))) return;
              if (s.blocks.indexOf(c) >= 0) return;
              var carries = keep.some(function (b) { return c === b || c.contains(b) || b.contains(c); });
              if (!carries) { c.classList.add("ss-miss"); return; }
              if (keep.indexOf(c) < 0) walk(c);
            });
          };
          walk(s.el);
        }
        // Closed <details> around a hit open for the time of the search.
        leaves.forEach(function (b) {
          for (var d = b.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
            if (!d.open) { d.open = true; openedDetails.push(d); }
          }
        });
        if (s.head) markIn(s.head, terms);
        leaves.forEach(function (b) { markIn(b, terms); });
      });
      announce(total);
    }

    var queued = false;
    function schedule() {
      if (queued) return;
      queued = true;
      (window.requestAnimationFrame || setTimeout)(function () { queued = false; run(); });
    }

    function clear(refocus) {
      input.value = "";
      run();
      if (refocus) input.focus();
    }

    input.addEventListener("input", schedule);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { if (input.value) { e.preventDefault(); clear(true); } }
      else if (e.key === "Enter") e.preventDefault();
    });
    clearBtn.addEventListener("click", function () { clear(true); });
    if (resetBtn) resetBtn.addEventListener("click", function () { clear(true); });

    // A required field that a filter hides cannot be focused, so the browser
    // would refuse to send the form without a word. Show everything, then ask
    // again where the problem is.
    scope.addEventListener("invalid", function (e) {
      var el = e.target;
      if (!scope.classList.contains("ss-active") || !el.closest || !el.closest(".ss-miss")) return;
      clear(false);
      setTimeout(function () { try { el.focus(); el.reportValidity(); } catch (x) { /* ignore */ } }, 0);
    }, true);

    // The row of chips: one per section, built from the headings.
    if (bar.dataset.ssChips && chipsBox) {
      sections.forEach(function (s, i) {
        if (!s.head) return;
        var a = document.createElement("a");
        a.href = "#" + sectionId(s, i);
        a.textContent = s.head.textContent.replace(/\s+/g, " ").trim();
        chipsBox.appendChild(a);
        chipOf.set(s.el, a);
      });
      chipsBox.hidden = chipsBox.children.length < 4;
    }
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".ss-bar"), mount);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
