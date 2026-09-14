/* ui/rack.js — card grid render (workspace -> snapshot groups), equal heights, progress bar, fire.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ── Card grid render (grouped by workspace → snapshot) ───────────────────────
function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;

  const groups = {};
  // KNOB macros live in the CONTROLS section, not the macro grid
  const knobNames = Object.keys(macros).filter(n => _knobStepOf(macros[n]));
  _renderKnobSection(knobNames);
  Object.entries(macros).forEach(([name, m]) => {
    if (_knobStepOf(m)) return;
    const ws = m.workspace || '—';
    const ss = m.snapshot || '—';
    if (!groups[ws]) groups[ws] = {};
    if (!groups[ws][ss]) groups[ws][ss] = [];
    groups[ws][ss].push(name);
  });

  let html = '';
  Object.entries(groups).forEach(([ws, snapshots]) => {
    const wsId = _safeId(ws);
    const collapsed = _collapsedGroups.has(ws);
    const bodyDisplay = collapsed ? 'none' : 'contents';
    const arrowStyle = collapsed ? 'style="transform:rotate(-90deg)"' : '';

    // Workspace section header — always visible, click to collapse
    html += `<div class="col-span-full mb-2">
      <button onclick="toggleGroup(decodeURIComponent('${_jsArg(ws)}'))"
          class="w-full flex items-center gap-3 group text-left py-1">
        <span class="text-xs font-semibold text-zinc-400 uppercase tracking-widest group-hover:text-white transition-colors">${_esc(ws)}</span>
        <div class="flex-1 h-px bg-zinc-800"></div>
        <!-- Group last-fired LED + label -->
        <span id="group-led-dot-${wsId}" class="w-2 h-2 rounded-full bg-zinc-600 transition-all duration-200 shrink-0"></span>
        <span id="group-led-label-${wsId}" class="text-[10px] font-mono text-zinc-600 max-w-[200px] truncate"></span>
        <i id="group-arrow-${wsId}" class="fas fa-chevron-down text-[9px] text-zinc-500 transition-transform duration-200 ml-1" ${arrowStyle}></i>
      </button>
    </div>`;

    // Collapsible body — display:contents keeps children as direct grid items
    html += `<div id="group-body-${wsId}" style="display:${bodyDisplay}">`;

    Object.entries(snapshots).forEach(([ss, names]) => {
      if (ss !== '—') {
        const ssKey        = _ssKey(ws, ss);
        const ssId         = _safeId(ssKey);
        const ssCollapsed  = _collapsedSnapshots.has(ssKey);
        const ssBodyDisp   = ssCollapsed ? 'none' : 'contents';
        const ssArrowStyle = ssCollapsed ? 'style="transform:rotate(-90deg)"' : '';

        html += `<div class="col-span-full mb-1 ml-1">
          <button onclick="toggleSnapshotGroup(decodeURIComponent('${_jsArg(ws)}'),decodeURIComponent('${_jsArg(ss)}'))"
              class="flex items-center gap-2 group text-left py-0.5">
            <span class="text-[10px] text-zinc-600 uppercase tracking-widest group-hover:text-zinc-400 transition-colors">↳ ${_esc(ss)}</span>
            <i id="ss-arrow-${ssId}" class="fas fa-chevron-down text-[8px] text-zinc-700 group-hover:text-zinc-500 transition-transform duration-150" ${ssArrowStyle}></i>
          </button>
        </div>`;
        html += `<div id="ss-body-${ssId}" style="display:${ssBodyDisp}">`;
        names.forEach(name => { html += createMacroCardHTML(name, macros[name]); });
        html += `</div>`;
      } else {
        names.forEach(name => { html += createMacroCardHTML(name, macros[name]); });
      }
    });

    html += `</div>`; // close group-body
  });

  grid.innerHTML = html;
  _modulWireWaves();   // synchronous — rAF never fires in hidden tabs

  // Run after paint so getBoundingClientRect reflects final layout
  requestAnimationFrame(equalizeCardHeights);
}

// ── Equal card heights per visual row ────────────────────────────────────────
// Groups cards by their top offset (= same grid row) and sets a shared
// min-height so each row looks uniform. Runs after render and on resize.
// Expanding details only grows that one card — it never shrinks its neighbours.
function equalizeCardHeights() {
  const cards = [...document.querySelectorAll('#macro-grid .card')];
  // Reset before measuring
  cards.forEach(c => { c.style.minHeight = ''; });

  const rows = new Map();
  cards.forEach(c => {
    const top = Math.round(c.getBoundingClientRect().top);
    if (!rows.has(top)) rows.set(top, []);
    rows.get(top).push(c);
  });

  rows.forEach(rowCards => {
    const max = Math.max(...rowCards.map(c => c.offsetHeight));
    rowCards.forEach(c => { c.style.minHeight = max + 'px'; });
  });
}

window.addEventListener('resize', equalizeCardHeights);

// ── Progress bar ─────────────────────────────────────────────────────────────
function animateProgress(name, durationMs) {
  _modulWaveKeys(name).forEach(({ key, i }) => {
    const step = ((macros[name] || {}).steps || [])[i];
    const lbl = document.getElementById(`mwaveval:${name}-${i}`);
    const fmt = _mwaveFmt(step);
    ModulGraph.waveformRun(key, durationMs, (t, v) => {
      if (!lbl) return;
      if (v == null) {
        lbl.textContent = _mwaveIdleText(step);
        lbl.classList.remove('live');
      } else {
        lbl.textContent = fmt(v);
        lbl.classList.add('live');
      }
    });
  });
  const bar = document.getElementById(`progress-bar:${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  bar.offsetHeight; // force reflow
  bar.style.transition = `width ${durationMs}ms linear`;
  bar.style.width = '100%';
}

function snapProgressToZero(name) {
  _modulWaveKeys(name).forEach(({ key }) => ModulGraph.waveformStop(key));
  const bar = document.getElementById(`progress-bar:${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
}

// The wave plots on a card: engine key + step index (empty outside MODUL)
function _modulWaveKeys(name) {
  if (!window.ModulGraph) return [];
  return [...document.querySelectorAll(`div[id^="mwave:${name}-"]`)]
    .map(el => {
      const i = el.id.slice(`mwave:${name}-`.length);
      return { key: `w:${name}:${i}`, i: parseInt(i, 10) };
    });
}

// ── Fire macro ────────────────────────────────────────────────────────────────
async function fireMacro(name, value = 1.0) {
  if (!macros[name]) return;
  try {
    await API.trigger(name, value, window._detectedBPM || null);
  } catch (e) {
    console.error('[UI] fireMacro error:', e);
  }
}


