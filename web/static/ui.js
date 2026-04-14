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
    <div class="flex items-start gap-3 mb-3">
        <!-- LED dot — top left, larger, aligned to title baseline -->
        <span id="led-dot-${name}" class="w-4 h-4 rounded-full bg-zinc-700 transition-all duration-150 shrink-0 mt-1"></span>
        <!-- Title + meta -->
        <div class="flex-1 min-w-0">
            <h3 class="text-lg font-bold text-white truncate">${name}</h3>
            <p class="text-zinc-400 text-xs mt-0.5">${m.description || ''}</p>
            <p class="routing-label text-orange-400 text-xs font-medium mt-1.5 tracking-wider">${m.routing_label || '—'}</p>
        </div>
        <!-- MIDI badge -->
        ${midiLabel ? `<div class="text-xs font-mono bg-zinc-800/80 text-zinc-400 px-2.5 py-1 rounded-lg shrink-0">${midiLabel}</div>` : ''}
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
    <button onclick="editDetail('${name}')"
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

  // Workspace / Snapshot — names only, no raw indices
  const wsColor  = wsResolved  ? 'text-zinc-400' : 'text-red-400/70';
  const ssColor  = ssResolved  ? 'text-zinc-400' : 'text-red-400/70';
  const wsLabel  = m.workspace || '—';
  const ssLabel  = m.snapshot  || '—';
  html += `<div class="flex items-center gap-1.5 border-t border-zinc-800 pt-2 font-mono text-xs flex-wrap">
    <span class="${wsColor}">${wsLabel}</span>
    <span class="text-zinc-700">/</span>
    <span class="${ssColor}">${ssLabel}</span>
    ${!wsResolved || !ssResolved ? `<span class="text-red-500/60 text-[10px]">(not in snapshot map)</span>` : ''}
  </div>`;

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

// ── Inline card editor ────────────────────────────────────────────────────────

// Escape value for use in HTML attribute (double-quote safe)
function _esc(v) {
  return String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

function editDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const arrow = document.getElementById(`detail-arrow-${name}`);
  const m = macros[name];
  if (!panel || !m) return;

  panel.classList.remove('hidden');
  if (arrow) arrow.style.transform = 'rotate(180deg)';

  // Shared input CSS classes
  const ic  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-full';
  const sc  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none';
  const nc  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-20 text-center';

  // Steps
  const stepsHtml = (m.steps || []).map((step, i) => {
    const addr = _esc(step.osc || '');
    if (step.operation) {
      const op = step.operation;
      return `<div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
        <div class="flex gap-2">
          <input data-field="steps.${i}.osc" value="${addr}" class="${ic}" placeholder="OSC address">
          <select data-field="steps.${i}.operation.type" class="${sc} shrink-0">
            <option value="ramp"${op.type==='ramp'?' selected':''}>RAMP</option>
            <option value="lfo"${op.type==='lfo'?' selected':''}>LFO</option>
          </select>
        </div>
        <div class="flex gap-2 items-center">
          <input data-field="steps.${i}.operation.bars" type="number" min="1" value="${_esc(op.bars??2)}" class="${nc}">
          <span class="text-zinc-500 text-xs shrink-0">bars @</span>
          <input data-field="steps.${i}.operation.bpm" type="number" min="1" value="${_esc(op.bpm??140)}" class="${nc}">
          <span class="text-zinc-500 text-xs shrink-0">BPM</span>
        </div>
      </div>`;
    } else {
      const val = _esc(step.value ?? '');
      return `<div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl flex gap-2">
        <input data-field="steps.${i}.osc" value="${addr}" class="${ic}" placeholder="OSC address">
        <input data-field="steps.${i}.value" value="${val}" class="${nc}" placeholder="value">
      </div>`;
    }
  }).join('');

  // MIDI triggers
  const midiHtml = (m.midi_triggers || []).map((t, i) => `
    <div class="flex gap-2 items-center bg-zinc-900/80 border border-zinc-800 px-2.5 py-2 rounded-xl">
      <span class="text-zinc-500 text-xs shrink-0">CC</span>
      <input data-field="midi_triggers.${i}.number" type="number" min="0" max="127" value="${_esc(t.number)}" class="${nc}">
      <span class="text-zinc-500 text-xs shrink-0">ch</span>
      <input data-field="midi_triggers.${i}.channel" type="number" min="1" max="16" value="${_esc(t.channel)}" class="${nc}">
    </div>`).join('');

  panel.innerHTML = `<div class="space-y-3 text-sm">

    <input data-field="description" value="${_esc(m.description)}"
        class="${ic}" placeholder="Description">

    <div class="flex gap-2 items-center flex-wrap">
      <select data-field="fire_mode" class="${sc}">
        <option value="ignore"${(m.fire_mode||'ignore')==='ignore'?' selected':''}>IGNORE</option>
        <option value="queue"${m.fire_mode==='queue'?' selected':''}>QUEUE</option>
        <option value="restart"${m.fire_mode==='restart'?' selected':''}>RESTART</option>
      </select>
      <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer select-none ml-1">
        <input type="checkbox" data-field="force_switch" class="w-3.5 h-3.5 accent-orange-500"${m.force_switch?' checked':''}>
        force switch
      </label>
    </div>

    <div class="flex gap-2">
      <div class="flex-1">
        <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Workspace</div>
        <input data-field="workspace" value="${_esc(m.workspace)}" class="${ic}">
      </div>
      <div class="flex-1">
        <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Snapshot</div>
        <input data-field="snapshot" value="${_esc(m.snapshot)}" class="${ic}">
      </div>
    </div>

    ${stepsHtml ? `<div>
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">Steps</div>
      <div class="space-y-2">${stepsHtml}</div>
    </div>` : ''}

    ${midiHtml ? `<div>
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">MIDI Triggers</div>
      <div class="space-y-1.5">${midiHtml}</div>
    </div>` : ''}

    <div class="flex gap-2 pt-2 border-t border-zinc-800">
      <button id="edit-save-${name}" onclick="saveInlineEdit('${name}')"
          class="flex-1 bg-orange-500 hover:bg-orange-400 active:scale-95 text-black font-bold py-2 rounded-xl text-sm transition-all">
        Save
      </button>
      <button onclick="cancelInlineEdit('${name}')"
          class="px-5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 py-2 rounded-xl text-sm transition-all">
        Cancel
      </button>
    </div>

  </div>`;
}

