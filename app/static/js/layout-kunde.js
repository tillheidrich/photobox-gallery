// The customer's layout page. Every change asks the gallery for its own render
// (debounced) and saves the draft (debounced a little longer). Plain ES2020,
// no build step, no request to any other host.
(function () {
  "use strict";
  const root = document.querySelector(".lk");
  const form = document.getElementById("lk-form");
  if (!root || !form) return;
  const base = root.dataset.base;
  const editable = ["offen", "entwurf", "zurueck"].includes(root.dataset.status);
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const img = document.getElementById("lk-img");
  const sheet = document.getElementById("lk-sheet");
  const errorBox = document.getElementById("lk-error");
  const saved = document.getElementById("lk-saved");
  const send = document.getElementById("lk-send");
  const hasLogo = document.getElementById("lk-has-logo");
  // Words in the page's language, from <script id="lk-texts">; German if absent.
  let T = {};
  try { T = JSON.parse((document.getElementById("lk-texts") || {}).textContent || "{}"); } catch (e) { T = {}; }
  const say = (key, fallback) => T[key] || fallback;

  function values() {
    const picked = (name) => (form.querySelector(`input[name="${name}"]:checked`) || {}).value || "";
    const fields = {};
    form.querySelectorAll('input[name="field"]').forEach((el) => { fields[el.dataset.key] = el.value; });
    return { template: picked("template"), fields, palette: picked("palette"), font: picked("font"),
             logo: !!(hasLogo && hasLogo.value) };
  }

  function showError(text) {
    errorBox.textContent = text || "";
    errorBox.hidden = !text;
  }

  async function post(path, body) {
    return fetch(base + path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body) });
  }

  let previewTimer = 0, previewSeq = 0, objectUrl = "";
  async function refreshPreview() {
    const seq = ++previewSeq;
    sheet.setAttribute("data-loading", "1");
    try {
      const r = await post("/vorschau", values());
      if (seq !== previewSeq) return;            // a newer change is on its way
      if (r.status === 429) { schedulePreview(1500); return; }
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        showError(data.error || say("preview_failed", "Die Vorschau ließ sich nicht berechnen."));
        return;
      }
      showError(r.headers.get("X-Layout-Too-Wide") === "1"
        ? say("too_wide", "Ein Text ist breiter als das Blatt und wird abgeschnitten. Bitte kürzer fassen.") : "");
      const blob = await r.blob();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      img.src = objectUrl;
    } catch (e) {
      if (seq === previewSeq) showError(say("offline", "Keine Verbindung. Wir versuchen es gleich noch einmal."));
      schedulePreview(4000);
    } finally {
      if (seq === previewSeq) sheet.removeAttribute("data-loading");
    }
  }
  function schedulePreview(ms) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, ms);
  }

  let saveTimer = 0;
  async function saveDraft() {
    if (!editable) return;
    const r = await post("/entwurf", values()).catch(() => null);
    if (!saved) return;
    if (r && r.ok) saved.textContent = say("saved", "Entwurf gespeichert");
    else if (r && r.status === 409) location.reload();
    else saved.textContent = say("not_saved", "Noch nicht gespeichert");
  }
  function scheduleSave() {
    if (!editable) return;
    if (saved) saved.textContent = say("saving", "Speichert …");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 1200);
  }

  function changed() {
    schedulePreview(350);
    scheduleSave();
  }
  form.addEventListener("input", (e) => { if (e.target.type !== "file") changed(); });
  form.addEventListener("change", (e) => { if (e.target.type === "radio") changed(); });
  form.addEventListener("submit", (e) => e.preventDefault());

  if (send) {
    send.addEventListener("click", async () => {
      send.disabled = true;
      clearTimeout(saveTimer);
      const r = await post("/abschicken", values()).catch(() => null);
      if (r && r.ok) { location.reload(); return; }
      const data = r ? await r.json().catch(() => ({})) : {};
      showError(data.error || say("send_failed", "Abschicken hat nicht geklappt. Bitte noch einmal versuchen."));
      send.disabled = false;
    });
  }

  const file = document.getElementById("lk-logo-file");
  const remove = document.getElementById("lk-logo-remove");
  const logoError = document.getElementById("lk-logo-error");
  if (file) {
    file.addEventListener("change", async () => {
      if (!file.files.length) return;
      const data = new FormData();
      data.append("logo", file.files[0]);
      logoError.hidden = true;
      const label = document.querySelector('label[for="lk-logo-file"]');
      label.textContent = say("uploading", "Lädt hoch …");
      const r = await fetch(base + "/logo", { method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf }, body: data }).catch(() => null);
      file.value = "";
      if (r && r.ok) {
        hasLogo.value = "1";
        remove.hidden = false;
        label.textContent = say("logo_other", "Anderes Logo wählen");
        changed();
      } else {
        const answer = r ? await r.json().catch(() => ({})) : {};
        logoError.textContent = answer.error || say("logo_failed", "Das Logo ließ sich nicht hochladen.");
        logoError.hidden = false;
        label.textContent = hasLogo.value ? say("logo_other", "Anderes Logo wählen") : say("logo_upload", "Logo hochladen");
      }
    });
  }
  if (remove) {
    remove.addEventListener("click", async () => {
      const r = await fetch(base + "/logo/entfernen", { method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf } }).catch(() => null);
      if (r && r.ok) {
        hasLogo.value = "";
        remove.hidden = true;
        document.querySelector('label[for="lk-logo-file"]').textContent = say("logo_upload", "Logo hochladen");
        changed();
      }
    });
  }

  // The "Vorschau ansehen" button on a phone: shown while the sheet is out of view.
  const jump = document.getElementById("lk-jump");
  if (jump && "IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      jump.hidden = entries.some((e) => e.isIntersecting);
    }, { threshold: 0.15 }).observe(document.getElementById("lk-s4").parentElement);
  }

  refreshPreview();
})();
