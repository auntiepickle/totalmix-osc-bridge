/* ui/cards.js — classic card HTML, collapse state, group LED, duration helper, validity chips.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */


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
  if (label) label.textContent = `${_displayName(macroName, macros[macroName])} · ${ts}`;
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

// One trigger → its badge text. Shared by the card badge (first trigger) and
// the DETAILS panel (every trigger) so the #23 types read the same in both.
function _triggerLabel(t) {
  const type = t.type || 'control_change';
  if (type === 'note_on')  return `NOTE ON ${t.note ?? '?'} · ch${t.channel}`;
  if (type === 'note_off') return `NOTE OFF ${t.note ?? '?'} · ch${t.channel}`;
  if (type === 'program_change')    return `PC${t.number ?? '?'} · ch${t.channel}`;
  if (type === 'control_change_14') return `CC14:${t.number ?? '?'} · ch${t.channel}`;
  if (type === 'pitch_bend')        return `BEND · ch${t.channel}`;
  if (type === 'aftertouch')        return `AT · ch${t.channel}`;
  return `CC${t.number ?? '?'} · ch${t.channel}`;
}

function getMidiTriggerLabel(m) {
  const t = m.midi_triggers && m.midi_triggers[0];
  return t ? _triggerLabel(t) : '';
}

// ── Card HTML ─────────────────────────────────────────────────────────────────
// Pre-flight validity (#22 seed, shipped with #9): check a macro's stored
// names against the LOADED caches only — cheap, advisory, no network. The
// bridge still refuses at fire time; this just makes refusals visible before
// anyone fires. Returns [] when nothing is loaded to check against.
function macroTargetIssues(m) {
  const issues = [];
  const snapMap = window._snapshotMap || {};
  if (m.workspace && m.snapshot && Object.keys(snapMap).length) {
    const wsEntry = snapMap[m.workspace];
    if (!wsEntry) {
      issues.push(`workspace "${m.workspace}" not found in snapshot map`);
    } else if (!_snapshotNames(wsEntry)
        .some(n => n.toLowerCase() === String(m.snapshot).toLowerCase())) {
      issues.push(`snapshot "${m.snapshot}" not found in ${m.workspace}`);
    }
  }
  // #22: raw steps on strip-positional pages write blind — strip numbers
  // shift with stereo-link state, so the same step can hit a different
  // channel per snapshot. Fixed-address pages (/3/ FX) are exempt.
  (m.steps || []).forEach(step => {
    if (!step.target && step.osc && /^\/[12]\//.test(step.osc)) {
      issues.push(`raw step ${step.osc} writes a strip position blind — re-point it via the picker`);
    }
  });
  // #24: names checked against the LIVE picker inventory. A name absent
  // right now may still resolve at fire time via the bridge's learned
  // aliases (a pair absorbed it) — so this stays advisory wording.
  const picker = window._picker || {};
  const inputNames = new Set((picker.inputs || []).map(i => i.name));
  const outputNames = new Set((picker.outputs || []).map(o => o.name));
  if (inputNames.size || outputNames.size) {
    (m.steps || []).forEach(step => {
      const t = step.target;
      if (!t) return;                              // raw steps: no names to check
      if ((PARAM_DEFS[t.param] || {}).global) return;  // fixed /3/ address
      if (t.row === 3) {
        if (t.channel && !outputNames.has(t.channel)) {
          issues.push(`output "${t.channel}" not on the device right now`);
        }
        return;
      }
      if (t.channel && !inputNames.has(t.channel)) {
        issues.push(`channel "${t.channel}" not on the device right now`);
      }
      if (t.submix && !outputNames.has(t.submix)) {
        issues.push(`submix "${t.submix}" not on the device right now`);
      }
    });
  }
  return [...new Set(issues)];
}

// Red issue strip shared by the read-only detail and both editors
function _issueStripHTML(issues, withSnapshotFixButton) {
  if (!issues.length) return '';
  const fixBtn = withSnapshotFixButton
    && issues.some(s => s.includes('snapshot map') || s.startsWith('snapshot '))
    ? `<button onclick="openEditor('snapshot_map')" class="underline hover:text-red-300 ml-1">Fix in editor</button>`
    : '';
  return `<div class="flex items-center gap-2 bg-red-900/20 border border-red-800/40 text-red-400 text-xs px-3 py-2 rounded-lg">
    <i class="fas fa-triangle-exclamation shrink-0"></i>
    <span>${_esc(issues.join(' · '))}${fixBtn}</span>
  </div>`;
}