async function saveInlineEdit(name) {
  const panel = document.getElementById(`detail-${name}`);
  const btn   = document.getElementById(`edit-save-${name}`);
  if (!panel) return;

  // Deep-clone so we don't mutate macros[name] until confirmed
  const m = JSON.parse(JSON.stringify(macros[name]));

  panel.querySelectorAll('[data-field]').forEach(el => {
    const parts = el.dataset.field.split('.');
    let obj = m;
    for (let i = 0; i < parts.length - 1; i++) {
      const k = isNaN(parts[i]) ? parts[i] : Number(parts[i]);
      if (obj[k] === undefined || obj[k] === null) return;
      obj = obj[k];
    }
    const lastRaw = parts[parts.length - 1];
    const last = isNaN(lastRaw) ? lastRaw : Number(lastRaw);
    if (el.type === 'checkbox') {
      obj[last] = el.checked;
    } else if (el.type === 'number') {
      obj[last] = el.value === '' ? 0 : parseFloat(el.value);
    } else {
      obj[last] = el.value;
    }
  });

  if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }

  try {
    const res = await fetch(`/api/config/macros/${name}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(m),
    });
    if (res.ok) {
      macros[name] = m;
      cancelInlineEdit(name);
      // Reopen in read-only mode to show the saved state
      setTimeout(() => toggleDetail(name), 30);
    } else {
      const err = await res.json().catch(() => ({ detail: 'unknown error' }));
      alert(`Save failed: ${err.detail}`);
      if (btn) { btn.textContent = 'Save'; btn.disabled = false; }
    }
  } catch (e) {
    alert(`Save error: ${e.message}`);
    if (btn) { btn.textContent = 'Save'; btn.disabled = false; }
  }
}

function cancelInlineEdit(name) {
  const panel = document.getElementById(`detail-${name}`);
  const arrow = document.getElementById(`detail-arrow-${name}`);
  if (!panel) return;
  panel.classList.add('hidden');
  if (arrow) arrow.style.transform = '';
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
