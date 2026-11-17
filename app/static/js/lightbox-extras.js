/* The large view's extras: colour tag (for whoever may tag on the tile) and
 * the EXIF line (when the gallery shows it). Both follow the picture shown
 * through the lightbox's "pb:show" event. No inline handlers.
 */
(function () {
  'use strict';
  const lb = document.getElementById('lightbox');
  const dataEl = document.getElementById('lbExtraData');
  if (!lb || !dataEl) return;
  const data = JSON.parse(dataEl.textContent || '{}');
  const order = [''].concat(data.tags || []);
  const tagBtn = document.getElementById('lbTag');
  const exifEl = document.getElementById('lbExif');
  let current = null;

  function tile(id) { return document.querySelector('.photo-item[data-id="' + id + '"]'); }

  function showTag(tag) {
    if (!tagBtn) return;
    tagBtn.dataset.tag = tag || '';
    tagBtn.style.color = tag ? (data.colors || {})[tag] || '' : '';
    const label = tag ? (data.labels || {})[tag] : '';
    if (label) tagBtn.title = label;
  }

  async function exif(id) {
    if (!exifEl) return;
    exifEl.textContent = '';
    try {
      const r = await fetch(exifEl.dataset.base + id + '/exif.json', { credentials: 'same-origin' });
      if (!r.ok || current !== id) return;
      const j = await r.json();
      exifEl.textContent = ['camera', 'focal', 'exposure', 'aperture', 'iso'].map(k => j[k]).filter(Boolean).join('  ·  ');
    } catch (e) { /* the line stays empty */ }
  }

  lb.addEventListener('pb:show', e => {
    current = e.detail && e.detail.id;
    const el = tile(current);
    showTag(el ? el.dataset.tag : '');
    exif(current);
  });

  if (tagBtn) tagBtn.addEventListener('click', async e => {
    e.stopPropagation();
    const el = tile(current);
    if (!el) return;
    const next = order[(order.indexOf(el.dataset.tag || '') + 1) % order.length];
    const r = await fetch('/api/photo/' + current + '/tag', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tag: next }) });
    if (!r.ok) return;
    el.dataset.tag = next;
    // The tile's dot follows, so the grid is right when the view closes.
    let dot = el.querySelector('.photo-tag');
    if (next) {
      if (!dot) { dot = document.createElement('span'); dot.className = 'photo-tag'; (el.querySelector('.photo-open') || el).appendChild(dot); }
      dot.style.background = (data.colors || {})[next] || '';
      dot.title = (data.labels || {})[next] || '';
    } else if (dot) {
      dot.remove();
    }
    showTag(next);
  });
})();