// #22: persistent last-fire outcome under the routing label — the truthful
// counterpart to the transient LED flash. Rendered into a stable slot so
// WS updates can refresh it surgically without re-rendering the card.
function _healthLineHTML(lf) {
  if (!lf || !lf.status) return '';
  const t = new Date(lf.at * 1000).toLocaleTimeString();
  if (lf.status === 'ok')
    return `<p class="text-[10px] text-emerald-500">✓ fired ${t}</p>`;
  if (lf.status === 'partial') {
    const sk = lf.skipped_steps || [];
    return `<p class="text-[10px] text-amber-400" title="${_esc(sk.join(', '))}">◐ fired ${t} — ${sk.length} step${sk.length === 1 ? '' : 's'} skipped (${_esc(sk[0] || '')})</p>`;
  }
  return `<p class="text-[10px] text-red-400">⚠ skipped ${t} — ${_esc(lf.reason || 'unknown')}</p>`;
}

function _warnIconHTML(issues) {
  return issues.length
    ? `<i class="fas fa-triangle-exclamation text-amber-400 text-xs" title="${_esc(issues.join('; '))}"></i>`
    : '';
}

// #22 fix (field report): validity is checked against the PICKER inventory,
// which changes when snapshots re-pair/rename channels — so the warn icons
// must be recomputed after every switch, not just at page load. Surgical:
// only the per-card warn slots update; open editors and animations survive.
window.refreshValidity = function () {
  Object.keys(macros).forEach(name => {
    const slot = document.querySelector(`#${CSS.escape(`card:${name}`)} .warn-slot`);
    if (slot) slot.innerHTML = _warnIconHTML(macroTargetIssues(macros[name]));
  });
};

// KNOB step of a macro (continuous MIDI control), or null
function _knobStepOf(m) {
  return ((m && m.steps) || []).find(s => s.target && s.operation && s.operation.type === 'knob') || null;
}

// Client-side mirror of operations.shape_value's range map — for readouts
function _shapeKnob(v, op) {
  const rng = op && Array.isArray(op.range) ? op.range : null;
  return rng ? parseFloat(rng[0]) + v * (parseFloat(rng[1]) - parseFloat(rng[0])) : v;
}

// Section switch behind a continuous param (mirrors global_units.ENABLE_FOR):
// a low-cut knob is inaudible while low cut is off, so the strip shows the
// switch and the knob can flip it on with the first move.
const ENABLE_FOR = {};
['eq_gain_1','eq_gain_2','eq_gain_3','eq_freq_1','eq_freq_2','eq_freq_3',
 'eq_q_1','eq_q_2','eq_q_3','eq_type_1','eq_type_3'].forEach(p => ENABLE_FOR[p] = 'eq_enable');
ENABLE_FOR.lowcut_freq = 'lowcut_enable'; ENABLE_FOR.lowcut_grade = 'lowcut_enable';
['dyn_gain','comp_thresh','comp_ratio','exp_thresh','exp_ratio','dyn_attack','dyn_release']
  .forEach(p => ENABLE_FOR[p] = 'dyn_enable');
['alev_maxgain','alev_headroom','alev_risetime'].forEach(p => ENABLE_FOR[p] = 'alev_enable');
['reverb_time','reverb_volume','reverb_width','reverb_predelay'].forEach(p => ENABLE_FOR[p] = 'reverb_enable');
['echo_time','echo_feedback','echo_volume','echo_width'].forEach(p => ENABLE_FOR[p] = 'echo_enable');

// Companion params shown beside the knob (mirror of global_units.COMPANION_FOR
// / ENUM_LABELS): the low-cut slope next to a low-cut knob, EQ band type
// next to an EQ knob. Click cycles to the next option on the device.
const COMPANION_FOR = { lowcut_freq: ['lowcut_grade'], lowcut_grade: ['lowcut_freq'] };
[['1', 'eq_type_1'], ['2', null], ['3', 'eq_type_3']].forEach(([b, type]) => {
  const members = [`eq_freq_${b}`, `eq_gain_${b}`, `eq_q_${b}`];
  members.forEach(m => { COMPANION_FOR[m] = (type ? [type] : []).concat(members.filter(x => x !== m)); });
});
// Shape glyphs for the type dropdowns (#user request): the option shows the
// curve it selects. Overline + box diagonals - survive native pickers.
// Band 1 shelf acts below its freq (low shelf), band 3 above (high shelf).
const ENUM_GLYPHS = {
  eq_type_1: { 'Bell': '∩', 'Shelf': '‾╲_', 'High Pass': '╱‾', 'Low Pass': '‾╲' },
  eq_type_3: { 'Bell': '∩', 'Shelf': '_╱‾', 'High Pass': '╱‾', 'Low Pass': '‾╲' },
};
const _enumGlyph = (cp, label) => {
  const g = (ENUM_GLYPHS[cp] || {})[label];
  return g ? `${g}  ` : '';
};

