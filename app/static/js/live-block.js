// A block that follows the booths while one of them is about to take a change
// (event page, "Box bei diesem Event"). Every element with data-live-url and
// data-live-busy="1" asks its URL every few seconds for two minutes at most;
// the answer is {busy, html} and the html replaces [data-live-body]. Stops as
// soon as nothing waits, or quietly when the answer is not JSON (logged out).
(() => {
  "use strict";
  const EVERY_MS = 4000;
  const FOR_MS = 120000;
  document.querySelectorAll("[data-live-url]").forEach((block) => {
    if (block.dataset.liveBusy !== "1" || block.dataset.liveStarted) return;
    block.dataset.liveStarted = "1";
    const started = Date.now();
    async function poll() {
      try {
        const r = await fetch(block.dataset.liveUrl, {credentials: "same-origin", cache: "no-store",
                                                      headers: {"Accept": "application/json"}});
        if (!r.ok || !(r.headers.get("Content-Type") || "").includes("json")) return;
        const data = await r.json();
        const body = block.querySelector("[data-live-body]");
        if (body && typeof data.html === "string") body.outerHTML = data.html;
        block.dataset.liveBusy = data.busy ? "1" : "0";
        if (!data.busy) return;
      } catch (e) {
        // a dropped request: try again next round
      }
      if (Date.now() - started < FOR_MS) setTimeout(poll, EVERY_MS);
    }
    setTimeout(poll, EVERY_MS);
  });
})();
