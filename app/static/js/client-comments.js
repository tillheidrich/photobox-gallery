/* Comments of a logged-in client (app/comments.py). Loaded only when the
 * gallery allows comments and a client holds it. Text goes in and out as
 * text (textContent), never as HTML. No inline handlers.
 */
(function () {
  'use strict';
  const panel = document.getElementById('commentPanel');
  const dataEl = document.getElementById('commentData');
  if (!panel || !dataEl || !panel.showModal) return;
  const data = JSON.parse(dataEl.textContent || '{}');
  const T = data.t || {};
  const list = document.getElementById('commentList');
  const title = document.getElementById('commentTitle');
  const bodyEl = document.getElementById('commentBody');
  const lb = document.getElementById('lightbox');
  let target = null;           // media id, or null for the gallery
  let lbId = null;

  function say(text) { if (window.pbToast) window.pbToast(text); }

  async function load() {
    const r = await fetch(data.api, { credentials: 'same-origin' });
    const j = r.ok ? await r.json() : { comments: [] };
    const mine = (j.comments || []).filter(c => (c.media_id || null) === target);
    list.textContent = '';
    if (!mine.length) {
      const li = document.createElement('li'); li.textContent = T.empty || ''; list.appendChild(li);
    }
    mine.forEach(c => {
      const li = document.createElement('li');
      if (c.from_operator) li.classList.add('is-operator');
      const meta = document.createElement('span');
      meta.className = 'comment-meta';
      meta.textContent = (c.from_operator ? (T.operator || c.author) : c.author) + ' · ' + String(c.created_at).slice(0, 16);
      li.appendChild(meta);
      li.appendChild(document.createTextNode(c.body));
      list.appendChild(li);
    });
  }

  function open(mediaId) {
    target = mediaId ? Number(mediaId) : null;
    title.textContent = target ? T.picture : T.gallery;
    // The large view makes everything outside it inert; the panel has to live inside it then.
    (lb && lb.classList.contains('open') ? lb : document.body).appendChild(panel);
    bodyEl.value = '';
    panel.showModal();
    load();
    bodyEl.focus();
  }

  document.getElementById('commentSend').addEventListener('click', async () => {
    const text = bodyEl.value.trim();
    if (!text) return;
    const payload = { body: text };
    if (target) payload.media_id = target;
    const r = await fetch(data.api, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (r.status === 429) { say(T.too_often); return; }
    if (!r.ok) { say(T.failed); return; }
    bodyEl.value = '';
    load();
  });

  const galleryBtn = document.getElementById('galleryComment');
  if (galleryBtn) galleryBtn.addEventListener('click', () => open(null));
  const lbBtn = document.getElementById('lbComment');
  if (lbBtn) lbBtn.addEventListener('click', e => { e.stopPropagation(); if (lbId) open(lbId); });
  if (lb) lb.addEventListener('pb:show', e => { lbId = e.detail && e.detail.id; });
  // Keys typed in the panel must not page through the large view behind it.
  panel.addEventListener('keydown', e => e.stopPropagation());
})();
