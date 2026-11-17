/* A finger that arrives as a mouse still pans.

   A real finger pans every page and every scrolling box natively (touch-action
   allows it everywhere except the drawing canvas and the template editor's
   sheet; tools/touch_pan_check.mjs proves it with raw touch points). But on
   the booth PC (16.11.2026, WebView2, HID touch screen) a finger did not
   scroll "Verwaltung" or "Vorlagen" while the wheel did: the touch screen
   reached the page as a mouse, and a mouse drag selects or does nothing.

   So a left-button mouse press that is dragged more than a few pixels pans the
   nearest box that can scroll that way (or the page), and the click at its end
   is not a tap. A press without movement is an ordinary click. Fields, sliders,
   the editor's sheet and layer rows, canvases and the on-screen keyboard keep
   the mouse for themselves. Plain script for the page CSP (script-src 'self'). */
(() => {
  "use strict";
  const SLOP = 10;   // px before a press counts as a drag, not a tap
  const SKIP = "input,textarea,select,option,[contenteditable],[draggable='true'],canvas,video," +
    ".vle-blatt,.vle-blattbox,.osk,[data-no-pan]";
  let press = null;          // {x, y, lastX, lastY, target, box, axis}
  let swallowClick = false;

  function canScroll(el, axis) {
    if (el === document.scrollingElement) {
      return axis === "y" ? el.scrollHeight > el.clientHeight + 1 : el.scrollWidth > el.clientWidth + 1;
    }
    const cs = getComputedStyle(el);
    const ov = axis === "y" ? cs.overflowY : cs.overflowX;
    if (!/(auto|scroll)/.test(ov)) return false;
    return axis === "y" ? el.scrollHeight > el.clientHeight + 1 : el.scrollWidth > el.clientWidth + 1;
  }
  function boxFor(el, axis) {
    for (let p = el; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      if (canScroll(p, axis)) return p;
    }
    const page = document.scrollingElement;
    return page && canScroll(page, axis) ? page : null;
  }

  document.addEventListener("pointerdown", (e) => {
    press = null;
    if (e.pointerType !== "mouse" || e.button !== 0) return;   // a finger pans natively
    if (e.target.closest && e.target.closest(SKIP)) return;
    press = { x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY, target: e.target, box: null };
  }, true);

  document.addEventListener("pointermove", (e) => {
    if (!press || e.pointerType !== "mouse") return;
    if (!(e.buttons & 1)) { end(true); return; }
    if (!press.box) {
      const dx = e.clientX - press.x, dy = e.clientY - press.y;
      if (Math.hypot(dx, dy) < SLOP) return;
      const axis = Math.abs(dy) >= Math.abs(dx) ? "y" : "x";
      press.box = boxFor(press.target, axis) || boxFor(press.target, axis === "y" ? "x" : "y");
      if (!press.box) { press = null; return; }
      // A carousel snaps: every small step would be pulled back to the card it
      // left. Snapping waits until the press ends, as it does for a finger.
      press.snap = press.box.style.scrollSnapType;
      press.box.style.scrollSnapType = "none";
      const sel = window.getSelection && window.getSelection();
      if (sel && sel.removeAllRanges) sel.removeAllRanges();
    }
    press.box.scrollLeft -= e.clientX - press.lastX;
    press.box.scrollTop -= e.clientY - press.lastY;
    press.lastX = e.clientX; press.lastY = e.clientY;
    e.preventDefault();
  }, true);

  function end(cancelled) {
    if (press && press.box) {
      press.box.style.scrollSnapType = press.snap || "";
      if (!cancelled) {
        swallowClick = true;
        setTimeout(() => { swallowClick = false; }, 400);
      }
    }
    press = null;
  }
  document.addEventListener("pointerup", () => end(false), true);
  document.addEventListener("pointercancel", () => end(true), true);

  // The click that ends a pan is not a tap on whatever lies under the pointer.
  document.addEventListener("click", (e) => {
    if (!swallowClick) return;
    swallowClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);
  // While panning nothing is selected and no link or picture is dragged away.
  document.addEventListener("selectstart", (e) => { if (press && press.box) e.preventDefault(); }, true);
  document.addEventListener("dragstart", (e) => { if (press) e.preventDefault(); }, true);
})();
