/* Script of hilfe.html, moved out of the page so its Content-Security-Policy
   can say script-src 'self' (R2-U1). */
  // Language: what was chosen last, otherwise what the browser speaks.
  (function () {
    var back = { de: "Zurück zur Verwaltung", en: "Back to settings" };
    function show(lang) {
      document.querySelectorAll("main").forEach(function (m) { m.hidden = m.lang !== lang; });
      document.querySelectorAll("[data-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.lang === lang)); });
      document.documentElement.lang = lang;
      document.getElementById("back").textContent = back[lang];
      try { localStorage.setItem("booth-help-lang", lang); } catch (e) {}
    }
    var saved = null;
    try { saved = localStorage.getItem("booth-help-lang"); } catch (e) {}
    show(saved || ((navigator.language || "de").slice(0, 2) === "en" ? "en" : "de"));
    document.querySelectorAll("[data-lang]").forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.lang); }); });
  })();

  // Buttons that lead to another page (data-href): no inline onclick, the
  // page's CSP allows none (R2-U1).
  document.querySelectorAll("[data-href]").forEach((b) => b.addEventListener("click", () => { location.href = b.dataset.href; }));
