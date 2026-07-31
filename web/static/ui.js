/* ui.js — card rendering, animation, fire/ramp, file upload, server reload, live editor */
/* Globals (macros, midiConnectedDevice, etc.) live in app.js — loaded first             */

// ── Collapse state (persisted in localStorage) ───────────────────────────────
const _collapsedGroups    = new Set(JSON.parse(localStorage.getItem('collapsedGroups')    || '[]'));
const _collapsedSnapshots = new Set(JSON.parse(localStorage.getItem('collapsedSnapshots') || '[]'));

function _saveCollapsed() {
  localStorage.setItem('collapsedGroups',    JSON.stringify([..._collapsedGroups]));
  localStorage.setItem('collapsedSnapshots', JSON.stringify([..._collapsedSnapshots]));
}

function _safeId(str) {
  return String(str).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function _ssKey(ws, ss) { return `${ws}::${ss}`; }

function toggleGroup(ws) {
  const wsId = _safeId(ws);
  _collapsedGroups.has(ws) ? _collapsedGroups.delete(ws) : _collapsedGroups.add(ws);
  _saveCollapsed();
  const body  = document.getElementById(`group-body-${wsId}`);
  const arrow = document.getElementById(`group-arrow-${wsId}`);
  if (body)  body.style.display = _collapsedGroups.has(ws) ? 'none' : 'contents';
  if (arrow) arrow.style.transform = _collapsedGroups.has(ws) ? 'rotate(-90deg)' : '';
}

function toggleSnapshotGroup(ws, ss) {
  const key  = _ssKey(ws, ss);
  const ssId = _safeId(key);
  _collapsedSnapshots.has(key) ? _collapsedSnapshots.delete(key) : _collapsedSnapshots.add(key);
  _saveCollapsed();
  const body  = document.getElementById(`ss-body-${ssId}`);
  const arrow = document.getElementById(`ss-arrow-${ssId}`);
  if (body)  body.style.display = _collapsedSnapshots.has(key) ? 'none' : 'contents';
  if (arrow) arrow.style.transform = _collapsedSnapshots.has(key) ? 'rotate(-90deg)' : '';
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
  const op  = step.operation;
  const bpm = op.bpm === 'clock' ? (window._detectedBPM || 140) : (op.bpm || 140);
  return Math.round((op.bars || 2) * (240000 / bpm));
}

function getMidiTriggerLabel(m) {
  const t = m.midi_triggers && m.midi_triggers[0];
  if (!t) return '';
  const type = t.type || 'control_change';
  if (type === 'note_on')  return `NOTE ON ${t.note ?? '?'} · ch${t.channel}`;
  if (type === 'note_off') return `NOTE OFF ${t.note ?? '?'} · ch${t.channel}`;
  return `CC${t.number} · ch${t.channel}`;
}

// ── Card HTML ─────────────────────────────────────────────────────────────────
function createMacroCardHTML(name, m) {
  const midiLabel   = getMidiTriggerLabel(m);
  const routingLabel = m.routing_label || '—';
  return `
<div id="card-${name}" class="card bg-zinc-900 border border-zinc-800 hover:border-zinc-700 p-5 rounded-2xl transition-colors duration-200">
    <!-- Header: LED · name/desc · MIDI badge -->
    <div class="flex items-center gap-3 mb-1">
        <span id="led-dot-${name}" class="w-3 h-3 rounded-full bg-zinc-700 transition-all duration-150 shrink-0"></span>
        <h3 class="text-sm font-bold text-white truncate flex-1 font-mono tracking-tight">${name}</h3>
        ${midiLabel ? `<div class="text-[10px] font-mono bg-zinc-800 text-zinc-500 px-2 py-0.5 rounded-md shrink-0 border border-zinc-700/60">${midiLabel}</div>` : ''}
    </div>
    <!-- Description + routing label -->
    <div class="pl-6 mb-3">
        ${m.description ? `<p class="text-zinc-500 text-xs leading-snug mb-1">${m.description}</p>` : ''}
        <p class="routing-label text-orange-400/80 text-[11px] font-medium tracking-wide">${routingLabel}</p>
    </div>
    <!-- Progress bar -->
    <div class="h-1 bg-zinc-800 rounded-full overflow-hidden mb-3">
      <div id="progress-bar-${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500 transition-none" style="width:0%;"></div>
    </div>
    <!-- Action buttons -->
    <div class="grid grid-cols-3 gap-2">
        <button onclick="fireMacro('${name}',1.0,false)"
            class="fire-btn col-span-2 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-500 active:scale-95 active:bg-zinc-600 text-zinc-400 hover:text-white font-medium py-2.5 rounded-xl text-xs tracking-widest transition-all">
            FIRE
        </button>
        <button onclick="fireMacro('${name}',1.0,true)"
            class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-500 text-zinc-500 hover:text-zinc-300 font-medium py-2.5 rounded-xl transition-all active:scale-95 text-xs tracking-widest">
            RAMP
        </button>
    </div>
    <!-- Details toggle -->
    <button onclick="toggleDetail('${name}')"
        class="mt-3 w-full text-zinc-700 hover:text-zinc-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors tracking-widest">
        DETAILS <i id="detail-arrow-${name}" class="fas fa-chevron-down text-[9px] transition-transform duration-150"></i>
    </button>
    <div id="detail-${name}" class="hidden mt-3 p-3 bg-zinc-950/80 rounded-xl border border-zinc-800 text-xs"></div>
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
        const ssKey        = _ssKey(ws, ss);
        const ssId         = _safeId(ssKey);
        const ssCollapsed  = _collapsedSnapshots.has(ssKey);
        const ssBodyDisp   = ssCollapsed ? 'none' : 'contents';
        const ssArrowStyle = ssCollapsed ? 'style="transform:rotate(-90deg)"' : '';

        html += `<div class="col-span-full mb-1 ml-1">
          <button onclick="toggleSnapshotGroup('${ws}','${ss}')"
              class="flex items-center gap-2 group text-left py-0.5">
            <span class="text-[10px] text-zinc-600 uppercase tracking-widest group-hover:text-zinc-400 transition-colors">↳ ${ss}</span>
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
    await API.trigger(name, value, window._detectedBPM || null);
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
  if (panel.classList.contains('hidden')) {
    requestAnimationFrame(equalizeCardHeights);
    return;
  }

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
      // Name-based targets display as names — the strip index is live-resolved
      const addr = step.target
        ? `${step.target.channel} → ${step.target.submix} ⚡live`
        : (step.osc || '?');
      if (step.operation) {
        const op = step.operation;
        const opType = (op.type || '').toUpperCase();
        const bars  = op.bars || 2;
        const bpm   = op.bpm;
        const bpmLabel = bpm === 'clock' ? '<span class="text-orange-400/80">clock</span>' : (bpm || 140);
        const curve = op.curve ? ` · ${op.curve}` : '';
        const opColors = { RAMP: 'text-amber-400', LFO: 'text-purple-400' };
        const opColor = opColors[opType] || 'text-zinc-400';
        html += `<div class="flex items-center gap-2 font-mono bg-zinc-900/60 px-2.5 py-1.5 rounded-lg">
          <span class="text-zinc-500 text-xs">∿</span>
          <span class="text-orange-300 text-xs flex-1 truncate">${addr}</span>
          <span class="${opColor} text-xs font-bold">${opType}</span>
          <span class="text-zinc-600 text-xs">${bars}b @ ${bpmLabel}${curve}</span>
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
      const type = t.type || 'control_change';
      let label;
      if (type === 'note_on')       label = `NOTE ON ${t.note ?? '?'} ch${t.channel}`;
      else if (type === 'note_off') label = `NOTE OFF ${t.note ?? '?'} ch${t.channel}`;
      else                          label = `CC${t.number ?? '?'} ch${t.channel}`;
      html += `<span class="text-xs font-mono bg-zinc-800 text-zinc-300 px-2.5 py-1 rounded-lg border border-zinc-700">${label}</span>`;
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

// Build <option> list for workspace select from loaded snapshot map.
// Always leads with a "none" option — a macro with no workspace must not
// silently adopt the first workspace just because it renders first.
function _buildWorkspaceOptions(current) {
  const snapMap = window._snapshotMap || {};
  const workspaces = Object.keys(snapMap);
  let html = `<option value=""${!current ? ' selected' : ''}>— no switch —</option>`;
  if (current && !workspaces.includes(current)) {
    html += `<option value="${_esc(current)}" selected>${_esc(current)} (custom)</option>`;
  }
  workspaces.forEach(ws => {
    html += `<option value="${_esc(ws)}"${ws === current ? ' selected' : ''}>${_esc(ws)}</option>`;
  });
  return html;
}

// Build <option> list for snapshot select given a workspace.
// Matching is case-insensitive — the bridge lowercases snapshot names.
function _buildSnapshotOptions(workspace, current) {
  const snapMap = window._snapshotMap || {};
  const wsEntry = snapMap[workspace];
  const snapshots = wsEntry ? Object.values(wsEntry.snapshots || {}) : [];
  const matches = (s) => String(s).toLowerCase() === String(current || '').toLowerCase();
  let html = `<option value=""${!current ? ' selected' : ''}>— no switch —</option>`;
  if (current && !snapshots.some(matches)) {
    html += `<option value="${_esc(current)}" selected>${_esc(current)} (custom)</option>`;
  }
  snapshots.forEach(s => {
    html += `<option value="${_esc(s)}"${matches(s) ? ' selected' : ''}>${_esc(s)}</option>`;
  });
  return html;
}

// Called when workspace dropdown changes — refreshes snapshot options for that workspace
window.updateSnapshotOptions = function(name, workspace) {
  const sel = document.getElementById(`snapshot-select-${name}`);
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = _buildSnapshotOptions(workspace, current);
};

// Working copies while a card is being edited — lets add/remove-step buttons
// re-render the form without losing unsaved input (harvest → mutate → render)
window._editBuffers = window._editBuffers || {};

// Runtime fields the bridge writes back into mappings.json after a run —
// stripped when duplicating so clones start clean
const RUNTIME_FIELDS = ['name', 'value', 'progress', 'lfo_active',
                        'last_trigger', 'osc_preview', 'midi_trigger',
                        'routing_label'];

function _cleanMacro(m) {
  const c = JSON.parse(JSON.stringify(m));
  RUNTIME_FIELDS.forEach(f => delete c[f]);
  return c;
}

// ── Routing picker (fed by the discovered channel map) ──────────────────────
// Macros store routing as NAMES ({"target": {submix, channel}}): the bridge
// live-resolves the strip index from OSC feedback at fire time, because
// /1/volume{N} is strip-positional and shifts with stereo-link state.
// The step's osc address is kept as a fallback for when feedback is absent.

function _buildSubmixPickerOptions(selected) {
  const subs = (window._channelMap || {}).submixes || {};
  return Object.values(subs)
    .sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
    .map(s => `<option value="${_esc(s.name)}"${s.name === selected ? ' selected' : ''}>${_esc(s.name)} (submix ${_esc(s.index)})</option>`)
    .join('');
}

window.updateSendPickerOptions = function (name, selectedChannel) {
  const submixSel = document.getElementById(`routing-submix-${name}`);
  const sendSel   = document.getElementById(`routing-send-${name}`);
  if (!submixSel || !sendSel) return;
  const subs = (window._channelMap || {}).submixes || {};
  const sub = subs[submixSel.value];
  const sends = sub ? sub.sends || {} : {};
  const paramSel = document.getElementById(`routing-param-${name}`);
  const def = PARAM_DEFS[paramSel ? paramSel.value : ''] || {};
  if (def.channelDetail) {
    // EQ exists on hardware INPUTS and OUTPUTS, not software playback —
    // exclude row-2 sends, and offer the outputs (= the submixes: one
    // output strip per submix, aimed by walked index on the bridge side)
    const inputs = Object.entries(sends)
      .filter(([, s]) => (s.row ?? 1) !== 2)
      .map(([sn]) => `<option value="${_esc(sn)}"${sn === selectedChannel ? ' selected' : ''}>${_esc(sn)}</option>`)
      .join('');
    // Outputs aim by the hardware-MEASURED rule (offset = first hw
    // channel via the walked index; Main = 0). The bridge only fires it
    // on all-stereo output layouts and refuses otherwise — a mono-output
    // layout shows the option but the step refuses safely at fire time.
    const outputs = Object.values(subs)
      .sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
      .map(s => { const v = `__out3__${s.name}`;
        return `<option value="${_esc(v)}"${v === selectedChannel ? ' selected' : ''}>${_esc(s.name)}</option>`; })
      .join('');
    sendSel.innerHTML =
      `<optgroup label="Hardware inputs">${inputs}</optgroup>` +
      `<optgroup label="Hardware outputs">${outputs}</optgroup>`;
    return;
  }
  sendSel.innerHTML = Object.keys(sends)
    .map(sn => `<option value="${_esc(sn)}"${sn === selectedChannel ? ' selected' : ''}>${_esc(sn)}</option>`)
    .join('') || '<option value="">no channels discovered</option>';
};

// Current routing of the buffer's param step — used to restore picker state
function _currentRouting(m) {
  const paramStep = (m.steps || []).find(s => s.value === '{{param}}');
  if (paramStep && paramStep.target) {
    return { submix: paramStep.target.submix,
             channel: paramStep.target.channel,
             row: paramStep.target.row,
             param: paramStep.target.param || 'volume' };
  }
  // Legacy macros: reverse-lookup /setSubmix index and the raw address
  const subs = (window._channelMap || {}).submixes || {};
  const submixStep = (m.steps || []).find(s => s.osc === '/setSubmix');
  const submix = submixStep
    ? Object.values(subs).find(s => String(s.index) === String(submixStep.value))?.name
    : undefined;
  let channel;
  if (submix && paramStep) {
    channel = Object.entries(subs[submix]?.sends || {})
      .find(([, s]) => s.osc_address === paramStep.osc)?.[0];
  }
  return { submix, channel };
}

// ── Parameter descriptors (#12) ──────────────────────────────────────────────
// Each parameter class declares how its VALUE should be edited and shown.
// Adding a new mod target (EQ band, gain, ...) means adding a descriptor
// here — the step editor renders the right control generically.
const PARAM_DEFS = {
  volume: {
    widget: 'slider', min: 0, max: 1, step: 0.01, default: 0.8,
    fmt: v => `${Math.round(v * 100)}%`,
    mod: { range: true },       // ramps/LFOs sweep a window, not always 0..1
  },
  pan: {
    // step 0.005: a 0.01 step moved the ×200 L/R readout in twos, making
    // odd values (L31) unreachable by dragging
    widget: 'slider', min: 0, max: 1, step: 0.005, default: 0.5,
    fmt: v => {
      const d = Math.round((v - 0.5) * 200);
      return d === 0 ? 'C' : d < 0 ? `L${-d}` : `R${d}`;
    },
    mod: { range: true },       // e.g. an auto-pan between L60 and R30
  },
  mute: {
    // default UNMUTED: unchecking 'from trigger' must not arm a mute as a
    // side effect of an editor interaction
    widget: 'toggle', default: 0.0,
    options: [['1.0', 'Muted'], ['0.0', 'Unmuted']],
    mod: { threshold: true },   // gate point: where the LFO trips on/off
  },
};

// Global FX-section parameters (#5 phase 1) — fixed /3/... addresses, no
// channel/submix scope. Device values are normalized 0..1; shown as % until
// unit mappings are captured. Channel EQ pends its hardware scope check.
const _pct = v => `${Math.round(v * 100)}%`;
const _fxSlider = (label, addr, dflt = 0.5) => ({
  widget: 'slider', min: 0, max: 1, step: 0.01, default: dflt, fmt: _pct,
  mod: { range: true }, global: true, label, addr,
});
Object.assign(PARAM_DEFS, {
  reverb_enable: { widget: 'toggle', default: 1.0, global: true,
                   label: 'Reverb On/Off', addr: '/3/reverbEnable',
                   options: [['1.0', 'On'], ['0.0', 'Off']],
                   mod: { threshold: true } },
  reverb_time:     _fxSlider('Reverb Time', '/3/reverbTime'),
  reverb_volume:   _fxSlider('Reverb Volume', '/3/reverbVolume', 0.8),
  reverb_width:    _fxSlider('Reverb Width', '/3/reverbWidth'),
  reverb_predelay: _fxSlider('Reverb Predelay', '/3/reverbPredelay', 0.0),
  echo_enable: { widget: 'toggle', default: 1.0, global: true,
                 label: 'Echo On/Off', addr: '/3/echoEnable',
                 options: [['1.0', 'On'], ['0.0', 'Off']],
                 mod: { threshold: true } },
  echo_time:     _fxSlider('Echo Time', '/3/echoDelaytime'),
  echo_feedback: _fxSlider('Echo Feedback', '/3/echoFeedback', 0.2),
  echo_volume:   _fxSlider('Echo Volume', '/3/echoVolume', 0.8),
  echo_width:    _fxSlider('Echo Width', '/3/echoWidth'),
});

// Channel EQ (#5 phase 2, hardware-verified) — page-2 channel-detail params.
// Channel-scoped like mute (no submix); the bridge aims the page-2 window
// via /setBankStart with LAYOUT-KEYED verified widths and REFUSES when the
// live layout has no width entry (#16) — a refused step logs why. Keys and
// addresses mirror bridge.CHANNEL_DETAIL_PARAMS exactly. Values are the
// device's normalized 0..1 floats; shown as % until unit maps are captured.
const _eqSlider = (label, addr, dflt = 0.5) => ({
  widget: 'slider', min: 0, max: 1, step: 0.01, default: dflt, fmt: _pct,
  mod: { range: true }, channelDetail: true, label, addr,
});
Object.assign(PARAM_DEFS, {
  eq_enable: { widget: 'toggle', default: 1.0, channelDetail: true,
               label: 'EQ On/Off', addr: '/2/eqEnable',
               options: [['1.0', 'On'], ['0.0', 'Off']],
               mod: { threshold: true } },
  eq_gain_1:   _eqSlider('EQ Band 1 Gain', '/2/eqGain1'),
  eq_freq_1:   _eqSlider('EQ Band 1 Freq', '/2/eqFreq1'),
  eq_q_1:      _eqSlider('EQ Band 1 Q',    '/2/eqQ1'),
  eq_gain_2:   _eqSlider('EQ Band 2 Gain', '/2/eqGain2'),
  eq_freq_2:   _eqSlider('EQ Band 2 Freq', '/2/eqFreq2'),
  eq_q_2:      _eqSlider('EQ Band 2 Q',    '/2/eqQ2'),
  eq_gain_3:   _eqSlider('EQ Band 3 Gain', '/2/eqGain3'),
  eq_freq_3:   _eqSlider('EQ Band 3 Freq', '/2/eqFreq3'),
  eq_q_3:      _eqSlider('EQ Band 3 Q',    '/2/eqQ3'),
  lowcut_freq: _eqSlider('Low Cut Freq',   '/2/lowcutFreq', 0.0),
});

// Dynamics / Auto-Level / input stage (#20) — same page-2 machinery as EQ.
// First tranche (probe-confirmed addresses); comp/exp threshold, ratio,
// attack, release follow once the device inventory round lands.
const _detailToggle = (label, addr, dflt = 0.0) => ({
  widget: 'toggle', default: dflt, channelDetail: true, label, addr,
  options: [['1.0', 'On'], ['0.0', 'Off']], mod: { threshold: true },
});
// Unit ranges below are hardware-MEASURED (#20 inventory: multi-point
// fits, every sampled point on its line) — floats stay 0..1 on the wire,
// the fmt shows the device's real units.
const _unitSlider = (label, addr, fmt, dflt = 0.5) => ({
  widget: 'slider', min: 0, max: 1, step: 0.01, default: dflt, fmt,
  mod: { range: true }, channelDetail: true, label, addr,
});
Object.assign(PARAM_DEFS, {
  dyn_enable:    _detailToggle('Dynamics On/Off',   '/2/compexpEnable'),
  dyn_gain:      _unitSlider('Dynamics Gain',       '/2/compexpGain',
                             v => `${(v * 60 - 30).toFixed(1)} dB`),
  comp_thresh:   _unitSlider('Comp Threshold',      '/2/compTrsh',
                             v => `${(v * 60 - 60).toFixed(1)} dB`),
  comp_ratio:    _unitSlider('Comp Ratio',          '/2/compRatio',
                             v => `${(1 + 9 * v).toFixed(1)}:1`, 0.0),
  exp_thresh:    _unitSlider('Exp Threshold',       '/2/expTrsh',
                             v => `${(-99 + 79 * v).toFixed(1)} dB`, 0.0),
  exp_ratio:     _unitSlider('Exp Ratio',           '/2/expRatio',
                             v => `${(1 + 9 * v).toFixed(1)}:1`, 0.0),
  dyn_attack:    _unitSlider('Dyn Attack',          '/2/compexpAttack',
                             v => `${Math.round(200 * v)} ms`, 0.05),
  dyn_release:   _unitSlider('Dyn Release',         '/2/compexpRelease',
                             v => `${Math.round(100 + 899 * v)} ms`, 0.0),
  alev_enable:   _detailToggle('Auto Level On/Off', '/2/alevEnable'),
  alev_headroom: _unitSlider('Auto Level Headroom', '/2/alevHeadroom',
                             v => `${(3 + 9 * v).toFixed(1)} dB`),
  alev_maxgain:  _unitSlider('Auto Level Max Gain', '/2/alevMaxgain',
                             v => `${(18 * v).toFixed(1)} dB`),
  alev_risetime: _unitSlider('Auto Level Rise',     '/2/alevRisetime',
                             v => `${(0.1 + 9.8 * v).toFixed(1)} s`),
  lowcut_enable: _detailToggle('Low Cut On/Off',    '/2/lowcutEnable'),
  lowcut_grade:  _unitSlider('Low Cut Slope',       '/2/lowcutGrade',
                             v => `${6 + Math.round(v * 3) * 6} dB/oct`, 1.0),
  input_gain:    _eqSlider('Input Gain',            '/2/gain', 0.0),
  input_gain_r:  _eqSlider('Input Gain R',          '/2/gainRight', 0.0),
  phase:         _detailToggle('Phase Invert',      '/2/phase'),
  phase_r:       _detailToggle('Phase Invert R',    '/2/phaseRight'),
});

function _buildParamOptions(selected) {
  const chan = ['volume', 'mute', 'pan'];
  const chanOpts = chan.map(p =>
    `<option value="${p}"${p === (selected || 'volume') ? ' selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`).join('');
  const fxOpts = Object.entries(PARAM_DEFS).filter(([, d]) => d.global)
    .map(([p, d]) => `<option value="${p}"${p === selected ? ' selected' : ''}>${d.label}</option>`).join('');
  const _opt = ([p, d]) => `<option value="${p}"${p === selected ? ' selected' : ''}>${d.label}</option>`;
  const isEq = p => p.startsWith('eq_') || p === 'lowcut_freq';
  const eqOpts = Object.entries(PARAM_DEFS)
    .filter(([p, d]) => d.channelDetail && isEq(p)).map(_opt).join('');
  const dynOpts = Object.entries(PARAM_DEFS)
    .filter(([p, d]) => d.channelDetail && !isEq(p)).map(_opt).join('');
  return `<optgroup label="Channel">${chanOpts}</optgroup><optgroup label="Channel EQ">${eqOpts}</optgroup><optgroup label="Channel Dynamics / Input">${dynOpts}</optgroup><optgroup label="FX (global)">${fxOpts}</optgroup>`;
}

// Extra operation controls per parameter (#13): a mute LFO exposes its gate
// threshold; continuous params expose the sweep range, formatted per param
function _modControls(name, i, step) {
  const param = (step.target && step.target.param) || 'volume';
  const def = PARAM_DEFS[param];
  const op = step.operation || {};
  if (!def || !def.mod) return '';
  let html = '';
  if (def.mod.threshold) {
    const t = Number.isFinite(parseFloat(op.threshold)) ? parseFloat(op.threshold) : 0.5;
    html += `<div class="flex gap-2 items-center">
      <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0" title="where the wave trips on/off">gate</span>
      <input data-field="steps.${i}.operation.threshold" type="range" min="0" max="1" step="0.01" value="${t}"
          class="flex-1 accent-orange-500" title="double-click resets to 50%"
          oninput="document.getElementById('mod-thr-${name}-${i}').textContent = Math.round(this.value*100)+'%'"
          ondblclick="this.value=0.5; document.getElementById('mod-thr-${name}-${i}').textContent='50%'">
      <span id="mod-thr-${name}-${i}" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0">${Math.round(t * 100)}%</span>
    </div>`;
  }
  if (def.mod.range) {
    const rng = Array.isArray(op.range) ? op.range : [def.min, def.max];
    const lo = Number.isFinite(parseFloat(rng[0])) ? parseFloat(rng[0]) : def.min;
    const hi = Number.isFinite(parseFloat(rng[1])) ? parseFloat(rng[1]) : def.max;
    // Two stacked rows — both sliders on one line was unreadably cramped
    // at card width (user screenshot)
    html += `<div class="space-y-2">
      <div class="flex gap-2 items-center">
        <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0">from</span>
        <input data-field="steps.${i}.operation.range.0" type="range" min="${def.min}" max="${def.max}" step="${def.step}" value="${lo}"
            class="flex-1 accent-orange-500" title="sweep start — double-click resets to ${def.fmt(def.min)}"
            oninput="document.getElementById('mod-lo-${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
            ondblclick="this.value=${def.min}; document.getElementById('mod-lo-${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
        <span id="mod-lo-${name}-${i}" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0">${def.fmt(lo)}</span>
      </div>
      <div class="flex gap-2 items-center">
        <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0">to</span>
        <input data-field="steps.${i}.operation.range.1" type="range" min="${def.min}" max="${def.max}" step="${def.step}" value="${hi}"
            class="flex-1 accent-orange-500" title="sweep end — double-click resets to ${def.fmt(def.max)}"
            oninput="document.getElementById('mod-hi-${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
            ondblclick="this.value=${def.max}; document.getElementById('mod-hi-${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
        <span id="mod-hi-${name}-${i}" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0">${def.fmt(hi)}</span>
      </div>
    </div>`;
  }
  return html;
}

window.fmtParamValue = function (param, v) {
  const def = PARAM_DEFS[param];
  return def && def.fmt ? def.fmt(parseFloat(v)) : String(v);
};

// Switch a step between SET (instant value) and RAMP/LFO (trigger-driven
// operation over time). Operations only execute with value {{param}}, and a
// SET step is where the parameter widgets (pan slider, mute toggle) live.
window.changeStepMode = function (name, i, mode) {
  const m = _harvestEditor(name);
  const step = (m.steps || [])[i];
  if (!step) return;
  const def = PARAM_DEFS[(step.target && step.target.param) || 'volume'] || {};
  if (mode === 'set') {
    delete step.operation;
    if (step.value === '{{param}}') {
      step.value = String(def.default ?? 1.0);
    }
  } else {
    step.operation = { ...(step.operation || { bars: 2, bpm: 140 }), type: mode };
    step.value = '{{param}}';
    // Seed the parameter's modulation shaping controls (#13)
    if (def.mod?.threshold && step.operation.threshold == null) step.operation.threshold = 0.5;
    if (def.mod?.range && !Array.isArray(step.operation.range)) {
      step.operation.range = [def.min ?? 0, def.max ?? 1];
    }
  }
  editDetail(name);
};

// Switch a target step between CC-driven ({{param}}) and a static value
window.toggleFollowParam = function (name, i) {
  const m = _harvestEditor(name);
  const step = (m.steps || [])[i];
  if (!step) return;
  const def = PARAM_DEFS[(step.target && step.target.param) || 'volume'] || {};
  step.value = step.value === '{{param}}'
    ? String(def.default ?? 0.5)
    : '{{param}}';
  editDetail(name);
};

// Value control for a target step, driven by the parameter descriptor
function _valueControl(name, i, step, nc, sc) {
  const param = (step.target && step.target.param) || 'volume';
  const def = PARAM_DEFS[param];
  const follow = step.value === '{{param}}';
  const followBox = `<label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none shrink-0"
      title="Value comes from the trigger (CC value / FIRE param) at fire time">
    <input type="checkbox" class="w-3 h-3 accent-orange-500"${follow ? ' checked' : ''}
        onchange="toggleFollowParam('${name}',${i})"> from trigger
  </label>`;
  if (follow || !def) {
    return `<div class="flex gap-2 items-center flex-1">
      ${follow ? `<span class="flex-1 text-xs text-zinc-500 font-mono px-1">value follows the trigger</span>`
               : `<input data-field="steps.${i}.value" value="${_esc(step.value ?? '')}" class="${nc}" placeholder="value">`}
      ${followBox}
    </div>`;
  }
  const v = parseFloat(step.value);
  const val = Number.isFinite(v) ? v : (def.default ?? 0);
  if (def.widget === 'toggle') {
    return `<div class="flex gap-2 items-center flex-1">
      <select data-field="steps.${i}.value" class="${sc}">
        ${def.options.map(([ov, ol]) =>
          `<option value="${ov}"${parseFloat(ov) === val ? ' selected' : ''}>${ol}</option>`).join('')}
      </select>
      ${followBox}
    </div>`;
  }
  // slider
  return `<div class="flex gap-2 items-center flex-1">
    <input data-field="steps.${i}.value" type="range"
        min="${def.min}" max="${def.max}" step="${def.step}" value="${val}"
        class="flex-1 accent-orange-500" title="double-click resets to ${def.fmt(def.default)}"
        oninput="document.getElementById('val-label-${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
        ondblclick="this.value=${def.default}; document.getElementById('val-label-${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
    <span id="val-label-${name}-${i}" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0">${def.fmt(val)}</span>
    ${followBox}
  </div>`;
}

// Mute is global-per-channel — the submix dropdown is meaningless for it
window.updateParamScope = function (name) {
  const paramSel  = document.getElementById(`routing-param-${name}`);
  const submixSel = document.getElementById(`routing-submix-${name}`);
  const sendSel   = document.getElementById(`routing-send-${name}`);
  if (!paramSel || !submixSel) return;
  const def = PARAM_DEFS[paramSel.value] || {};
  const isMute = paramSel.value === 'mute';
  const isGlobal = !!def.global;
  const isDetail = !!def.channelDetail;
  submixSel.disabled = isMute || isGlobal || isDetail;
  submixSel.title = isGlobal ? 'Global FX parameter — no routing applies'
    : isDetail ? 'Channel EQ addresses the channel — no submix applies'
    : isMute ? 'Mute is global per channel — no submix applies' : '';
  if (sendSel) {
    sendSel.disabled = isGlobal;
    sendSel.title = isGlobal ? 'Global FX parameter — no routing applies' : '';
  }
  // Re-filter the channel list: EQ excludes playback and offers outputs
  updateSendPickerOptions(name, sendSel ? sendSel.value : undefined);
};

window.applyRouting = function (name) {
  const submixSel = document.getElementById(`routing-submix-${name}`);
  const sendSel   = document.getElementById(`routing-send-${name}`);
  const paramSel0 = document.getElementById(`routing-param-${name}`);
  const def0 = PARAM_DEFS[paramSel0 ? paramSel0.value : ''] || {};
  if (def0.global) {
    // Global FX parameter — no channel/submix, fixed address fallback
    const g = _harvestEditor(name);
    g.steps = (g.steps || []).filter(s => s.osc !== '/setSubmix');
    const pStep = g.steps.find(s => s.value === '{{param}}');
    const gTarget = { param: paramSel0.value };
    if (pStep) { pStep.target = gTarget; pStep.osc = def0.addr; }
    else g.steps.push({ osc: def0.addr, target: gTarget, value: '{{param}}',
                        operation: { type: 'ramp', bars: 2, bpm: 140,
                                     range: [0, 1] } });
    editDetail(name);
    return;
  }
  if (!submixSel || !sendSel || !sendSel.value) return;
  const sub = ((window._channelMap || {}).submixes || {})[submixSel.value];
  if (!sub) return;
  if (def0.channelDetail) {
    // EQ targets a hardware channel directly: input picks resolve by name
    // against the live bank (verified widths); output picks (__out3__) aim
    // by walked submix index — row 3 tells the bridge which path
    const isOut = sendSel.value.startsWith('__out3__');
    const pickedSend = (sub.sends || {})[sendSel.value];
    const target = { channel: isOut ? sendSel.value.slice(8)
                              : ((pickedSend && pickedSend.name) || sendSel.value),
                     param: paramSel0.value };
    if (isOut) target.row = 3;
    const m0 = _harvestEditor(name);
    m0.steps = (m0.steps || []).filter(s => s.osc !== '/setSubmix');
    const pStep0 = m0.steps.find(s => s.value === '{{param}}');
    if (pStep0) { pStep0.target = target; pStep0.osc = def0.addr; }
    else m0.steps.push({ osc: def0.addr, target,
                         value: String(def0.default ?? 0.5) });
    editDetail(name);
    return;
  }
  const send = (sub.sends || {})[sendSel.value];

  const m = _harvestEditor(name);
  m.steps = m.steps || [];
  // The bridge sends /setSubmix itself when resolving a target —
  // a legacy explicit step would double-send it
  m.steps = m.steps.filter(s => s.osc !== '/setSubmix');
  const paramStep = m.steps.find(s => s.value === '{{param}}');
  // target.channel is the raw trackname (send.name); the picker KEY may
  // carry a "(playback)" suffix that the device never reports
  const target = { submix: submixSel.value,
                   channel: (send && send.name) || sendSel.value };
  if (send && send.row === 2) target.row = 2;  // software-playback send
  const paramSel = document.getElementById(`routing-param-${name}`);
  const param = paramSel ? paramSel.value : 'volume';
  if (param !== 'volume') target.param = param;
  const paramDef = PARAM_DEFS[param] || {};
  // Mute is global-per-channel (#10) and channel-detail params (EQ, #5p2)
  // are addressed by channel — neither stores a submix
  if (param === 'mute' || paramDef.channelDetail) delete target.submix;
  // Fallback address must match the parameter, not always the volume
  const ch = send ? send.channel : null;
  const fallbackAddr = !send ? ''
    : paramDef.channelDetail ? paramDef.addr
    : param === 'mute' ? `/1/mute/1/${ch}`
    : param === 'pan'  ? `/1/pan${ch}`
    : send.osc_address;
  if (paramStep) {
    paramStep.target = target;
    paramStep.osc = fallbackAddr;
  } else if (param === 'volume') {
    // Volume defaults to a ramp — the classic send macro
    m.steps.push({ osc: fallbackAddr, target, value: '{{param}}',
                   operation: { type: 'ramp', bars: 2, bpm: 140 } });
  } else {
    // Pan/mute default to a SET step so their value widgets (slider,
    // toggle) appear immediately — switch to RAMP/LFO via the mode select
    m.steps.push({ osc: fallbackAddr, target,
                   value: String(paramDef.default ?? 0.5) });
  }
  editDetail(name);
};

// ── Step / trigger management (harvest → mutate buffer → re-render) ─────────
window.addEditorStep = function (name, kind) {
  const m = _harvestEditor(name);
  m.steps = m.steps || [];
  if (kind === 'raw') {
    // Advanced escape hatch — the only place a bare address is typed
    m.steps.push({ osc: '', value: '1.0' });
    editDetail(name);
    return;
  }
  // New steps are born ROUTED (#14): mapping addresses is the bridge's job.
  // The step takes the routing picker's CURRENT selections.
  const submixSel = document.getElementById(`routing-submix-${name}`);
  const sendSel   = document.getElementById(`routing-send-${name}`);
  const paramSel  = document.getElementById(`routing-param-${name}`);
  const sub  = submixSel ? ((window._channelMap || {}).submixes || {})[submixSel.value] : null;
  const send = sub && sendSel ? (sub.sends || {})[sendSel.value] : null;
  const param = paramSel ? paramSel.value : 'volume';
  const def = PARAM_DEFS[param] || {};
  if (!send) {  // no channel map loaded — raw fallback is all we can offer
    m.steps.push({ osc: '', value: kind === 'operation' ? '{{param}}' : '1.0',
                   ...(kind === 'operation' ? { operation: { type: 'ramp', bars: 2, bpm: 140 } } : {}) });
    editDetail(name);
    return;
  }
  const target = { channel: send.name || sendSel.value };
  if (param !== 'volume') target.param = param;
  if (param !== 'mute') target.submix = submixSel.value;
  if (send.row === 2) target.row = 2;
  const ch = send.channel;
  const fallback = param === 'mute' ? `/1/mute/1/${ch}`
    : param === 'pan' ? `/1/pan${ch}` : send.osc_address;
  if (kind === 'operation') {
    const op = { type: 'ramp', bars: 2, bpm: 140 };
    if (def.mod?.threshold) op.threshold = 0.5;
    if (def.mod?.range) op.range = [def.min ?? 0, def.max ?? 1];
    m.steps.push({ osc: fallback, target, value: '{{param}}', operation: op });
  } else {
    m.steps.push({ osc: fallback, target, value: String(def.default ?? 1.0) });
  }
  editDetail(name);
};

window.removeEditorStep = function (name, i) {
  const m = _harvestEditor(name);
  (m.steps || []).splice(i, 1);
  editDetail(name);
};

// MIDI-learn (#7): arm a one-shot capture — the next CC or note from any
// connected device (or MIDIEmu) fills this trigger's type/number/channel
window.learnTrigger = function (name, i) {
  const btn = document.getElementById(`learn-btn-${name}-${i}`);
  if (window._midiLearn) {          // second click cancels
    window._midiLearn = null;
    if (btn) btn.textContent = 'learn';
    return;
  }
  if (btn) btn.textContent = 'waiting…';
  window._midiLearn = (captured) => {
    const m = _harvestEditor(name);
    const trig = (m.midi_triggers || [])[i];
    if (trig) {
      trig.type = captured.type;
      trig.channel = captured.channel;
      delete trig.number;
      delete trig.note;
      if (captured.type === 'control_change') trig.number = captured.number;
      else trig.note = captured.note;
    }
    editDetail(name);
  };
};

window.addEditorTrigger = function (name) {
  const m = _harvestEditor(name);
  m.midi_triggers = m.midi_triggers || [];
  m.midi_triggers.push({ type: 'control_change', number: 0, channel: 1,
                         use_value_as_param: true });
  editDetail(name);
};

window.removeEditorTrigger = function (name, i) {
  const m = _harvestEditor(name);
  (m.midi_triggers || []).splice(i, 1);
  editDetail(name);
};

function editDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const arrow = document.getElementById(`detail-arrow-${name}`);
  if (!panel || !macros[name]) return;
  if (!window._editBuffers[name]) {
    window._editBuffers[name] = JSON.parse(JSON.stringify(macros[name]));
  }
  const m = window._editBuffers[name];

  panel.classList.remove('hidden');
  if (arrow) arrow.style.transform = 'rotate(180deg)';

  // Shared input CSS classes
  const ic  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-full';
  const sc  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none';
  const nc  = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-20 text-center';

  // Remove-step button (shared)
  const _removeBtn = (i) => `<button onclick="removeEditorStep('${name}',${i})" title="Remove step"
      class="shrink-0 w-8 text-zinc-600 hover:text-red-400 transition-colors"><i class="fas fa-xmark"></i></button>`;

  // Steps
  const stepsHtml = (m.steps || []).map((step, i) => {
    const addr = _esc(step.osc || '');
    // Name-based target: read-only display — the routing picker above is
    // how it changes, and duplicating it as editable fields was confusing
    const targetHtml = step.target ? `<div class="flex gap-2 items-center">
        <span class="text-[10px] text-emerald-500/90 font-bold tracking-widest shrink-0" title="Resolved live from device feedback at fire time">LIVE</span>
        <span class="text-sm text-white font-mono flex-1 truncate">${
          (PARAM_DEFS[step.target.param] || {}).global
            ? `${_esc((PARAM_DEFS[step.target.param] || {}).label || step.target.param)} <span class="text-purple-400 text-xs">fx</span>`
            : step.target.param === 'mute'
            ? `${_esc(step.target.channel ?? '')} <span class="text-purple-400 text-xs">mute</span>${step.target.row === 2 ? ' <span class="text-zinc-500 text-xs">(playback)</span>' : step.target.row === 3 ? ' <span class="text-zinc-500 text-xs">(output)</span>' : ''}`
            : `${_esc(step.target.channel ?? '')} <span class="text-zinc-500">→</span> ${_esc(step.target.submix ?? '')}${step.target.row === 2 ? ' <span class="text-zinc-500 text-xs">(playback)</span>' : step.target.row === 3 ? ' <span class="text-zinc-500 text-xs">(output)</span>' : ''}${step.target.param && step.target.param !== 'volume' ? ` <span class="text-purple-400 text-xs">${_esc(step.target.param)}</span>` : ''}`
        }</span>
        <span class="text-[10px] text-zinc-600 shrink-0">set via Routing above</span>
      </div>` : '';
    // For target steps the address is machine-managed bookkeeping (used only
    // when device feedback is down) — show it muted, not as an input nobody
    // can be expected to decode
    const addrField = step.target
      ? `<div class="flex-1 text-[10px] text-zinc-600 font-mono px-1 truncate" title="Machine-managed: only used when device feedback is unavailable">fallback: ${addr || '—'}</div>`
      : `<input data-field="steps.${i}.osc" value="${addr}" class="${ic}" placeholder="OSC address">`;
    // SET / RAMP / LFO mode select — SET removes the operation and exposes
    // the parameter's value widget (this is how you get the pan slider)
    const modeSel = (mode) => `<select onchange="changeStepMode('${name}',${i},this.value)" class="${sc} shrink-0"
        title="SET writes a value instantly; RAMP/LFO animate the value from the trigger">
        <option value="set"${mode==='set'?' selected':''}>SET</option>
        <option value="ramp"${mode==='ramp'?' selected':''}>RAMP</option>
        <option value="lfo"${mode==='lfo'?' selected':''}>LFO</option>
      </select>`;
    if (step.operation) {
      const op = step.operation;
      return `<div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
        ${targetHtml}
        <div class="flex gap-2 items-center">
          ${addrField}
          ${modeSel(op.type === 'lfo' ? 'lfo' : 'ramp')}
          ${_removeBtn(i)}
        </div>
        <div class="flex gap-2 items-center">
          <input data-field="steps.${i}.operation.bars" type="number" min="1" value="${_esc(op.bars??2)}" class="${nc}">
          <span class="text-zinc-500 text-xs shrink-0">bars @</span>
          <input data-field="steps.${i}.operation.bpm" id="bpm-input-${name}-${i}"
              type="${op.bpm==='clock'?'text':'number'}" min="20" max="400"
              value="${op.bpm==='clock'?'clock':_esc(op.bpm??140)}"
              class="${nc}" ${op.bpm==='clock'?'disabled':''}>
          <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none shrink-0"
              title="Sync to live MIDI clock tempo">
            <input type="checkbox" id="bpm-clock-cb-${name}-${i}" class="w-3 h-3 accent-orange-500"
                ${op.bpm==='clock'?'checked':''}
                onchange="toggleBPMClock('${name}',${i})">
            clock
          </label>
        </div>
        ${step.target ? _modControls(name, i, step) : ''}
      </div>`;
    } else {
      const val = _esc(step.value ?? '');
      // Target steps get a parameter-aware value control (#12): pan shows
      // L/C/R, mute a toggle, volume a fader — raw numbers only for raw steps
      const valueHtml = step.target
        ? _valueControl(name, i, step, nc, sc)
        : `<input data-field="steps.${i}.value" value="${val}" class="${nc}" placeholder="value">`;
      return `<div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
        ${targetHtml}
        <div class="flex gap-2 items-center">
          ${addrField}
          ${step.target ? modeSel('set') : valueHtml}
          ${step.target ? '' : modeSel('set')}
          ${_removeBtn(i)}
        </div>
        ${step.target ? `<div class="flex gap-2 items-center">${valueHtml}</div>` : ''}
      </div>`;
    }
  }).join('');

  // MIDI triggers — type-aware: CC uses 'number', Note On/Off use 'note'
  const midiHtml = (m.midi_triggers || []).map((t, i) => {
    const type    = t.type || 'control_change';
    const isNote  = type === 'note_on' || type === 'note_off';
    const numField = isNote ? `midi_triggers.${i}.note`   : `midi_triggers.${i}.number`;
    const numValue = isNote ? (t.note ?? 0)               : (t.number ?? 0);
    return `<div class="flex gap-2 items-center bg-zinc-900/80 border border-zinc-800 px-2.5 py-2 rounded-xl">
      <select data-field="midi_triggers.${i}.type" class="${sc} shrink-0">
        <option value="control_change"${type==='control_change'?' selected':''}>CC</option>
        <option value="note_on"${type==='note_on'?' selected':''}>Note On</option>
        <option value="note_off"${type==='note_off'?' selected':''}>Note Off</option>
      </select>
      <span class="text-zinc-500 text-xs shrink-0">#</span>
      <input data-field="${numField}" type="number" min="0" max="127" value="${_esc(numValue)}" class="${nc}">
      <span class="text-zinc-500 text-xs shrink-0">ch</span>
      <input data-field="midi_triggers.${i}.channel" type="number" min="1" max="16" value="${_esc(t.channel)}" class="${nc}">
      <button id="learn-btn-${name}-${i}" onclick="learnTrigger('${name}',${i})"
          title="Capture the next CC or note from any device (or the MIDI emulator)"
          class="shrink-0 text-xs px-2 py-1 rounded-lg bg-zinc-800 border border-zinc-700 text-cyan-400 hover:text-white transition-all">learn</button>
      <button onclick="removeEditorTrigger('${name}',${i})" title="Remove trigger"
          class="shrink-0 w-8 text-zinc-600 hover:text-red-400 transition-colors"><i class="fas fa-xmark"></i></button>
    </div>`;
  }).join('');

  // Routing picker — restored to the macro's current routing, only when a
  // discovered channel map is loaded
  const hasChannelMap = Object.keys((window._channelMap || {}).submixes || {}).length > 0;
  const routing = _currentRouting(m);
  const routingPickerHtml = hasChannelMap ? `<div>
    <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">Routing (from your device)</div>
    <div class="flex gap-2 items-center flex-wrap">
      <div class="flex-1 min-w-[120px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Input channel</div>
        <select id="routing-send-${name}" class="${sc} w-full"></select>
      </div>
      <span class="text-zinc-500 text-xs shrink-0">→</span>
      <div class="flex-1 min-w-[160px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Output submix</div>
        <select id="routing-submix-${name}" onchange="updateSendPickerOptions('${name}')" class="${sc} w-full">
          ${_buildSubmixPickerOptions(routing.submix)}
        </select>
      </div>
      <div class="shrink-0">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Parameter</div>
        <select id="routing-param-${name}" onchange="updateParamScope('${name}')" class="${sc}">
          ${_buildParamOptions(routing.param)}
        </select>
      </div>
      <button onclick="applyRouting('${name}')" title="Store this routing by name — the strip index is resolved live from device feedback when the macro fires"
          class="shrink-0 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-300 hover:text-white text-xs px-3 py-2 rounded-lg transition-all">
        Set routing
      </button>
    </div>
  </div>` : '';

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
        <select data-field="workspace" class="${ic}" onchange="updateSnapshotOptions('${name}', this.value)">
          ${_buildWorkspaceOptions(m.workspace)}
        </select>
      </div>
      <div class="flex-1">
        <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Snapshot</div>
        <select id="snapshot-select-${name}" data-field="snapshot" class="${ic}">
          ${_buildSnapshotOptions(m.workspace, m.snapshot)}
        </select>
      </div>
    </div>

    ${routingPickerHtml}

    <div>
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">Steps</div>
      <div class="space-y-2">${stepsHtml || '<div class="text-zinc-600 text-xs italic">no steps yet</div>'}</div>
      <div class="flex gap-2 mt-2 flex-wrap">
        <button onclick="addEditorStep('${name}','value')"
            title="Adds a SET step targeting the routing picker's current channel/parameter — no address needed"
            class="text-xs text-zinc-500 hover:text-orange-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800">
          <i class="fas fa-plus text-[9px]"></i> set step
        </button>
        <button onclick="addEditorStep('${name}','operation')"
            title="Adds a RAMP step targeting the routing picker's current channel/parameter"
            class="text-xs text-zinc-500 hover:text-orange-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800">
          <i class="fas fa-plus text-[9px]"></i> ramp/LFO step
        </button>
        <button onclick="addEditorStep('${name}','raw')"
            title="Advanced: a step with a hand-typed OSC address"
            class="text-xs text-zinc-700 hover:text-zinc-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800">
          <i class="fas fa-plus text-[9px]"></i> raw OSC step
        </button>
      </div>
    </div>

    <div>
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">MIDI Triggers</div>
      <div class="space-y-1.5">${midiHtml || '<div class="text-zinc-600 text-xs italic">no triggers — fire from the UI or MQTT only</div>'}</div>
      <button onclick="addEditorTrigger('${name}')"
          class="text-xs text-zinc-500 hover:text-orange-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800 mt-2">
        <i class="fas fa-plus text-[9px]"></i> MIDI trigger
      </button>
    </div>

    <div class="flex gap-2 pt-2 border-t border-zinc-800">
      <button id="edit-save-${name}" onclick="saveInlineEdit('${name}')"
          class="flex-1 bg-orange-500 hover:bg-orange-400 active:scale-95 text-black font-bold py-2 rounded-xl text-sm transition-all">
        Save
      </button>
      <button onclick="cancelInlineEdit('${name}')"
          class="px-5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 py-2 rounded-xl text-sm transition-all">
        Cancel
      </button>
      <button onclick="deleteMacroUI('${name}')" title="Delete this macro"
          class="px-4 bg-zinc-900 hover:bg-red-900/40 border border-zinc-800 hover:border-red-800/60 text-zinc-600 hover:text-red-400 py-2 rounded-xl text-sm transition-all">
        <i class="fas fa-trash text-xs"></i>
      </button>
    </div>

  </div>`;

  // Populate the cascading send picker, restoring the macro's current send,
  // and set the submix dropdown's enabled state for the current parameter
  if (hasChannelMap) {
    updateSendPickerOptions(name, routing.row === 3
      ? `__out3__${routing.channel}` : routing.channel);
    updateParamScope(name);
  }
}

// Read every [data-field] input in the open editor back into the edit buffer.
// Returns the buffer. Skip elements with no editor open (routing picker
// selects carry no data-field, so they never pollute the macro).
function _harvestEditor(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = window._editBuffers[name];
  if (!panel || !m) return m;

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
  return m;
}

async function saveInlineEdit(name) {
  const btn = document.getElementById(`edit-save-${name}`);
  const m = _harvestEditor(name);
  if (!m) return;

  if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }

  try {
    await API.saveMacro(name, m);
    window._lastLocalSave = { name, ts: Date.now() };  // suppress own WS echo
    macros[name] = JSON.parse(JSON.stringify(m));
    cancelInlineEdit(name);
    // Reopen in read-only mode to show the saved state
    setTimeout(() => toggleDetail(name), 30);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
    if (btn) { btn.textContent = 'Save'; btn.disabled = false; }
  }
}

function cancelInlineEdit(name) {
  delete window._editBuffers[name];
  const panel = document.getElementById(`detail-${name}`);
  const arrow = document.getElementById(`detail-arrow-${name}`);
  if (!panel) return;
  panel.classList.add('hidden');
  if (arrow) arrow.style.transform = '';
  // Re-equalise now this card has collapsed
  requestAnimationFrame(equalizeCardHeights);
}

// ── Delete macro ──────────────────────────────────────────────────────────────
async function deleteMacroUI(name) {
  if (!confirm(`Delete macro "${name}"?\n\nmappings.json is auto-backed-up first.`)) return;
  try {
    await API.deleteMacro(name);
    delete window._editBuffers[name];
    delete macros[name];
    renderCards();
  } catch (e) {
    alert(`Delete failed: ${e.message}`);
  }
}

// ── New Macro flow ────────────────────────────────────────────────────────────
const MACRO_NAME_RE = /^[A-Za-z0-9_\-]{1,64}$/;

function _blankMacroTemplate() {
  const subs = Object.values((window._channelMap || {}).submixes || {})
    .sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  const first = subs[0];
  const firstSendName = first ? Object.keys(first.sends || {})[0] : null;
  const firstSend = firstSendName ? first.sends[firstSendName] : null;
  const step = {
    osc: firstSend ? firstSend.osc_address : '/1/volume1',
    value: '{{param}}',
    operation: { type: 'ramp', bars: 2, bpm: 140 },
  };
  // Name-based target — live-resolved at fire time (strip indices drift
  // with stereo-link state, names don't)
  if (first && firstSendName) {
    step.target = { submix: first.name, channel: firstSendName };
  }
  return {
    description: '',
    force_switch: false,
    fire_mode: 'ignore',
    steps: [step],
    midi_triggers: [],
  };
}

function openNewMacro() {
  const modal = document.getElementById('new-macro-modal');
  const tmpl  = document.getElementById('new-macro-template');
  const nameEl = document.getElementById('new-macro-name');
  const errEl  = document.getElementById('new-macro-error');
  if (!modal) return;
  if (tmpl) {
    tmpl.innerHTML = '<option value="">Blank (send + ramp template)</option>' +
      Object.keys(macros).sort()
        .map(n => `<option value="${_esc(n)}">Duplicate: ${_esc(n)}</option>`)
        .join('');
  }
  if (errEl) errEl.classList.add('hidden');
  if (nameEl) nameEl.value = '';
  modal.classList.remove('hidden');
  if (nameEl) nameEl.focus();
}

function closeNewMacro() {
  const modal = document.getElementById('new-macro-modal');
  if (modal) modal.classList.add('hidden');
}

async function createNewMacro() {
  const nameEl = document.getElementById('new-macro-name');
  const tmpl   = document.getElementById('new-macro-template');
  const errEl  = document.getElementById('new-macro-error');
  const showErr = (msg) => {
    if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
  };
  const name = (nameEl ? nameEl.value : '').trim();

  if (!MACRO_NAME_RE.test(name)) {
    return showErr('Name must be 1–64 chars: letters, digits, _ or -');
  }
  if (macros[name]) {
    return showErr(`"${name}" already exists`);
  }

  const source = tmpl && tmpl.value ? macros[tmpl.value] : null;
  const body = source ? _cleanMacro(source) : _blankMacroTemplate();

  try {
    await API.saveMacro(name, body);
    macros[name] = body;
    closeNewMacro();
    renderCards();
    // Scroll to the new card and open it straight in edit mode
    requestAnimationFrame(() => {
      const card = document.getElementById(`card-${name}`);
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      editDetail(name);
    });
  } catch (e) {
    showErr(`Create failed: ${e.message}`);
  }
}

// Enter key in the name field creates
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter'
      && document.activeElement?.id === 'new-macro-name') {
    createNewMacro();
  }
});

// ── BPM clock toggle in step editor ──────────────────────────────────────────
window.toggleBPMClock = function(macroName, stepIndex) {
  const cb  = document.getElementById(`bpm-clock-cb-${macroName}-${stepIndex}`);
  const inp = document.getElementById(`bpm-input-${macroName}-${stepIndex}`);
  if (!cb || !inp) return;
  if (cb.checked) {
    inp.dataset.prevBpm = inp.value;   // stash the last numeric BPM
    inp.type     = 'text';
    inp.value    = 'clock';
    inp.disabled = true;
  } else {
    inp.type     = 'number';
    inp.value    = inp.dataset.prevBpm || '140';
    inp.disabled = false;
  }
};

// ── Settings menu ─────────────────────────────────────────────────────────────
async function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    try {
      const s = await API.getStatus();
      const info = document.getElementById('settings-status');
      if (info) {
        const wsCount = s.snapshot_map_workspaces || 0;
        const wsText = wsCount > 0
          ? `<span class="text-zinc-400">${wsCount} workspace${wsCount !== 1 ? 's' : ''}</span>`
          : `<span class="text-red-400" title="ufx2_snapshot_map.json not loaded">⚠ no snapshot map</span>`;
        const submixLabel = s.channel_map_submixes > 0 ? s.channel_map_submixes : 0;
        const submixEx = s.channel_map_is_example
          ? `<span class="text-amber-400/80" title="Using ufx2_channel_map.example.json — routing labels may not match your setup">${submixLabel} submix${submixLabel !== 1 ? 'es' : ''} (example)</span>`
          : `<span class="text-zinc-400">${submixLabel} submix${submixLabel !== 1 ? 'es' : ''}</span>`;
        info.innerHTML =
          `<span class="text-zinc-400">${s.macros} macro${s.macros !== 1 ? 's' : ''}</span>` +
          ` · ${submixEx}` +
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
    await API.reload();
    location.reload();
  }
}

// ── File upload (legacy — kept for drag-and-drop workflows) ──────────────────
function uploadFile(input, type) {
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  API.upload(type, formData)
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
    const text = await API.getConfig(configType);
    // getConfig returns raw text; pretty-print it
    textarea.value = JSON.stringify(JSON.parse(text), null, 2);
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
    await API.saveConfig(configType, textarea.value);
    modal.classList.add('hidden');
    if (configType === 'snapshot_map') {
      // Refresh local snapshot map cache so detail panels show correct validation
      window._snapshotMap = await API.getSnapshotMap().catch(() => ({}));
    } else {
      // Hot-reload macro cards from updated bridge mappings
      await loadMacros();
      renderCards();
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

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeEditor(); closeNewMacro(); }
});
