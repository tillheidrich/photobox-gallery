/* On-screen keyboard for every text, number and password field of the booth's
   pages (kiosk, admin, templates). Windows does not open its own touch
   keyboard inside the kiosk window (WebView2, 16.11.2026), and the booth PC
   often has nothing but the touch screen.

   One file, no markup in the pages: it listens for focus on a field, opens
   at the bottom of the screen, scrolls the field above itself and types into
   it with the same "input" event a real key sends, so every page script sees
   ordinary typing. A real keyboard keeps working: its first key closes this
   one. Profile switch controls.onscreen_keyboard (default on).
   Under the page CSP: no inline script or handler, styles live in app.css. */
(() => {
  "use strict";
  const lang = () => (document.documentElement.lang || "de").slice(0, 2);
  const T = (key, de, en) => {
    const all = window.BOOTH_I18N || {};
    return ((all[lang()] || {})[key]) || (lang() === "en" ? en : de);
  };
  const TEXT_TYPES = new Set(["", "text", "search", "email", "url", "tel", "password", "number"]);
  let enabled = true;
  let target = null;        // the field typed into
  let layer = "letters";    // letters | symbols | numbers
  let shift = false;
  let hardware = false;     // a real keyboard was used: stay closed until the next tap
  let kb = null;
  let repeat = null;

  function eligible(el) {
    if (!el || el.disabled || el.readOnly) return false;
    if (el.closest && el.closest("[data-no-osk]")) return false;
    if (el.tagName === "TEXTAREA") return true;
    if (el.tagName !== "INPUT") return false;
    return TEXT_TYPES.has((el.getAttribute("type") || "").toLowerCase());
  }
  // Numbers and PINs get the number pad: type=number, inputmode numeric/decimal.
  function numeric(el) {
    const mode = (el.getAttribute("inputmode") || "").toLowerCase();
    return (el.getAttribute("type") || "").toLowerCase() === "number" || mode === "numeric" || mode === "decimal";
  }

  const LETTERS = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "ß", "__back"],
    ["q", "w", "e", "r", "t", "z", "u", "i", "o", "p", "ü"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"],
    ["__shift", "y", "x", "c", "v", "b", "n", "m", ",", ".", "-"],
    ["__symbols", "@", "__space", ".de", "__enter", "__close"],
  ];
  const SYMBOLS = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "__back"],
    ["!", "\"", "§", "$", "%", "&", "/", "(", ")", "=", "?"],
    ["+", "*", "#", "'", "_", ":", ";", "<", ">", "€"],
    ["[", "]", "{", "}", "\\", "|", "~", "^", "°", "@"],
    ["__letters", "@", "__space", ".", "__enter", "__close"],
  ];
  const NUMBERS = [
    ["7", "8", "9", "__back"],
    ["4", "5", "6", ","],
    ["1", "2", "3", "-"],
    ["0", ".", "__enter", "__close"],
  ];

  function label(k) {
    switch (k) {
      case "__back": return ["⌫", T("kbd.back", "Löschen", "Delete")];
      case "__shift": return ["⇧", T("kbd.shift", "Großbuchstaben", "Capitals")];
      case "__space": return ["", T("kbd.space", "Leerzeichen", "Space")];
      case "__enter": return ["↵", T("kbd.enter", "Fertig", "Done")];
      case "__close": return ["✕", T("kbd.close", "Tastatur schließen", "Close keyboard")];
      case "__symbols": return ["?123", T("kbd.symbols", "Zeichen", "Symbols")];
      case "__letters": return ["ABC", T("kbd.letters", "Buchstaben", "Letters")];
      default: return [shift && k.length === 1 ? k.toUpperCase() : k, ""];
    }
  }

  function build() {
    if (!kb) {
      kb = document.createElement("div");
      kb.className = "osk";
      kb.id = "osk";
      kb.setAttribute("role", "group");
      kb.hidden = true;
      // The field keeps the focus: no key may take it.
      kb.addEventListener("mousedown", (e) => e.preventDefault());
      kb.addEventListener("pointerdown", onDown);
      ["pointerup", "pointercancel", "pointerleave"].forEach((t) => kb.addEventListener(t, stopRepeat));
      document.body.appendChild(kb);
    }
    kb.setAttribute("aria-label", T("kbd.title", "Bildschirmtastatur", "On-screen keyboard"));
    kb.dataset.layer = layer;
    const rows = layer === "numbers" ? NUMBERS : layer === "symbols" ? SYMBOLS : LETTERS;
    kb.replaceChildren(...rows.map((row) => {
      const r = document.createElement("div");
      r.className = "osk-row";
      for (const k of row) {
        const b = document.createElement("button");
        b.type = "button";
        b.tabIndex = -1;
        b.className = "osk-key" + (k.startsWith("__") ? " osk-key--" + k.slice(2) : "") + (k.length > 2 && !k.startsWith("__") ? " osk-key--wide" : "");
        if (k === "__shift" && shift) b.classList.add("is-on");
        const [text, aria] = label(k);
        b.textContent = text;
        if (aria) b.setAttribute("aria-label", aria);
        b.dataset.k = k;
        r.appendChild(b);
      }
      return r;
    }));
  }

  function onDown(e) {
    const b = e.target.closest(".osk-key");
    if (!b) return;
    e.preventDefault();
    press(b.dataset.k);
    if (b.dataset.k === "__back") {
      stopRepeat();
      repeat = setTimeout(function again() { press("__back"); repeat = setTimeout(again, 70); }, 450);
    }
  }
  function stopRepeat() { clearTimeout(repeat); repeat = null; }

  function caret(el) {
    try {
      if (typeof el.selectionStart === "number") return [el.selectionStart, el.selectionEnd];
    } catch (err) { /* email and number fields have no caret API */ }
    return null;
  }
  function insert(el, text) {
    const max = el.maxLength > 0 ? el.maxLength : Infinity;
    const at = caret(el);
    if (at) {
      if (el.value.length - (at[1] - at[0]) + text.length > max) return;
      el.setRangeText(text, at[0], at[1], "end");
    } else {
      if (el.value.length + text.length > max) return;
      el.value = el.value + text;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function backspace(el) {
    const at = caret(el);
    if (at && at[0] !== at[1]) el.setRangeText("", at[0], at[1], "end");
    else if (at && at[0] > 0) el.setRangeText("", at[0] - 1, at[0], "end");
    else if (!at) el.value = el.value.slice(0, -1);
    else return;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function press(k) {
    const el = target;
    if (!el || !document.contains(el)) return hide();
    switch (k) {
      case "__back": return backspace(el);
      case "__shift": shift = !shift; return build();
      case "__symbols": layer = "symbols"; return build();
      case "__letters": layer = "letters"; return build();
      case "__space": return insert(el, " ");
      case "__close": hide(); el.blur(); return;
      case "__enter":
        if (el.tagName === "TEXTAREA") return insert(el, "\n");
        el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        hide(); el.blur(); return;
      default:
        insert(el, shift && k.length === 1 ? k.toUpperCase() : k);
        // One capital, then small letters again, as on a phone.
        if (shift) { shift = false; build(); }
    }
  }

  // Keep the field in view above the keyboard: the page gets room at the
  // bottom as tall as the keyboard, then the field is scrolled up.
  function keepVisible(el) {
    const h = kb.getBoundingClientRect().height;
    document.documentElement.style.setProperty("--osk-height", h + "px");
    document.body.classList.add("osk-open");
    const r = el.getBoundingClientRect();
    const room = window.innerHeight - h - 24;
    if (r.bottom > room || r.top < 0) {
      const scroller = document.scrollingElement || document.documentElement;
      const delta = r.top < 0 ? r.top - 24 : r.bottom - room;
      scroller.scrollBy({ top: delta, behavior: "auto" });
      // A field inside its own scrolling box (a dialog) moves there too.
      if (el.getBoundingClientRect().bottom > room) el.scrollIntoView({ block: "center" });
    }
  }

  function show(el) {
    if (!enabled || hardware || !eligible(el)) return;
    target = el;
    const wanted = numeric(el) ? "numbers" : (layer === "numbers" ? "letters" : layer);
    if (wanted !== layer || !kb) { layer = wanted; shift = false; }
    build();
    kb.hidden = false;
    requestAnimationFrame(() => keepVisible(el));
  }
  function hide() {
    stopRepeat();
    if (kb) kb.hidden = true;
    target = null;
    document.body.classList.remove("osk-open");
  }

  document.addEventListener("focusin", (e) => show(e.target));
  document.addEventListener("focusout", () => {
    // A key does not take the focus; anything else that does closes the keyboard.
    setTimeout(() => { if (!eligible(document.activeElement)) hide(); }, 0);
  });
  // A tap into a field brings the keyboard back after "close" or a real keyboard.
  document.addEventListener("pointerdown", (e) => {
    if (kb && kb.contains(e.target)) return;
    hardware = false;
    if (eligible(e.target) && e.target === document.activeElement) show(e.target);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (!e.isTrusted || !kb || kb.hidden) return;
    if (e.key === "Escape") { hide(); return; }
    if (e.key.length === 1 || e.key === "Backspace") { hardware = true; hide(); }
  }, true);

  // The switch: off in the profile means the page behaves as before.
  window.BoothKeyboard = {
    setEnabled(on) { enabled = on !== false; if (!enabled) hide(); },
    get enabled() { return enabled; },
  };
  fetch("/api/state", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((s) => {
    const c = s && s.profile && s.profile.controls;
    if (c && c.onscreen_keyboard === false) window.BoothKeyboard.setEnabled(false);
  }).catch(() => {});
})();
