/* ui.js — card rendering, animation, fire/ramp, file upload, server reload, live editor */
/* Globals (macros, midiConnectedDevice, etc.) live in app.js — loaded first             */

// ── Group collapse state (persisted in localStorage) ─────────────────────────
const _collapsedGroups = new Set(JSON.parse(localStorage.getItem('collapsedGroups') || '[]'));

function _saveCollapsed() {
  localStorage.setItem('collapsedGroups', JSON.stringify([..._collapsedGroups]));
}

function _safeId(str) {
  return String(str).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function toggleGroup(ws) {
  const wsId = _safeId(ws);
  if (_collapsedGroups.has(ws)) {
    _collapsedGroups.delete(ws);
  } else {
    _collapsedGroups.add(ws);
  }
  _saveCollapsed();
  const body = document.getElementById(`group-body-${wsId}`);
  const arrow = document.getElementById(`group-arrow-${wsId}`);
  if (body) body.style.display = _collapsedGroups.has(ws) ? 'none' : 'contents';
  if (arrow) arrow.style.transform = _collapsedGroups.has(ws) ? 'rotate(-90deg)' : '';
}

// ── Group-level LED (workspace level) ────────────────────────────────────────
// Called from app.js pulseLED after the per-card dot is lit
window.pulseGroupLED = function (ws, macroName, triggerTimestamp) {
  const wsId = _safeId(ws);
  const dot   = document.getElementById(`group-led-dot-${wsId}`);
  const label = document.getElementById(`group-led-label-${wsId}`);
  if (!dot) return;
  const ts = new Date(triggerTimestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  if (label) label.textContent = `${macroName} · ${ts}`;
  dot.classList.remove('bg-zinc-600');
  dot.classList.add('bg-green-400', 'shadow-[0_0_6px_#4ade80]');
  setTimeout(() => {
    dot.classList.remove('bg-green-400', 'shadow-[0_0_6px_#4ade80]');
    dot.classList.add('bg-zinc-600');
  }, 3000);
};

// ── Duration helper ───────────────────────────────────────────────────────────
function calculateDurationMs(macro) {
  if (macro.durationMs) return macro.durationMs;
  const step = macro.steps ? macro.steps.find(s => s.operation) : null;
  if (!step || !step.operation) return 2000;
  const op = step.operation;
  return Math.round((op.bars || 2) * (240000 / (op.bpm || 140)));
}

function getMidiTriggerLabel(m) {
  const t = m.midi_triggers && m.midi_triggers[0];
  if (!t) return '';
  return `CC${t.number} · ch${t.channel}`;
}

// ── Card HTML ─────────────────────────────────────────────────────────────────
function createMacroCardHTML(name, m) {
  const midiLabel = getMidiTriggerLabel(m);
  return `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-5 rounded-2xl">
    <div class="flex justify-between items-start mb-3">
        <div class="flex-1 min-w-0 pr-3">
            <h3 class="text-lg font-bold text-white truncate">${name}</h3>
            <p class="text-zinc-400 text-xs mt-0.5">${m.description || ''}</p>
            <p class="routing-label text-orange-400 text-xs font-medium mt-1.5 tracking-wider">${m.routing_label || '—'}</p>
        </div>
        <div class="flex flex-col items-end gap-1.5 shrink-0">
            ${midiLabel ? `<div class="text-xs font-mono bg-zinc-800/80 text-zinc-400 px-2.5 py-1 rounded-lg">${midiLabel}</div>` : ''}
            <!-- LED dot only — timestamp moved to group header -->
            <div class="flex items-center gap-1.5 px-2 py-1">
              <span id="led-dot-${name}" class="w-2 h-2 rounded-full bg-zinc-700 transition-all duration-150"></span>
            </div>
        </div>
    </div>
    <div class="h-1.5 bg-zinc-800 rounded-full overflow-hidden mb-4">
      <div id="progress-bar-${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500" style="width:0%;"></div>
    </div>
    <div class="grid grid-cols-3 gap-2">
        <button onclick="fireMacro('${name}',1.0,false)"
            class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-400 active:scale-95 active:bg-orange-600 text-black font-bold py-2.5 rounded-xl text-base transition-all">
            FIRE
        </button>
        <button onclick="fireMacro('${name}',1.0,true)"
            class="bg-zinc-800 hover:bg-amber-400/20 border border-amber-400/40 hover:border-amber-400 text-amber-400 font-semibold py-2.5 rounded-xl transition-all active:scale-95 text-xs tracking-widest">
            RAMP
        </button>
    </div>
    <button onclick="toggleDetail('${name}')"
        class="mt-4 w-full text-zinc-600 hover:text-orange-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors tracking-widest">
        DETAILS <i id="detail-arrow-${name}" class="fas fa-chevron-down text-[9px] transition-transform duration-150"></i>
    </button>
    <div id="detail-${name}" class="hidden mt-2 p-3 bg-[#111111] rounded-xl border border-zinc-700/50 text-xs"></div>
</div>`;
}

// ── Card grid render (grouped by workspace → snapshot) ───────────────────────
function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;

  const groups = {};
  Object.entries(macros).forEach(([name, m]) => {
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
      <button onclick="toggleGroup('${ws}')"
          class="w-full flex items-center gap-3 group text-left py-1">
        <span class="text-xs font-semibold text-zinc-400 uppercase tracking-widest group-hover:text-white transition-colors">${ws}</span>
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
        html += `<div class="col-span-full mb-1 ml-1">
          <span class="text-[10px] text-zinc-600 uppercase tracking-widest">↳ ${ss}</span>
        </div>`;
      }
      names.forEach(name => { html += createMacroCardHTML(name, macros[name]); });
    });

    html += `</div>`; // close group-body
  });

  grid.innerHTML = html;
}

