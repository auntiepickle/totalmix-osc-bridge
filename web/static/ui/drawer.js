/* ui/drawer.js — detail drawer (#31).
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ── Detail drawer (#31) ─────────────────────────────────────────────────────
// The per-card DETAILS panel used to expand inside the card: a ~300px column
// beside three empty card bodies on a wide screen. It now opens in a drawer -
// a right-side panel on desktop (the grid stays usable, another card's DETAILS
// swaps the content), a bottom sheet on phones. Mechanically the card's OWN
// #detail-<name> node is moved into the drawer while open and back afterwards,
// so the editor, save, cancel and Full JSON keep working unchanged (they look
// the node up by id; the drawer sits before the grid in DOM order so the
// moved node wins over the empty one a re-render leaves in the card).
window._drawerOpenFor = null;

function _detailSlot(name) {
  const id = CSS.escape(`detail:${name}`);
  return document.querySelector(`#macro-grid #${CSS.escape(id)}, #knob-row #${CSS.escape(id)}`);
}

function _syncDetailArrow(name, open) {
  const a = document.getElementById(`detail-arrow:${name}`);
  if (a) a.style.transform = open ? 'rotate(180deg)' : '';
}

function _openDetailFor(name) {
  const m = macros[name];
  const body = document.getElementById('drawer-body');
  const drawer = document.getElementById('detail-drawer');
  if (!m || !body || !drawer) return null;
  if (window._drawerOpenFor && window._drawerOpenFor !== name) {
    closeDetailDrawer();
    if (window._drawerOpenFor) return null;          // kept unsaved edits open
  }
  const panel = document.querySelector(`#drawer-body #${CSS.escape(`detail:${name}`)}`)
    || _detailSlot(name) || document.getElementById(`detail:${name}`);
  if (!panel) return null;
  if (panel.parentElement !== body) body.appendChild(panel);
  panel.classList.remove('hidden');
  const title = document.getElementById('drawer-title');
  const sub = document.getElementById('drawer-sub');
  if (title) title.textContent = m.label || name;
  const rl = m.routing_label && m.routing_label !== '—' ? m.routing_label : '';
  if (sub) sub.textContent = rl || (m.label ? name : '');
  drawer.classList.remove('hidden');
  drawer.setAttribute('aria-hidden', 'false');
  requestAnimationFrame(() => drawer.classList.add('open'));
  window._drawerOpenFor = name;
  _syncDetailArrow(name, true);
  document.getElementById(`card:${name}`)?.classList.add('drawer-target');
  return panel;
}

function showDetail(name) {
  const m = macros[name];
  if (!m) return;
  const panel = _openDetailFor(name);
  if (!panel) return;
  if (!window._editBuffers[name]) panel.innerHTML = _detailHTML(name, m);
  const body = document.getElementById('drawer-body');
  if (body) body.scrollTop = 0;
}

function toggleDetail(name) {
  if (window._drawerOpenFor === name) closeDetailDrawer();
  else showDetail(name);
}

// An armed MIDI-learn must die with its editor: otherwise the next controller
// message is swallowed (not fired) and the callback harvests a gone buffer.
function _disarmMidiLearn() {
  window._midiLearn = null;
  clearTimeout(window._learnHoldTimer);
  window._learnHold = null;
}

function closeDetailDrawer(force) {
  const name = window._drawerOpenFor;
  if (!name) return;
  if (window._editBuffers[name]) {
    if (!force && !confirm('Discard unsaved changes to this macro?')) return;
    delete window._editBuffers[name];
    delete window._descStale[name];
  }
  _disarmMidiLearn();
  const drawer = document.getElementById('detail-drawer');
  const panel = document.querySelector(`#drawer-body #${CSS.escape(`detail:${name}`)}`);
  window._drawerOpenFor = null;
  _syncDetailArrow(name, false);
  document.getElementById(`card:${name}`)?.classList.remove('drawer-target');
  if (panel) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
    const slot = _detailSlot(name);
    if (slot && slot !== panel) slot.replaceWith(panel);
    else if (!slot) {
      const card = document.getElementById(`card:${name}`);
      if (card) card.appendChild(panel); else panel.remove();
    }
  }
  if (drawer) {
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    const hide = () => { if (!window._drawerOpenFor) drawer.classList.add('hidden'); };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) hide(); else setTimeout(hide, 200);
  }
  requestAnimationFrame(equalizeCardHeights);
}

// After a re-render the card holds a fresh empty panel and an un-rotated
// arrow; keep the drawer coherent, or close it when its macro is gone.
function _syncDetailDrawer() {
  const name = window._drawerOpenFor;
  if (!name) return;
  if (!macros[name]) { closeDetailDrawer(true); return; }
  _syncDetailArrow(name, true);
  document.getElementById(`card:${name}`)?.classList.add('drawer-target');
  const panel = document.querySelector(`#drawer-body #${CSS.escape(`detail:${name}`)}`);
  if (panel && !window._editBuffers[name]) panel.innerHTML = _detailHTML(name, macros[name]);
}
{
  const _rc = renderCards, _rk = _renderKnobSection;
  renderCards = function () { _rc.apply(this, arguments); _syncDetailDrawer(); };
  _renderKnobSection = function () { _rk.apply(this, arguments); _syncDetailDrawer(); };
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !window._drawerOpenFor) return;
  const modal = document.getElementById('editor-modal');
  if (modal && !modal.classList.contains('hidden')) return;
  closeDetailDrawer();
});
