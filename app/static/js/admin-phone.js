// Long admin forms on a phone: a row of section chips to jump with, sections
// that fold and remember it, a one-line summary on a folded section, a search
// over every setting, and a save bar that says when something is unsaved.
//
// Markup it reads (nothing here changes a field name or what is sent):
//   <div data-phone-sections="event-edit"              key for the stored state
//        data-section-card=".card"                     what a section is (default .card)
//        data-section-head=".card-header > h2"         its heading inside it
//        data-section-search>                          optional: add the search field
//   <form data-dirty-watch> ... <div class="adm-savebar"> ... </div></form>
// On a desk nothing folds and no chips appear: the layout there is unchanged.
(function () {
  "use strict";
  var phone = window.matchMedia("(max-width: 768px)");
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };
  var norm = function (s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim(); };

  function fieldLabel(el) {
    var lab = el.closest("label");
    if (lab) return lab.textContent;
    if (el.id) { var l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.textContent; }
    var g = el.closest(".form-group"); var gl = g && g.querySelector(".form-label");
    return gl ? gl.textContent : (el.getAttribute("aria-label") || el.name || "");
  }

  // What is set in a section, in one line: switched-on options by name, and how
  // many of the other fields carry a value.
  function summary(sec) {
    if (sec.dataset.sectionSummary) return sec.dataset.sectionSummary;
    var on = [], filled = 0, total = 0;
    sec.querySelectorAll("input, select, textarea").forEach(function (el) {
      if (el.type === "hidden" || el.type === "submit" || el.type === "button" || el.type === "file" || el.disabled) return;
      if (el.type === "checkbox" || el.type === "radio") {
        if (el.type === "radio" && !el.checked) return;
        total++;
        if (el.checked) on.push(fieldLabel(el).replace(/\s+/g, " ").trim().slice(0, 28));
        return;
      }
      total++;
      if (el.tagName === "SELECT") { if (el.selectedIndex > 0) filled++; }
      else if (String(el.value || "").trim()) filled++;
    });
    var parts = [];
    if (on.length) parts.push("An: " + on.slice(0, 3).join(", ") + (on.length > 3 ? " und " + (on.length - 3) + " mehr" : ""));
    if (filled) parts.push(filled + (filled === 1 ? " Feld ausgefüllt" : " Felder ausgefüllt"));
    if (!parts.length) parts.push(total ? total + (total === 1 ? " Einstellung" : " Einstellungen") + ", nichts gesetzt" : "Nur Hinweise");
    return parts.join(" · ");
  }

  function setup(root) {
    if (root.dataset.sectionsReady) return;
    root.dataset.sectionsReady = "1";
    var key = "pb-sections:" + (root.dataset.phoneSections || location.pathname) + ":";
    var cardSel = root.dataset.sectionCard || ".card";
    var headSel = root.dataset.sectionHead || ".card-header > h2";
    var secs = Array.prototype.filter.call(root.querySelectorAll(cardSel), function (c) {
      return c.querySelector(headSel) && !c.parentElement.closest(cardSel);
    });
    if (secs.length < 3) return;

    var nav = document.createElement("nav");
    nav.className = "adm-jump";
    nav.setAttribute("aria-label", "Abschnitte");
    var chips = [];

    secs.forEach(function (sec, i) {
      var head = sec.querySelector(headSel);
      if (!sec.id) sec.id = (head.id ? head.id + "-sec" : "abschnitt-" + (i + 1));
      sec.classList.add("adm-sec");
      var title = head.textContent.replace(/\s+/g, " ").trim();
      // The heading becomes the switch: a real button, so it is reachable and announced.
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "adm-sec-toggle";
      btn.setAttribute("aria-controls", sec.id);
      while (head.firstChild) btn.appendChild(head.firstChild);
      head.appendChild(btn);
      var sum = document.createElement("span");
      sum.className = "adm-sec-summary";
      btn.appendChild(sum);
      var stored = store.get(key + sec.id);
      var open = stored === null ? i === 0 : stored === "1";
      function apply(o, save) {
        sec.classList.toggle("is-collapsed", !o);
        btn.setAttribute("aria-expanded", o ? "true" : "false");
        if (!o) sum.textContent = summary(sec);
        if (save) store.set(key + sec.id, o ? "1" : "0");
      }
      apply(open, false);
      btn.addEventListener("click", function () { apply(sec.classList.contains("is-collapsed"), true); });
      sec._open = function () { apply(true, true); };

      var a = document.createElement("a");
      a.href = "#" + sec.id;
      a.textContent = title;
      a.addEventListener("click", function (e) {
        e.preventDefault();
        sec._open();
        sec.scrollIntoView({ block: "start", behavior: "smooth" });
        history.replaceState(null, "", "#" + sec.id);
      });
      nav.appendChild(a);
      chips.push([a, sec]);
    });

    // The slot marks where the row goes, but the row must not live inside it:
    // a sticky element only sticks within its parent, and an empty slot div
    // would let the row scroll away with the page header. The row takes the
    // slot's place, so its parent is the whole form and it stays on screen.
    var slot = root.querySelector("[data-section-nav]");
    if (slot) slot.parentNode.replaceChild(nav, slot); else root.insertBefore(nav, root.firstChild);

    // The chip of the section on screen is marked, and kept in view in its row.
    // Only the row scrolls, sideways. scrollIntoView() is not used here on
    // purpose: it also scrolls every scrollable ancestor, the page included, so
    // with the row off screen it dragged the page back up to it while the
    // operator was scrolling down the form (and cut a chip click's smooth scroll short).
    function centerChip(a) {
      var left = a.offsetLeft - (nav.clientWidth - a.offsetWidth) / 2;
      left = Math.max(0, Math.min(left, nav.scrollWidth - nav.clientWidth));
      if (Math.abs(nav.scrollLeft - left) < 1) return;
      if (nav.scrollTo) nav.scrollTo({ left: left, behavior: "smooth" }); else nav.scrollLeft = left;
    }
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          chips.forEach(function (c) {
            var on = c[1] === en.target;
            c[0].classList.toggle("is-here", on);
            if (on) centerChip(c[0]);
          });
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      secs.forEach(function (s) { io.observe(s); });
    }

    // A required field inside a folded section: open it, or the browser
    // refuses to send the form without showing why.
    root.addEventListener("invalid", function (e) {
      var sec = e.target.closest(".adm-sec.is-collapsed");
      if (sec && sec._open) sec._open();
    }, true);

    // The search field goes in before the arrival jump below, so the jump
    // measures the final layout.
    // One search per page: the shared bar (settings-search.js) wins where it stands.
    if (root.hasAttribute("data-section-search") && !document.querySelector(".ss-bar")) addSearch(root, secs, nav);

    // Arriving with #anchor: open that section and put the target under the
    // sticky bars, once, now. The browser's own jump happened before the
    // sections folded (or before generated ids like #abschnitt-2 existed), so
    // it points at the wrong place. This runs once at load and never again:
    // nothing after it moves the page unless the operator taps a chip.
    if (location.hash) {
      var id = location.hash.slice(1);
      try { id = decodeURIComponent(id); } catch (e) { /* keep it as it is */ }
      var t = document.getElementById(id);
      var s = t && (t.classList.contains("adm-sec") ? t : t.closest(".adm-sec"));
      if (s && s._open) {
        s._open();
        var bar = document.querySelector(".admin-topbar");
        var above = (bar ? bar.getBoundingClientRect().height : 0) + nav.getBoundingClientRect().height + 8;
        var y = Math.max(0, t.getBoundingClientRect().top + window.pageYOffset - above);
        // Instant like the browser's own anchor jump; html has scroll-behavior: smooth.
        try { window.scrollTo({ top: y, behavior: "instant" }); } catch (e) { window.scrollTo(0, y); }
      }
    }
  }

  // "Einstellung suchen": hides every field that does not match and opens the
  // sections that hold a match, so each result stands under its section title.
  function addSearch(root, secs, nav) {
    var wrap = document.createElement("div");
    wrap.className = "adm-search";
    wrap.innerHTML = '<label class="form-label" for="admSearch">Einstellung suchen</label>' +
      '<input id="admSearch" class="form-control" type="search" inputmode="search" autocomplete="off" enterkeyhint="search" placeholder="z. B. Countdown, Drucker, Farbe">' +
      '<p class="adm-search-result" aria-live="polite"></p>';
    nav.parentNode.insertBefore(wrap, nav);
    var input = wrap.querySelector("input"), out = wrap.querySelector(".adm-search-result");
    // A field block: the smallest wrapper that carries one setting and its label.
    var blockSel = root.dataset.sectionField || ".form-group, label, .bp-row > div";
    var run = function () {
      var q = norm(input.value);
      var hits = 0, where = 0;
      root.classList.toggle("is-searching", !!q);
      secs.forEach(function (sec) {
        sec.querySelectorAll(".adm-search-miss").forEach(function (x) { x.classList.remove("adm-search-miss"); });
        var blocks = Array.prototype.filter.call(sec.querySelectorAll(blockSel), function (b) {
          return b.querySelector("input:not([type=hidden]), select, textarea") || b.matches("label");
        });
        var title = norm(sec.querySelector(root.dataset.sectionHead || "h2").textContent);
        var secHit = q && title.indexOf(q) >= 0;
        var n = 0;
        blocks.forEach(function (b) {
          var m = !q || secHit || norm(b.textContent).indexOf(q) >= 0 ||
            Array.prototype.some.call(b.querySelectorAll("input, select, textarea"), function (el) {
              return norm(el.name + " " + (el.placeholder || "")).indexOf(q) >= 0;
            });
          b.classList.toggle("adm-search-miss", !m);
          if (m && q) n++;
        });
        // A hit keeps what it sits in and what sits in it.
        blocks.forEach(function (b) {
          if (b.classList.contains("adm-search-miss")) return;
          b.querySelectorAll(".adm-search-miss").forEach(function (x) { x.classList.remove("adm-search-miss"); });
          for (var p = b.parentElement && b.parentElement.closest(".adm-search-miss"); p && sec.contains(p); p = p.parentElement && p.parentElement.closest(".adm-search-miss")) p.classList.remove("adm-search-miss");
        });
        var keep = blocks.filter(function (b) { return !b.classList.contains("adm-search-miss"); });
        // Count settings, not wrappers: a hit inside another hit is one.
        if (q) n = keep.filter(function (b) { return !keep.some(function (o) { return o !== b && o.contains(b); }); }).length;
        // Whatever else stands in the section (hints, sub-headings, previews) goes
        // while searching, unless it holds a hit.
        if (q && !secHit) {
          Array.prototype.forEach.call(sec.children, function (c) {
            if (c.querySelector(".adm-sec-toggle")) return;
            var has = keep.some(function (h) { return c === h || c.contains(h); });
            c.classList.toggle("adm-search-miss", !has);
          });
        }
        sec.classList.toggle("adm-search-empty", !!q && !n && !secHit);
        if (q && (n || secHit)) { where++; hits += n; sec.classList.remove("is-collapsed"); }
        else if (!q) { var b = sec.querySelector(".adm-sec-toggle"); if (b) sec.classList.toggle("is-collapsed", b.getAttribute("aria-expanded") !== "true"); }
      });
      out.textContent = q ? (hits ? hits + (hits === 1 ? " Treffer" : " Treffer") + " in " + where + (where === 1 ? " Bereich" : " Bereichen") : "Nichts gefunden. Anderes Wort versuchen.") : "";
    };
    input.addEventListener("input", run);
  }

  // Unsaved changes: the save bar says so, in words and colour.
  function watch(form) {
    var bar = form.querySelector(".adm-savebar, .bp-save");
    if (!bar) {
      var flag = function (e) { if (!e.target.closest("[data-not-dirty], .adm-search")) form.dataset.dirty = "1"; };
      form.addEventListener("input", flag);
      form.addEventListener("change", flag);
      return;
    }
    var state = bar.querySelector(".adm-savebar-state");
    if (!state) {
      state = document.createElement("span");
      state.className = "adm-savebar-state";
      state.setAttribute("aria-live", "polite");
      bar.insertBefore(state, bar.firstChild);
    }
    state.textContent = "Alles gespeichert";
    var mark = function (e) {
      if (e.target.closest("[data-not-dirty], .adm-search")) return;
      if (form.dataset.dirty) return;
      form.dataset.dirty = "1";
      bar.classList.add("is-dirty");
      state.textContent = "Ungespeicherte Änderungen";
    };
    form.addEventListener("input", mark);
    form.addEventListener("change", mark);
  }

  // "Live schalten" / "Zurück auf Entwurf" leaves the page: with unsaved
  // changes in the form they would be lost. The first press says so in the
  // panel itself (no native dialog); a second press switches anyway.
  function guardPublish() {
    document.addEventListener("submit", function (e) {
      var f = e.target;
      if (!f.classList || !f.classList.contains("pub-form")) return;
      var dirty = document.querySelector("form[data-dirty-watch][data-dirty]");
      if (!dirty || f.dataset.warned) return;
      e.preventDefault();
      f.dataset.warned = "1";
      var box = f.closest(".pub-switch");
      var note = document.createElement("p");
      note.className = "pub-dirty-note";
      note.setAttribute("role", "alert");
      note.textContent = (box && box.dataset.dirtyNote) || "Ungespeicherte Änderungen.";
      // On the event page the note gets a line of its own under the header
      // bar, so badge and button stay where they are.
      var at = f.closest(".pub-head") || f;
      at.parentNode.insertBefore(note, at.nextSibling);
    }, true);
  }

  function init() {
    guardPublish();
    document.querySelectorAll("form[data-dirty-watch]").forEach(watch);
    var go = function () { if (phone.matches) document.querySelectorAll("[data-phone-sections]").forEach(setup); };
    // The settings tabs scroll sideways on a phone: start with the current one in view.
    var cur = phone.matches && document.querySelector(".settings-tabs .active");
    if (cur) cur.parentNode.scrollLeft = cur.offsetLeft - (cur.parentNode.clientWidth - cur.offsetWidth) / 2;
    go();
    phone.addEventListener("change", go);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