// ── Progress bar ─────────────────────────────────────────────────────────────
function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  bar.offsetHeight; // force reflow
  bar.style.transition = `width ${durationMs}ms linear`;
  bar.style.width = '100%';
}

function snapProgressToZero(name) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
}

// ── Fire macro ────────────────────────────────────────────────────────────────
async function fireMacro(name, value = 1.0, ramp = false) {
  if (!macros[name]) return;
  try {
    await fetch(`/api/trigger/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param: value }),
    });
  } catch (e) {
    console.error('[UI] fireMacro error:', e);
  }
}

// ── Structured detail panel ───────────────────────────────────────────────────
// _snapshotMap is loaded once on init (see app.js loadSnapshotMap)
window._snapshotMap = window._snapshotMap || {};

function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const arrow = document.getElementById(`detail-arrow-${name}`);
  const m = macros[name];
  if (!panel || !m) return;

  panel.classList.toggle('hidden');
  if (arrow) arrow.style.transform = panel.classList.contains('hidden') ? '' : 'rotate(180deg)';
  if (panel.classList.contains('hidden')) return;

  const durationSec = (calculateDurationMs(m) / 1000).toFixed(2);
  const fireMode = (m.fire_mode || 'ignore').toUpperCase();
  const fireModeColors = {
    RESTART: 'text-red-400 bg-red-900/30 border border-red-800/50',
    QUEUE:   'text-yellow-400 bg-yellow-900/30 border border-yellow-800/50',
    IGNORE:  'text-zinc-400 bg-zinc-800 border border-zinc-700',
  };
  const fireModeClass = fireModeColors[fireMode] || fireModeColors.IGNORE;

  // Check if ws/snapshot can be resolved in the loaded snapshot map
  const snapMap = window._snapshotMap || {};
  const wsEntry = snapMap[m.workspace];
  const wsResolved = !!wsEntry;
  const ssResolved = wsResolved && Object.values(wsEntry.snapshots || {})
    .some(v => String(v).toLowerCase() === String(m.snapshot || '').toLowerCase());

  let html = `<div class="space-y-3 text-zinc-300 text-sm">`;

  // Snapshot map validation warning
  if (m.workspace && m.snapshot && (!wsResolved || !ssResolved)) {
    const missing = !wsResolved ? `workspace "${m.workspace}"` : `snapshot "${m.snapshot}" in ${m.workspace}`;
    html += `<div class="flex items-center gap-2 bg-red-900/20 border border-red-800/40 text-red-400 text-xs px-3 py-2 rounded-lg">
      <i class="fas fa-triangle-exclamation shrink-0"></i>
      <span>${missing} not found in snapshot map — WS/SS switch will always fire.
        <button onclick="openEditor('snapshot_map')" class="underline hover:text-red-300 ml-1">Fix in editor</button>
      </span>
    </div>`;
  }

  // Top row: fire mode badge + duration + Edit button
  html += `<div class="flex items-center justify-between gap-2">
    <span class="text-xs font-bold px-2.5 py-1 rounded-lg tracking-widest ${fireModeClass}">${fireMode}</span>
    <span class="text-zinc-500 text-xs font-mono">⏱ ${durationSec}s</span>
    <button onclick="openEditor('mappings')"
        class="ml-auto text-xs text-zinc-500 hover:text-orange-400 flex items-center gap-1 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800">
      <i class="fas fa-pen text-[10px]"></i> Edit
    </button>
  </div>`;

  // Steps
  if (m.steps && m.steps.length) {
    html += `<div>
      <div class="text-xs uppercase tracking-widest text-zinc-500 mb-1.5">Steps</div>
      <div class="space-y-1.5">`;
    m.steps.forEach(step => {
      const addr = step.osc || '?';
      if (step.operation) {
        const op = step.operation;
        const opType = (op.type || '').toUpperCase();
        const bars = op.bars || 2;
        const bpm  = op.bpm  || 140;
        const curve = op.curve ? ` · ${op.curve}` : '';
        const opColors = { RAMP: 'text-amber-400', LFO: 'text-purple-400' };
        const opColor = opColors[opType] || 'text-zinc-400';
        html += `<div class="flex items-center gap-2 font-mono bg-zinc-900/60 px-2.5 py-1.5 rounded-lg">
          <span class="text-zinc-500 text-xs">∿</span>
          <span class="text-orange-300 text-xs flex-1 truncate">${addr}</span>
          <span class="${opColor} text-xs font-bold">${opType}</span>
          <span class="text-zinc-600 text-xs">${bars}b @ ${bpm}${curve}</span>
        </div>`;
      } else {
        const val = step.value !== undefined ? step.value : '?';
        html += `<div class="flex items-center gap-2 font-mono bg-zinc-900/60 px-2.5 py-1.5 rounded-lg">
          <span class="text-zinc-500 text-xs">⚡</span>
          <span class="text-orange-300 text-xs flex-1 truncate">${addr}</span>
          <span class="text-zinc-400 text-xs">= ${val}</span>
        </div>`;
      }
    });
    html += `</div></div>`;
  }

  // MIDI triggers
  if (m.midi_triggers && m.midi_triggers.length) {
    html += `<div>
      <div class="text-xs uppercase tracking-widest text-zinc-500 mb-1.5">MIDI Triggers</div>
      <div class="flex flex-wrap gap-1.5">`;
    m.midi_triggers.forEach(t => {
      html += `<span class="text-xs font-mono bg-zinc-800 text-zinc-300 px-2.5 py-1 rounded-lg border border-zinc-700">CC${t.number} ch${t.channel}</span>`;
    });
    html += `</div></div>`;
  }

  // Workspace / Snapshot
  html += `<div class="flex items-center gap-1.5 border-t border-zinc-800 pt-2">`;
  html += wsResolved
    ? `<span class="text-xs text-zinc-500 font-mono">${m.workspace || '—'} / ${m.snapshot || '—'}</span>`
    : `<span class="text-xs text-red-500/70 font-mono">${m.workspace || '—'} / ${m.snapshot || '—'}</span>`;
  if (wsEntry) {
    const slot = wsEntry.slot;
    const snapEntry = Object.entries(wsEntry.snapshots || {}).find(
      ([, v]) => String(v).toLowerCase() === String(m.snapshot || '').toLowerCase()
    );
    if (slot !== undefined) html += `<span class="text-xs text-zinc-700 font-mono">· WS slot ${slot}</span>`;
    if (snapEntry) html += `<span class="text-xs text-zinc-700 font-mono">· SS index ${snapEntry[0]}</span>`;
  }
  html += `</div>`;

  // Full JSON — collapsible
  html += `<details class="group">
    <summary class="cursor-pointer text-xs text-zinc-600 hover:text-orange-400 transition-colors flex items-center gap-1 select-none">
      <i class="fas fa-code text-[10px]"></i> Full JSON
    </summary>
    <pre class="mt-2 text-xs overflow-auto max-h-52 bg-zinc-950 text-zinc-400 p-3 rounded-lg border border-zinc-800 leading-relaxed">${JSON.stringify(m, null, 2)}</pre>
  </details>`;

  html += `</div>`;
  panel.innerHTML = html;
}

// ── Settings menu ─────────────────────────────────────────────────────────────
async function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    try {
      const res = await fetch('/api/status');
      const s = await res.json();
      const info = document.getElementById('settings-status');
      if (info) {
        const wsCount = s.snapshot_map_workspaces || 0;
        const wsText = wsCount > 0
          ? `<span class="text-zinc-400">${wsCount} workspace${wsCount !== 1 ? 's' : ''}</span>`
          : `<span class="text-red-400" title="ufx2_snapshot_map.json not loaded">⚠ no snapshot map</span>`;
        info.innerHTML =
          `<span class="text-zinc-400">${s.macros} macro${s.macros !== 1 ? 's' : ''}</span>` +
          ` · <span class="text-zinc-400">${s.channel_map_submixes} submix${s.channel_map_submixes !== 1 ? 'es' : ''}</span>` +
          ` · ${wsText}`;
      }
    } catch (_) {}
  }
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('settings-menu');
  if (!menu || menu.classList.contains('hidden')) return;
  if (!menu.contains(e.target) && !e.target.closest('[data-settings-toggle]')) {
    menu.classList.add('hidden');
  }
});

// ── Server reload ─────────────────────────────────────────────────────────────
async function reloadServer() {
  if (confirm('Reload bridge server?')) {
    await fetch('/api/reload', { method: 'POST' });
    location.reload();
  }
}

// ── File upload (legacy — kept for drag-and-drop workflows) ──────────────────
function uploadFile(input, type) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  fetch(`/api/upload/${type}`, { method: 'POST', body: formData })
    .then(() => location.reload())
    .catch(e => console.error('[UI] uploadFile error:', e));
}

// ── Live Config Editor ────────────────────────────────────────────────────────
async function openEditor(configType = 'mappings') {
  const modal    = document.getElementById('editor-modal');
  const textarea = document.getElementById('editor-textarea');
  const statusEl = document.getElementById('editor-status');
  if (!modal || !textarea) return;

  // Tab styling
  ['mappings', 'channel_map', 'snapshot_map'].forEach(t => {
    const tab = document.getElementById(`editor-tab-${t}`);
    if (!tab) return;
    tab.className = t === configType
      ? 'text-xs px-3 py-1.5 rounded-lg bg-orange-500 text-black font-bold transition-colors'
      : 'text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-400 hover:text-white transition-colors';
  });

  modal.dataset.configType = configType;
  if (statusEl) statusEl.textContent = 'Loading…';
  modal.classList.remove('hidden');

  try {
    const res  = await fetch(`/api/config/${configType}`);
    const data = await res.json();
    textarea.value = JSON.stringify(data, null, 2);
    if (statusEl) statusEl.textContent = '';
    textarea.focus();
  } catch (e) {
    console.error('[UI] openEditor error:', e);
    textarea.value = '// Error loading config';
    if (statusEl) statusEl.textContent = 'Error';
  }
}

async function saveEditor() {
  const modal    = document.getElementById('editor-modal');
  const textarea = document.getElementById('editor-textarea');
  const statusEl = document.getElementById('editor-status');
  if (!modal || !textarea) return;

  const configType = modal.dataset.configType;
  let data;
  try {
    data = JSON.parse(textarea.value);
  } catch (e) {
    alert(`Invalid JSON:\n${e.message}`);
    return;
  }

  if (statusEl) statusEl.textContent = 'Saving…';
  try {
    const res = await fetch(`/api/config/${configType}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      modal.classList.add('hidden');
      if (configType === 'snapshot_map') {
        // Refresh local snapshot map cache so detail panels show correct validation
        const sm = await fetch('/api/snapshot_map').then(r => r.json()).catch(() => ({}));
        window._snapshotMap = sm;
      } else {
        // Hot-reload macro cards from updated bridge mappings
        await loadMacros();
        renderCards();
      }
    } else {
      const err = await res.json();
      if (statusEl) statusEl.textContent = 'Error';
      alert(`Save failed: ${err.detail}`);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Error';
    alert(`Save error: ${e.message}`);
  }
}

function closeEditor() {
  const modal = document.getElementById('editor-modal');
  if (modal) modal.classList.add('hidden');
}

function formatEditorJSON() {
  const textarea = document.getElementById('editor-textarea');
  if (!textarea) return;
  try {
    textarea.value = JSON.stringify(JSON.parse(textarea.value), null, 2);
  } catch (e) {
    alert(`Invalid JSON: ${e.message}`);
  }
}

// Close editor on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeEditor();
});
