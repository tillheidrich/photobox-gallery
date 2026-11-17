/* Manage mode on the gallery page (?ansicht=verwalten), owner only.
 *
 * The page is the guests' page; this adds filters, multi-select with bulk
 * actions and the tools in the large view. Every change goes to one admin
 * endpoint (admin.manage_media) with the session's CSRF token in a header.
 * The server never sends this file's data to a guest: the template includes
 * it only when the owner is logged in and asked for manage mode.
 */
(function () {
  'use strict';
  const dataEl = document.getElementById('mgData');
  if (!dataEl) return;
  const D = JSON.parse(dataEl.textContent || '{}');
  const T = D.t || {};
  const grid = document.getElementById('photoGrid');
  const lb = document.getElementById('lightbox');
  const bulk = document.getElementById('mgBulk');
  const bulkCount = document.getElementById('mgBulkCount');
  const selBtn = document.getElementById('mgSelect');
  const tagMenu = document.getElementById('mgTagMenu');
  let selecting = false;
  let current = null;           // id shown in the large view
  let tagTarget = null;         // ids the colour menu applies to

  const tiles = () => Array.from(document.querySelectorAll('.photo-item'));
  const tile = id => document.querySelector('.photo-item[data-id="' + id + '"]');
  const say = msg => { if (window.pbToast) window.pbToast(msg); };

  async function send(action, ids, extra) {
    const r = await fetch(D.api, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': D.csrf },
      body: JSON.stringify(Object.assign({ action: action, ids: ids }, extra || {})),
    });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  // ── What a tile shows ────────────────────────────────────────
  function setHidden(el, hidden) {
    el.toggleAttribute('data-hidden', hidden);
    if (!hidden) el.removeAttribute('data-pending');
    let badge = el.querySelector('.mg-badge');
    if (hidden && !badge) {
      badge = document.createElement('span');
      badge.className = 'mg-badge';
      badge.textContent = T.hidden;
      (el.querySelector('.photo-open') || el).appendChild(badge);
    } else if (!hidden && badge) {
      badge.remove();
    }
  }
  function setTag(el, tag) {
    el.dataset.tag = tag || '';
    let dot = el.querySelector('.photo-tag');
    if (tag) {
      if (!dot) { dot = document.createElement('span'); dot.className = 'photo-tag'; (el.querySelector('.photo-open') || el).appendChild(dot); }
      dot.style.background = (D.colors || {})[tag] || '';
    } else if (dot) {
      dot.remove();
    }
  }

  // ── Filters ──────────────────────────────────────────────────
  let filter = '';
  function matches(el) {
    if (!filter) return true;
    if (filter === 'hidden') return el.hasAttribute('data-hidden');
    if (filter === 'pending') return el.hasAttribute('data-pending');
    if (filter === 'liked') return +(el.dataset.likes || 0) > 0;
    if (filter.indexOf('tag:') === 0) return el.dataset.tag === filter.slice(4);
    return true;
  }
  function applyFilter() {
    let n = 0;
    tiles().forEach(el => { const ok = matches(el); el.style.display = ok ? '' : 'none'; if (ok) n++; });
    const c = document.getElementById('photoCount');
    if (c && window._t) c.textContent = n + ' ' + (n !== 1 ? window._t.photoMany : window._t.photoOne);
  }
  document.querySelectorAll('[data-mgfilter]').forEach(chip => chip.addEventListener('click', () => {
    filter = chip.dataset.mgfilter;
    document.querySelectorAll('[data-mgfilter]').forEach(c => {
      c.classList.toggle('active', c === chip);
      c.setAttribute('aria-pressed', c === chip ? 'true' : 'false');
    });
    applyFilter();
  }));

  // ── Selecting ────────────────────────────────────────────────
  function picked() { return tiles().filter(el => el.classList.contains('is-mg-picked')).map(el => +el.dataset.id); }
  function updateCount() {
    const n = picked().length;
    bulkCount.textContent = (T.selected || '{n}').replace('{n}', n);
    bulk.querySelectorAll('button').forEach(b => { b.disabled = n === 0; });
  }
  function setSelecting(on) {
    selecting = on;
    document.body.classList.toggle('is-mg-selecting', on);
    selBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    selBtn.textContent = on ? selBtn.dataset.on : selBtn.dataset.off;
    bulk.hidden = !on;
    tiles().forEach(el => {
      el.classList.remove('is-mg-picked');
      const a = el.querySelector('a.photo-open');
      if (a) a.setAttribute('aria-pressed', 'false');
      if (a && !on) a.removeAttribute('aria-pressed');
    });
    updateCount();
  }
  selBtn.addEventListener('click', () => setSelecting(!selecting));
  // Capture phase: in selection mode a tap picks, the large view stays shut.
  if (grid) grid.addEventListener('click', e => {
    if (!selecting) return;
    const el = e.target.closest('.photo-item');
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    const on = el.classList.toggle('is-mg-picked');
    const a = el.querySelector('a.photo-open');
    if (a) a.setAttribute('aria-pressed', on ? 'true' : 'false');
    updateCount();
  }, true);

  async function run(action, ids, extra) {
    if (!ids.length) return null;
    try {
      const res = await send(action, ids, extra);
      return res;
    } catch (e) {
      say(T.failed);
      return null;
    }
  }

  async function hide(ids, hidden) {
    const res = await run(hidden ? 'hide' : 'show', ids);
    if (!res) return;
    ids.forEach(id => { const el = tile(id); if (el) setHidden(el, hidden); });
    applyFilter();
    say(T.ok);
  }

  async function remove(ids) {
    if (!window.confirm((T.confirm_delete || '{n}').replace('{n}', ids.length))) return;
    const res = await run('delete', ids);
    if (!res) return;
    ids.forEach(id => { const el = tile(id); if (el) el.remove(); });
    updateCount();
    applyFilter();
    undoToast(ids.length, res.trash || []);
  }

  // "Rückgängig" for a delete that went into the trash: a toast with one button.
  function undoToast(n, entries) {
    let el = document.getElementById('mgUndo');
    if (!el) {
      el = document.createElement('div');
      el.id = 'mgUndo';
      el.className = 'mg-undo';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.textContent = '';
    const p = document.createElement('span');
    p.textContent = (T.deleted || '{n}').replace('{n}', n);
    el.appendChild(p);
    if (entries.length) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'mg-undo-btn';
      b.textContent = T.undo;
      b.addEventListener('click', async () => {
        const res = await run('restore', entries);
        if (res && res.restored) { say(T.restored); location.reload(); }
      });
      el.appendChild(b);
    }
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.hidden = true; }, 8000);
  }

  // ── Colour menu (bulk and large view) ────────────────────────
  function openTags(ids, anchor) {
    tagTarget = ids;
    if (tagMenu.parentNode !== document.body) document.body.appendChild(tagMenu);
    tagMenu.hidden = false;
    const r = anchor.getBoundingClientRect();
    const w = tagMenu.offsetWidth, h = tagMenu.offsetHeight;
    tagMenu.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
    tagMenu.style.top = (r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8) + 'px';
    const first = tagMenu.querySelector('[data-mgtag]');
    if (first) first.focus();
    tagMenu._anchor = anchor;
  }
  function closeTags(focus) {
    if (tagMenu.hidden) return;
    tagMenu.hidden = true;
    if (focus && tagMenu._anchor) tagMenu._anchor.focus();
  }
  tagMenu.addEventListener('click', async e => {
    const b = e.target.closest('[data-mgtag]');
    if (!b || !tagTarget) return;
    const tag = b.dataset.mgtag;
    const ids = tagTarget;
    closeTags(true);
    const res = await run('tag', ids, { tag: tag });
    if (!res) return;
    ids.forEach(id => { const el = tile(id); if (el) setTag(el, tag); });
    applyFilter();
    if (lb) lb.dispatchEvent(new CustomEvent('pb:show', { detail: { id: current } }));
  });
  document.addEventListener('keydown', e => {
    if (tagMenu.hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeTags(true); }
  }, true);
  document.addEventListener('click', e => {
    if (!tagMenu.hidden && !e.target.closest('#mgTagMenu') && !e.target.closest('[data-mgbulk="tag"]')) closeTags(false);
  });

  bulk.addEventListener('click', e => {
    const b = e.target.closest('[data-mgbulk]');
    if (!b) return;
    const ids = picked();
    if (!ids.length) return;
    const act = b.dataset.mgbulk;
    if (act === 'hide') hide(ids, true);
    else if (act === 'show') hide(ids, false);
    else if (act === 'delete') remove(ids);
    else if (act === 'tag') openTags(ids, b);
  });

  // ── Large view ───────────────────────────────────────────────
  const lbHide = document.getElementById('mgLbHide');
  const lbCover = document.getElementById('mgLbCover');
  const lbDelete = document.getElementById('mgLbDelete');
  function syncLb() {
    const el = tile(current);
    if (!el || !lbHide) return;
    const hidden = el.hasAttribute('data-hidden');
    const label = hidden ? lbHide.dataset.show : lbHide.dataset.hide;
    lbHide.setAttribute('aria-label', label);
    lbHide.title = label;
    lbHide.classList.toggle('is-on', hidden);
  }
  if (lb) lb.addEventListener('pb:show', e => { current = e.detail && e.detail.id; syncLb(); });
  if (lbHide) lbHide.addEventListener('click', async e => {
    e.stopPropagation();
    const el = tile(current);
    if (!el) return;
    await hide([+current], !el.hasAttribute('data-hidden'));
    syncLb();
  });
  if (lbCover) lbCover.addEventListener('click', async e => {
    e.stopPropagation();
    const res = await run('cover', [+current]);
    if (res) say(T.cover_set);
  });
  if (lbDelete) lbDelete.addEventListener('click', async e => {
    e.stopPropagation();
    const id = +current;
    if (!window.confirm((T.confirm_delete || '{n}').replace('{n}', 1))) return;
    const res = await run('delete', [id]);
    if (!res) return;
    const el = tile(id);
    if (window.pbLightboxClose) window.pbLightboxClose();
    if (el) el.remove();
    applyFilter();
    undoToast(1, res.trash || []);
  });
  // The colour button in the large view opens the four colours at once
  // instead of stepping through them (lightbox-extras.js steps for guests).
  const lbTag = document.getElementById('lbTag');
  if (lbTag) lbTag.addEventListener('click', e => {
    e.stopImmediatePropagation();
    openTags([+current], lbTag);
  }, true);
  // Download in manage mode: always the original, no size question.
  const lbDl = document.getElementById('lbDownload');
  if (lbDl) lbDl.addEventListener('click', e => {
    e.stopImmediatePropagation();
    const a = document.createElement('a');
    a.href = '/gallery/' + encodeURIComponent(D.slug) + '/photo/' + current + '/download?size=original';
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, true);
})();
