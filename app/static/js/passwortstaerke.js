/* Wie stark ist dieses Passwort — sichtbar, während es getippt wird.
 *
 * Dieselben Regeln wie in app/passwords.py, nur grob: Der Server entscheidet,
 * der Balken erklärt. Bewusst ohne Bibliothek und ohne Netz — eine Seite, die
 * ein Passwort entgegennimmt, lädt nichts nach.
 */
(function () {
  const BEKANNT = ["passwort", "password", "geheim", "admin", "fotobox", "photobox",
    "galerie", "gallery", "willkommen", "welcome", "sommer", "winter", "hochzeit",
    "qwertz", "qwerty", "letmein", "test", "start", "fotos", "bilder"];
  const REIHEN = ["0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertzuiopasdfghjklyxcvbnm"];
  const STUFEN = ["untauglich", "schwach", "geht so", "gut", "sehr gut"];

  const entleet = (s) => s.toLowerCase()
    .replace(/0/g, "o").replace(/1/g, "l").replace(/3/g, "e").replace(/4/g, "a")
    .replace(/5/g, "s").replace(/7/g, "t").replace(/@/g, "a").replace(/\$/g, "s");

  function bewerten(pw) {
    const gruende = [];
    let punkte = 0;
    if (pw.length >= 10) punkte++;
    if (pw.length >= 13) punkte++;
    if (pw.length >= 16) punkte++;
    const arten = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/, /\s/].filter((r) => r.test(pw)).length;
    if (arten >= 3) punkte++;
    if (pw.length < 10) { gruende.push("mindestens 10 Zeichen"); punkte = 0; }
    const kern = entleet(pw).replace(/[\d\W_]+$/, "");
    if (BEKANNT.includes(kern) || BEKANNT.includes(entleet(pw))) {
      gruende.push("ein bekanntes Wort"); punkte = 0;
    }
    const klein = pw.toLowerCase();
    if (REIHEN.some((r) => { for (let i = 0; i + 5 <= klein.length; i++) { if (r.includes(klein.slice(i, i + 5))) return true; } return false; })) {
      gruende.push("eine Zahlen- oder Tastaturreihe"); punkte = Math.min(punkte, 1);
    }
    if (/(.)\1{3,}/.test(pw)) { gruende.push("nur Wiederholungen"); punkte = Math.min(punkte, 1); }
    return { punkte: Math.max(0, Math.min(4, punkte)), gruende };
  }

  function anhaengen(feld) {
    const box = document.createElement("div");
    box.className = "pw-staerke";
    box.innerHTML = '<div class="pw-balken"><i></i></div><p class="form-hint pw-text" aria-live="polite"></p>';
    feld.insertAdjacentElement("afterend", box);
    const balken = box.querySelector("i");
    const text = box.querySelector(".pw-text");
    const zeigen = () => {
      const pw = feld.value;
      if (!pw) { box.dataset.stufe = ""; balken.style.width = "0"; text.textContent = ""; return; }
      const { punkte, gruende } = bewerten(pw);
      box.dataset.stufe = String(punkte);
      balken.style.width = (punkte + 1) * 20 + "%";
      text.textContent = gruende.length
        ? `${STUFEN[punkte]} — enthält ${gruende.join(" und ")}. Am einfachsten ist es lang: vier Wörter, die sonst niemand zusammen sagt.`
        : `${STUFEN[punkte]}${punkte < 2 ? " — länger wäre besser." : ""}`;
    };
    feld.addEventListener("input", zeigen);
    zeigen();
  }

  document.querySelectorAll('input[type="password"][data-staerke]').forEach(anhaengen);
})();