const ENUM_LABELS = {   // orders wire-verified 2026-08-21 (RME's own labels)
  lowcut_grade: ['6 dB/oct', '12 dB/oct', '18 dB/oct', '24 dB/oct'],
  eq_type_1: ['Bell', 'Shelf', 'High Pass', 'Low Pass'],
  eq_type_3: ['Bell', 'Shelf', 'Low Pass', 'High Pass'],
};

// Continuous companions (a band's gain / Q / freq beside the main knob):
// compact live sliders writing through the companion endpoint
function _companionSlidersHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  return (COMPANION_FOR[param] || []).filter(cp => !ENUM_LABELS[cp]).map(cp => {
    const def = PARAM_DEFS[cp] || {};
    const v = (m.companions || {})[cp];
    const val = Number.isFinite(parseFloat(v)) ? parseFloat(v) : null;
    const short = (def.label || cp).replace(/^EQ Band \d /, '').replace(/^Low Cut /, '');
    return `<div class="flex gap-2 items-center">
      <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0">${_esc(short)}</span>
      <input id="knob-cps:${name}-${cp}" type="range" min="0" max="1" step="0.002" value="${val ?? 0.5}"
          class="flex-1 min-w-0 accent-zinc-400" title="${_esc(def.label || cp)} on the device — live"
          oninput="companionInput('${name}','${cp}',this.value)" ondblclick="resetCompToDefault('${name}','${cp}')"
          onpointerdown="window._knobDrag='${name}:${cp}'" onpointerup="window._knobDrag=null"
          onpointercancel="window._knobDrag=null" onlostpointercapture="window._knobDrag=null">
      <span id="knob-cpv:${name}-${cp}" onclick="startCompValEdit('${name}','${cp}')" title="tap to type a value" class="text-[10px] text-zinc-400 font-mono w-12 text-center shrink-0 cursor-text">${val == null ? '?' : (def.fmt ? def.fmt(val) : Math.round(val * 100) + '%')}</span>
    </div>`;
  }).join('');
}

function _companionChipsHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  return (COMPANION_FOR[param] || []).filter(cp => ENUM_LABELS[cp]).map(cp => {
    const labels = ENUM_LABELS[cp] || [];
    const pinned = (((step.operation || {}).companions) || {})[cp] != null;
    const v = (m.companions || {})[cp];
    const idx = Number.isFinite(parseFloat(v)) ? Math.round(parseFloat(v) * (labels.length - 1)) : null;
    return `<select id="knob-cp:${name}-${cp}" onchange="setKnobParam('${name}','${cp}',this.selectedIndex - 1,${labels.length})"
        title="${_esc(cp.replace(/_/g, ' '))} on the device${pinned ? ' (pinned: the knob re-asserts this choice)' : ''}"${pinned ? ' style="border-color:rgba(251,146,60,0.5)"' : ''}
        class="shrink-0 text-[10px] font-mono px-2 py-1 rounded-md border cursor-pointer transition-all ${idx == null ? 'bg-zinc-900 border-zinc-800 text-zinc-600' : 'bg-zinc-800 border-zinc-700 text-zinc-300 hover:text-white'}">
        <option value="" disabled${idx == null ? ' selected' : ''}>${_esc(cp.replace(/_/g, ' '))} ?</option>
        ${labels.map((l, i) => `<option value="${i}"${i === idx ? ' selected' : ''}>${_enumGlyph(cp, l)}${_esc(l)}</option>`).join('')}
      </select>`;
  }).join('');
}

function _enableChipHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  const en = ENABLE_FOR[param];
  if (!en) return '';
  const label = ((PARAM_DEFS[en] || {}).label || en).replace(/ On\/Off$/i, '');
  const st = m.enable_value;
  const cls = st === true  ? 'bg-green-900/40 border-green-700 text-green-400'
            : st === false ? 'bg-zinc-800 border-zinc-700 text-zinc-500'
            :                'bg-zinc-900 border-zinc-800 text-zinc-600';
  const word = st === true ? 'ON' : st === false ? 'OFF' : '?';
  return `<button id="knob-en:${name}" onclick="toggleKnobEnable('${name}')"
      title="${_esc(label)} section switch on the device — click to toggle"
      class="shrink-0 text-[10px] font-mono px-2 py-1 rounded-md border transition-all ${cls}">${_esc(label)} ${word}</button>`;
}

