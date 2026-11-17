/* The client's picks (app/picks.py): named selections that stay with the
 * client, not with the browser. Loaded only for a logged-in client; without
 * one the gallery page does not include this file at all.
 *
 * No inline handlers: everything hangs on ids and data attributes, so the
 * page's content security policy can tighten later without touching this.
 */
(function () {
  'use strict';
  const bar = document.getElementById('clientPicks');
  const dataEl = document.getElementById('clientPicksData');
  if (!bar || !dataEl) return;
  const data = JSON.parse(dataEl.textContent || '{}');
  const api = bar.dataset.api;
  const T = data.t || {};
  const select = document.getElementById('clientPicksSelect');
  const countEl = document.getElementById('clientPicksCount');
  const onlyBox = document.getElementById('clientPicksOnly');
  const lbBtn = document.getElementById('lbPick');
  const zipBtn = document.getElementById('clientPicksZip');
  let selections = data.selections || [];
  let current = selections.length ? selections[0].id : null;
  let lbId = null;

  function sel() { return selections.find(s => s.id === current) || null; }
  function has(id) { return target().media_ids.indexOf(Number(id)) !== -1; }
  function fmt(text, n, max) { return String(text || '').replace('{n}', n).replace('{max}', max); }

  function fillSelect() {
    select.textContent = '';
    if (!selections.length) {
      const o = document.createElement('option');
      o.value = ''; o.textContent = data.default_name || 'Auswahl';
      select.appendChild(o);
    }
    selections.forEach(s => {
      const o = document.createElement('option');
      o.value = String(s.id); o.textContent = s.name;
      if (s.id === current) o.selected = true;
      select.appendChild(o);
    });
  }

  function mark(btn, on) {
    btn.classList.toggle('is-picked', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    const label = on ? T.remove : T.add;
    if (label) { btn.title = label; btn.setAttribute('aria-label', label); }
    const svg = btn.querySelector('svg');
    if (svg) svg.setAttribute('fill', on ? 'currentColor' : 'none');
  }

  function render() {
    const n = target().media_ids.length;
    countEl.textContent = data.max ? fmt(T.count_max, n, data.max) : fmt(T.count, n, '');
    document.querySelectorAll('.pick-dot').forEach(b => mark(b, has(b.dataset.id)));
    if (zipBtn) zipBtn.disabled = n === 0;
    document.querySelectorAll('.photo-item').forEach(el => {
      el.classList.toggle('is-picked', has(el.dataset.id));
      if (onlyBox && onlyBox.checked) el.style.display = has(el.dataset.id) ? '' : 'none';
      else if (el.dataset.pickHidden !== undefined) el.style.display = '';
    });
    if (lbBtn && lbId) mark(lbBtn, has(lbId));
  }

  async function call(path, body) {
    const r = await fetch(api + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    let j = {};
    try { j = await r.json(); } catch (e) { /* empty answer */ }
    return { status: r.status, body: j };
  }

  // A client with no selection yet gets one on the first pick. Every tap
  // before the server has answered waits for that same request: before,
  // each fast tap started its own and the list showed "Auswahl" twice.
  let creating = null;
  function ensureSelection() {
    if (current) return Promise.resolve(current);
    if (!creating) {
      creating = call('', { name: data.default_name || 'Auswahl' }).then(r => {
        if (r.status !== 200) throw new Error('create');
        let s = selections.find(x => x.id === r.body.id);
        if (!s) { s = { id: r.body.id, name: r.body.name, media_ids: [], count: 0 }; selections.push(s); }
        // picks made while the request was under way move into it
        s.media_ids = Array.from(new Set(s.media_ids.concat(draft.media_ids)));
        draft.media_ids = [];
        current = s.id;
        fillSelect();
        return current;
      }).finally(() => { creating = null; });
    }
    return creating;
  }
  // Picks made before the first selection exists live here until it does.
  const draft = { id: null, media_ids: [] };
  function target() { return sel() || draft; }

  function say(text) {
    if (window.pbToast) window.pbToast(text); else countEl.textContent = text;
  }

  // A tap shows at once; the requests for one photo go out one after
  // another, in the order of the taps (different photos do not wait for each
  // other). Before, the dot changed only when the server had answered - on
  // mobile data a few hundred ms per tap - and two quick taps on one dot both
  // read "not picked" and both sent "on", so it stayed picked.
  const queue = new Map();
  function toggle(id) {
    id = Number(id);
    const s = target();
    const on = s.media_ids.indexOf(id) === -1;
    if (on && data.max && s.media_ids.length >= data.max) { say(T.full); return; }
    if (on) s.media_ids.push(id); else s.media_ids = s.media_ids.filter(x => x !== id);
    render();
    const next = (queue.get(id) || Promise.resolve()).then(() => store(id, on));
    queue.set(id, next);
    next.then(() => { if (queue.get(id) === next) queue.delete(id); });
  }
  async function store(id, on) {
    try {
      const sid = await ensureSelection();
      const r = await call('/' + sid + '/items', { media_id: id, on: on });
      if (r.status === 200) return;
      undo(id, on);
      say(r.status === 409 ? T.full : T.failed);
    } catch (e) {
      undo(id, on);
      say(T.failed);
    }
  }
  function undo(id, on) {
    // Only if no later tap has changed this photo since.
    const s = target();
    const now = s.media_ids.indexOf(id) !== -1;
    if (now !== on) return;
    if (on) s.media_ids = s.media_ids.filter(x => x !== id); else s.media_ids.push(id);
    render();
  }

  document.querySelectorAll('.pick-dot').forEach(btn => {
    btn.addEventListener('click', e => {
      // The tile opens the large view on click; this button must not.
      e.preventDefault(); e.stopPropagation();
      toggle(btn.dataset.id);
    });
  });
  if (lbBtn) {
    lbBtn.addEventListener('click', e => { e.stopPropagation(); if (lbId) toggle(lbId); });
    const lb = document.getElementById('lightbox');
    if (lb) lb.addEventListener('pb:show', e => { lbId = e.detail && e.detail.id; render(); });
  }
  select.addEventListener('change', () => { current = Number(select.value) || null; render(); });
  document.getElementById('clientPicksNew').addEventListener('click', async () => {
    const name = window.prompt(T.new_prompt || '');
    if (!name || !name.trim()) return;
    const r = await call('', { name: name.trim() });
    if (r.status !== 200) { say(T.failed); return; }
    if (!selections.find(s => s.id === r.body.id)) {
      selections.push({ id: r.body.id, name: r.body.name, media_ids: [], count: 0 });
    }
    current = r.body.id;
    fillSelect();
    render();
  });
  if (zipBtn) zipBtn.addEventListener('click', async () => {
    // The stored selection as a ZIP, through the same route and switches as
    // "Meine Auswahl" (size choice included, if the gallery offers sizes).
    const s = sel();
    if (!s || !s.media_ids.length) return;
    const size = window.dlAskSize ? await window.dlAskSize() : 'original';
    if (size === null) return;
    window.location.href = zipBtn.dataset.zip + '?ids=' + s.media_ids.slice(0, 100).join(',')
      + '&size=' + encodeURIComponent(size);
  });
  if (onlyBox) onlyBox.addEventListener('change', () => {
    document.querySelectorAll('.photo-item').forEach(el => { el.dataset.pickHidden = ''; });
    render();
  });

  fillSelect();
  render();
})();
