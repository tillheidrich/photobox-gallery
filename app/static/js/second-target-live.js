// The second-target card on a booth's sync page (app/templates/admin/_second_target_card.html).
// While a change or a check waits for the booth, ask the gallery every few
// seconds what the booth answered and show it, for two minutes at most: the
// booth calls about every 30 s, and a booth that does not call within two
// minutes is named on the card as silent. The form is refreshed only while
// nobody has typed in it, so a reply never overwrites what the operator is
// writing. The password field is never touched.
(() => {
  "use strict";
  const card = document.getElementById("zweites-ziel");
  if (!card) return;

  // "Zugang entfernen" and "Schlüssel annehmen" ask once more on the same button.
  card.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-zt-confirm]");
    if (!btn || btn.dataset.armed === "1") return;
    e.preventDefault();
    const label = btn.textContent;
    btn.dataset.armed = "1";
    btn.textContent = btn.dataset.ztConfirm;
    setTimeout(() => {
      if (btn.dataset.armed === "1") { btn.dataset.armed = ""; btn.textContent = label; }
    }, 6000);
  });

  const live = card.querySelector("[data-zt-live]");
  const form = card.querySelector("form.zt-form:not([data-host-key-form])");
  if (!live || !live.dataset.ztUrl) return;
  let touched = false;
  if (form) {
    form.addEventListener("input", () => { touched = true; });
    form.addEventListener("change", () => { touched = true; });
  }

  const EVERY_MS = 4000;
  const FOR_MS = 120000;
  const started = Date.now();

  function fillForm(values) {
    if (!form || touched || !values) return;
    for (const [name, value] of Object.entries(values)) {
      const el = form.elements.namedItem(name);
      if (!el || el.type === "password") continue;
      if (el.type === "checkbox") el.checked = !!value;
      else if (name === "port") el.value = value ? String(value) : "";
      else el.value = value == null ? "" : String(value);
    }
  }

  function setText(selector, attr, value, text) {
    const el = card.querySelector(selector);
    if (!el || value == null) return;
    el.setAttribute(attr, value);
    if (text != null) el.textContent = text;
  }

  async function poll() {
    let data;
    try {
      const r = await fetch(live.dataset.ztUrl, {credentials: "same-origin", cache: "no-store",
                                                  headers: {"Accept": "application/json"}});
      if (!r.ok || !(r.headers.get("Content-Type") || "").includes("json")) return;   // logged out: stop quietly
      data = await r.json();
    } catch (e) {
      data = null;                                       // a dropped request: try again next round
    }
    if (data && data.ok) {
      live.innerHTML = data.html;
      live.dataset.ztBusy = data.busy ? "1" : "0";
      if (!touched) {
        fillForm(data.form);
        setText("[data-form-source]", "data-form-source", data.form_source, data.form_note);
        setText("[data-password-note]", "data-password-note", data.password_note, null);
        const pw = card.querySelector("[data-password-note] b");
        if (pw && data.password_note_text) pw.textContent = data.password_note_text;
      }
      if (!data.busy) return;
    }
    if (Date.now() - started < FOR_MS) {
      setTimeout(poll, EVERY_MS);
    } else if (live.dataset.ztTimeout) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = live.dataset.ztTimeout;
      live.appendChild(p);
    }
  }

  if (live.dataset.ztBusy === "1") setTimeout(poll, EVERY_MS);
})();