// A fader-strip card for a KNOB macro (the CONTROLS section)
function _knobStripHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  const midiLabel = getMidiTriggerLabel(m);
  const issues = macroTargetIssues(m);
  const v = _knobNormOf(m, step.operation);
  const dev = Number.isFinite(parseFloat(m.device_value)) ? parseFloat(m.device_value) : null;
  return `
<div id="card:${name}" class="card bg-zinc-900 border border-zinc-800 hover:border-zinc-700 p-4 rounded-2xl transition-colors duration-200">
    <div class="flex items-center gap-2 mb-1">
        ${_nameHTML(name)}
        <span class="warn-slot shrink-0">${_warnIconHTML(issues)}</span>
        ${midiLabel ? `<div class="text-[10px] font-mono bg-zinc-800 text-zinc-500 px-2 py-0.5 rounded-md shrink-0 border border-zinc-700/60">${midiLabel}</div>` : ''}
    </div>
    ${m.description ? `<p class="text-zinc-500 text-xs leading-snug mb-1">${_esc(m.description)}</p>` : ''}
    <p class="routing-label text-orange-400/80 text-[11px] font-medium tracking-wide">${_esc(m.routing_label || '—')}</p>
    <div class="health-line-slot">${_healthLineHTML(m.last_fire)}</div>
    <div class="flex gap-1 items-center flex-wrap mt-3 mb-2">
        ${_enableChipHTML(name, m, step)}
        ${_companionChipsHTML(name, m, step)}
    </div>
    ${_graphsEnabled() && _graphKindOf(param) ? `<div id="mgraph:${name}" class="mg-wrap"></div>` : ''}
    <div class="flex gap-2 items-center mb-1 min-w-0">
        <input id="knob:${name}" type="range" min="0" max="1" step="0.002" value="${v}"
            class="flex-1 min-w-0 accent-orange-500" title="Drag to set — MIDI moves it too · double-click resets to default"
            oninput="knobInput('${name}', this.value)" ondblclick="resetKnobToDefault('${name}')"
            onpointerdown="window._knobDrag='${name}'" onpointerup="window._knobDrag=null"
            onpointercancel="window._knobDrag=null" onlostpointercapture="window._knobDrag=null">
        <span id="knob-val:${name}" onclick="startKnobValEdit('${name}')" title="tap to type a value" class="text-xs text-zinc-300 font-mono w-12 text-center shrink-0 cursor-text">${fmtParamValue(param, _shapeKnob(v, step.operation))}</span>
    </div>
    <div id="knob-dev:${name}" class="text-[10px] text-zinc-600 font-mono mb-2">${dev != null ? 'device ' + fmtParamValue(param, dev) : ''}</div>
    <div id="knob-band:${name}" class="space-y-1 mb-2">${_companionSlidersHTML(name, m, step)}</div>
    <button onclick="toggleDetail('${name}')"
        class="w-full text-zinc-700 hover:text-zinc-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors tracking-widest">
        DETAILS <i id="detail-arrow:${name}" class="fas fa-chevron-down text-[9px] transition-transform duration-150"></i>
    </button>
    <div id="detail:${name}" class="hidden mt-3 p-3 bg-zinc-950/80 rounded-xl border border-zinc-800 text-xs"></div>
</div>`;
}

function _renderKnobSection(names) {
  const row = document.getElementById('knob-row');
  if (!row) return;
  const modul = document.documentElement.getAttribute('data-skin') === 'modul';
  row.innerHTML = names.length
    ? names.map(n => (modul ? _knobModuleHTML : _knobStripHTML)(n, macros[n], _knobStepOf(macros[n]))).join('')
    : `<div class="col-span-full text-xs text-zinc-600 italic">no knobs yet — <b>New Knob</b> binds a channel parameter to a MIDI control</div>`;
  // wire synchronously: innerHTML is parsed already, and rAF never fires
  // in a hidden tab (graphs would stay empty until the next visible render)
  if (modul && names.length) { names.forEach(n => _modulWire(n)); _wireKnobReorder(); _wireKnobResize(); }
  else if (names.length && _graphsEnabled()) names.forEach(n => _wireKnobGraph(n));
}

