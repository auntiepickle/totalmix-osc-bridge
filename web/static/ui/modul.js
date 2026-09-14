/* ui/modul.js — MODUL instrument layout (docs/design-modul.md), sidechain duck, send groups, drag-to-reorder, step waveforms.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ═══ MODUL layout (docs/design-modul.md) ═══════════════════════════
// A knob renders as an instrument module: silkscreen title plate, LIVE
// filter-response graph (draggable cutoff), rotary knob, power switch +
// LED, companion mini-knobs. Same ids as the strip everywhere updaters
// look, so the data path is identical.
//
// PORTABILITY BOUNDARY: _modulModel / _modulToKnob below and
// ModulGraph.magDb are PURE — no DOM, no globals beyond the verified
// taper tables. They are the layer that ports to C on an embedded
// server; everything DOM-flavored stays in the shell functions.
const _MODUL_TAPER = { lowcut_freq: [20, 500], eq_freq_1: [20, 20000],
                       eq_freq_2: [20, 20000], eq_freq_3: [20, 20000] };

// Which display a knob's graph well gets (#user request: volume and
// gain knobs too). 'filter' = response curve; 'gain' = the band's bell/
// shelf with axes swapped (x = freq companion, y = the knob); 'level' =
// fader-law strip; 'pan' = center-anchored strip.
function _graphKindOf(param) {
  if (_MODUL_TAPER[param]) return 'filter';
  if (/^eq_gain_\d$/.test(param)) return 'gain';
  if (param === 'pan') return 'pan';
  if (param === 'volume') return 'level';
  return null;
}

// Graphs in EVERY theme (#user request), gear-menu toggle to hide
function _graphsEnabled() {
  try { return localStorage.getItem('uiGraphs') !== 'off'; } catch (_) { return true; }
}

// Knob position from macro state: knob_value IS knob-norm; device_value is
// param-norm and must be inverse-mapped through the knob's range (never
// re-shaped forward — that's the double-mapping bug).
function _knobNormOf(m, op) {
  const kv = parseFloat(m.knob_value);
  if (Number.isFinite(kv)) return Math.max(0, Math.min(1, kv));
  const dv = parseFloat(m.device_value);
  if (!Number.isFinite(dv)) return 0;
  const rng = Array.isArray((op || {}).range) ? op.range.map(Number) : [0, 1];
  const span = (rng[1] - rng[0]) || 1;
  return Math.max(0, Math.min(1, (dv - rng[0]) / span));
}

function _modulModel(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  const kind = _graphKindOf(param);
  if (kind === 'gain') {
    // the band's curve: freq/Q from companions, GAIN from this knob
    const comps = m.companions || {};
    const band = param.slice(-1);
    const fq = parseFloat(comps[`eq_freq_${band}`]);
    const qn = parseFloat(comps[`eq_q_${band}`]);
    const kn = _knobNormOf(m, step.operation);
    const gDb = _shapeKnob(kn, step.operation) * 40 - 20;
    const labels = ENUM_LABELS[`eq_type_${band}`] || [];
    const tv = parseFloat(comps[`eq_type_${band}`]);
    const lbl = labels.length && Number.isFinite(tv)
      ? labels[Math.round(tv * (labels.length - 1))] : 'Bell';   // band 2 = Bell
    const model = {
      f: 20 * Math.pow(1000, Number.isFinite(fq) ? fq : 0.5),
      gain: gDb,
      q: 0.4 + (Number.isFinite(qn) ? qn : 0.03) * 9.5,
      enabled: m.enable_value !== false,
    };
    if (lbl === 'Shelf')          model.kind = band === '1' ? 'shelf-lo' : 'shelf-hi';
    else if (lbl === 'Low Pass')  model.kind = 'lowpass-q';
    else if (lbl === 'High Pass') model.kind = 'highpass-q';
    else                          model.kind = 'bell';
    return model;
  }
  if (kind === 'level' || kind === 'pan') {
    const kn = _knobNormOf(m, step.operation);
    return { levelKind: kind, v: _shapeKnob(kn, step.operation),
             enabled: m.enable_value !== false };
  }
  const taper = _MODUL_TAPER[param];
  if (!taper) return null;                       // no display for this param
  // knob_value (knob-norm, shaped through the range) is the display
  // authority: it is the commanded state, updated by every write AND by
  // device-side changes (the handler inverse-maps those into knob-norm).
  // device_value is param-norm and only fresh after a readback — using it
  // first made the curve snap back to stale device state right after a
  // drag (#knob jump-back). It remains the fallback before any state.
  const dv = parseFloat(m.device_value), kv = parseFloat(m.knob_value);
  const t = Number.isFinite(kv) ? _shapeKnob(Math.max(0, Math.min(1, kv)), step.operation)
          : Number.isFinite(dv) ? dv : 0;
  const f = taper[0] * Math.pow(taper[1] / taper[0], Math.max(0, Math.min(1, t)));
  const comps = m.companions || {};
  const model = { f, enabled: m.enable_value !== false };
  if (param === 'lowcut_freq') {
    model.kind = 'highpass';
    model.order = Math.round((parseFloat(comps.lowcut_grade) || 0) * 3) + 1;
  } else {
    const band = param.slice(-1);
    const labels = ENUM_LABELS[`eq_type_${band}`] || [];
    const tv = parseFloat(comps[`eq_type_${band}`]);
    const lbl = Number.isFinite(tv) ? labels[Math.round(tv * (labels.length - 1))] : '';
    const qn = parseFloat(comps[`eq_q_${band}`]);
    const gn = parseFloat(comps[`eq_gain_${band}`]);
    model.q = 0.4 + (Number.isFinite(qn) ? qn : 0.03) * 9.5;
    model.gain = (Number.isFinite(gn) ? gn : 0.5) * 40 - 20;
    if (lbl === 'Low Pass')       model.kind = 'lowpass-q';
    else if (lbl === 'High Pass') model.kind = 'highpass-q';
    else if (lbl === 'Shelf')     model.kind = band === '1' ? 'shelf-lo' : 'shelf-hi';
    else if (lbl === 'Bell')      model.kind = 'bell';
    else                          model.kind = null;
  }
  return model;
}

function _modulToKnob(step, param) {
  const taper = _MODUL_TAPER[param];
  const op = step.operation || {};
  const rng = Array.isArray(op.range) ? op.range.map(Number) : [0, 1];
  return (f) => {
    const t = Math.log(f / taper[0]) / Math.log(taper[1] / taper[0]);
    const span = (rng[1] - rng[0]) || 1;
    return Math.max(0, Math.min(1, (t - rng[0]) / span));
  };
}

function _knobModuleHTML(name, m, step) {
  // RACK layout (user-chosen direction, 2026-08-25): every control is a
  // full-width 1U rack unit - identity flank | hero graph | control flank.
  // Every id the live updaters touch is unchanged.
  const param = (step.target && step.target.param) || 'volume';
  const midiLabel = getMidiTriggerLabel(m);
  const issues = macroTargetIssues(m);
  const shown = _displayName(name, m);
  const v = _knobNormOf(m, step.operation);
  const hasGraph = !!_graphKindOf(param);
  const en = m.enable_value;
  const rng = Array.isArray(step.operation && step.operation.range) ? step.operation.range.map(Number) : null;
  const rangeTxt = rng ? `${fmtParamValue(param, rng[0])} \u2013 ${fmtParamValue(param, rng[1] ?? 1)}` : '';
  const enumChips = (COMPANION_FOR[param] || []).filter(cp => ENUM_LABELS[cp]).map(cp => {
    const labels = ENUM_LABELS[cp];
    const cv = parseFloat((m.companions || {})[cp]);
    const idx = Number.isFinite(cv) ? Math.round(cv * (labels.length - 1)) : null;
    const pinned = (((step.operation || {}).companions) || {})[cp] != null;
    return `<select id="knob-cp:${name}-${cp}" onchange="setKnobParam('${name}','${cp}',this.selectedIndex - 1,${labels.length})"
        class="mplate${pinned ? ' pinned' : ''}" title="${_esc(cp.replace(/_/g, ' '))}${pinned ? ' (pinned: the knob re-asserts this choice)' : ''}">
        <option value="" disabled${idx == null ? ' selected' : ''}>\u2014</option>
        ${labels.map((l, i) => `<option value="${i}"${i === idx ? ' selected' : ''}>${_enumGlyph(cp, l)}${_esc(l)}</option>`).join('')}
      </select>`;
  }).join('');
  const minis = (COMPANION_FOR[param] || []).filter(cp => !ENUM_LABELS[cp]).map(cp => {
    const def = PARAM_DEFS[cp] || {};
    const cv = parseFloat((m.companions || {})[cp]);
    const short = (def.label || cp).replace(/^EQ Band \d /, '').replace(/^Low Cut /, '');
    return `<div class="mmini" title="${_esc(def.label || cp)} on the device \u2014 live">
      ${ModulKnob.html(name, { size: 'mini', suffix: cp, label: def.label || cp })}
      <span class="mmini-lbl">${_esc(short)}</span>
      <span id="knob-cpv:${name}-${cp}" class="mmini-val" onclick="startCompValEdit('${name}','${cp}')" title="tap to type a value">${Number.isFinite(cv) ? (def.fmt ? def.fmt(cv) : Math.round(cv * 100) + '%') : '\u2014'}</span>
    </div>`;
  }).join('');
  const hasEnable = !!ENABLE_FOR[param];
  const size = _unitHP(m);
  const gkind = _graphKindOf(param) || 'plain';
  const showGraph = hasGraph && size > 2;   // 2HP: one knob, one value
  return `
<div id="card:${name}" class="card mmodule" data-size="${size}" data-kind="${gkind}">
  <div class="mrk-head">
    <div class="mhead"><span class="mgrip" draggable="true" title="drag to reorder">\u28ff</span>${_nameHTML(name)}<span class="warn-slot">${_warnIconHTML(issues)}</span></div>
    <select class="msize" onchange="setUnitSize('${name}', this.value)" title="unit width - Eurorack HP sizes (or drag the module's right edge)">
      ${[2, 4, 6, 8, 12, 16, 24].map(h => `<option value="${h}"${h === size ? ' selected' : ''}>${h}HP</option>`).join('')}
    </select>
    <div class="msub"><span class="routing-label">${_esc(m.routing_label || '\u2014')}</span></div>
    ${enumChips ? `<div class="mrk-plates">${enumChips}</div>` : ''}
    ${hasEnable ? `<button id="knob-en:${name}" onclick="toggleKnobEnable('${name}')" class="mpower ${en === true ? 'on' : en === false ? 'off' : 'unk'}"
        title="section power"><span class="mled"></span><span class="mpower-txt">${en === true ? 'ON' : en === false ? 'OFF' : '\u2014'}</span></button>` : ''}
  </div>
  ${showGraph ? `<div id="mgraph:${name}" class="mg-wrap"></div>` : `<div class="mrk-nograph"></div>`}
  <div class="mrk-ctrl">
    <div class="mrk-knob">${ModulKnob.html(name, { label: shown })}</div>
    <span id="knob-val:${name}" class="mval" onclick="startKnobValEdit('${name}')" title="tap to type a value">${fmtParamValue(param, _shapeKnob(v, step.operation))}</span>
    ${rangeTxt ? `<span class="mrk-range">RANGE ${rangeTxt}</span>` : ''}
    <span id="knob-dev:${name}" class="mdev">${Number.isFinite(parseFloat(m.device_value)) ? 'device ' + fmtParamValue(param, parseFloat(m.device_value)) : ''}</span>
    ${midiLabel ? `<span class="mbadge">${midiLabel}</span>` : ''}
    <div class="mminis">${minis}</div>
    <button onclick="toggleDetail('${name}')" class="mdetails">
      DETAILS <i id="detail-arrow:${name}" class="fas fa-chevron-down text-[9px] transition-transform duration-150"></i>
    </button>
  </div>
  ${_dgBarHTML(name, m, step)}
  <div class="mrk-below">
    <div class="health-line-slot">${_healthLineHTML(m.last_fire)}</div>
    <div id="detail:${name}" class="hidden mt-2 p-3 bg-zinc-950/80 rounded-xl border border-zinc-800 text-xs"></div>
  </div>
  <div class="mresize" title="drag to resize"></div>
</div>`;
}

// == SIDECHAIN DUCK (#user idea: 'using the dynamics module build side
// chain compression') ========================================================
// TotalMix has no sidechain input; the bridge sees every channel's live
// meter, so a KEY channel ducks this knob's own send. The knob stays the
// send level - the duck rides UNDER it (engine restores on disable).
const _DUCK_FIELDS = [
  // [field, label, toReal(v01), toNorm(real), fmt]
  ['threshold', 'THR', v => -60 + v * 60, r => (r + 60) / 60, r => `${r.toFixed(0)}dB`],
  ['depth', 'DEPTH', v => v * 24, r => r / 24, r => `-${r.toFixed(0)}dB`],
  ['attack', 'ATK', v => Math.round(Math.pow(500, Math.max(v, 0.001))), r => Math.log(Math.max(1, r)) / Math.log(500), r => `${Math.round(r)}ms`],
  ['release', 'REL', v => Math.round(20 * Math.pow(100, v)), r => Math.log(Math.max(20, r) / 20) / Math.log(100), r => `${Math.round(r)}ms`],
];

function _duckCfg(m) {
  const step = _knobStepOf(m);
  return step && step.operation ? step.operation.duck : null;
}

function _dgBarHTML(name, m, step) {
  // ONE compact bar (#user: duck/group were two dead full-width rows each
  // wasting real estate). Idle = two tiny chips on a single line; only the
  // enabled feature expands its controls inline.
  const param = (step.target && step.target.param) || 'volume';
  if (param !== 'volume') return '';
  return `<div class="mdg">${_duckHTML(name, m, step)}${_groupHTML(name, m, step)}</div>`;
}

function _duckHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  if (param !== 'volume') return '';           // duck rides the fader law
  const duck = _duckCfg(m);
  const on = duck && duck.enabled;
  const chip = `<button id="duck-chip:${name}" onclick="toggleDuck('${name}')"
      class="mduck-chip${on ? ' on' : duck ? ' cfg' : ''}"
      title="sidechain duck - a key channel's level pulls this send down (goblin mode)">DUCK</button>`;
  if (!duck) return `<span class="mdg-sec">${chip}</span>`;
  const key = duck.key || {};
  const keyVal = `${key.row || 1}|${key.channel || ''}`;
  const g = window._pickerGroups;
  const opt = (v, l) => `<option value="${_esc(v)}"${v === keyVal ? ' selected' : ''}>${_esc(l)}</option>`;
  const grp = (label, names, row) => names && names.length
    ? `<optgroup label="${label}">${names.map(n => opt(`${row}|${n}`, n)).join('')}</optgroup>` : '';
  const keyOpts = g
    ? grp('Inputs', [...g.inputs.stereo, ...g.inputs.mono], 1)
      + grp('Playback', [...g.outputs.stereo, ...g.outputs.mono], 2)
      + grp('Outputs', [...g.outputs.stereo, ...g.outputs.mono], 3)
    : ((window._picker || {}).inputs || []).map(c => opt(`1|${c.name}`, c.name)).join('');
  const sliders = _DUCK_FIELDS.map(([f, lbl, toReal, toNorm, fmt]) => {
    const real = Number(duck[f] ?? { threshold: -30, depth: 12, attack: 20, release: 250 }[f]);
    return `<label class="mduck-p" title="${f}">
      <span class="mduck-lbl">${lbl}</span>
      <input type="range" min="0" max="1" step="any" value="${toNorm(real)}"
          oninput="_duckSlide('${name}','${f}',this.value)">
      <span id="duck-v:${name}-${f}" class="mduck-val">${fmt(real)}</span>
    </label>`;
  }).join('');
  return `<span class="mdg-sec on">
    ${chip}
    <span class="mduck-lbl">KEY</span>
    <select class="mduck-key" onchange="_duckKey('${name}', this.value)" title="the channel whose level does the ducking">${keyOpts}</select>
    ${sliders}
    <span id="duck-gr:${name}" class="mduck-gr" title="live gain reduction"></span>
  </span>`;
}

// == SEND GROUPS (#user request: 'move multiple faders at once as a
// group') ====================================================================
// VCA-style: this knob is the group master; every member send follows at
// its stored dB offset, so the balance you mixed survives every move.
// Deliberated vs native TotalMix fader groups first: those are 4 global
// toggles whose logic lives in the GUI fader layer - direct /mix writes
// bypass them, and one group programmed across submixes corrupts the
// others (RME forum). Bridge members are per-knob and unlimited.
function _groupOf(m) {
  const step = _knobStepOf(m);
  const g = step && step.operation ? step.operation.group : null;
  return Array.isArray(g) ? g : null;
}

function _groupHTML(name, m, step) {
  const param = (step.target && step.target.param) || 'volume';
  if (param !== 'volume') return '';
  const grp = _groupOf(m);
  const chip = `<button onclick="toggleSendGroup('${name}')"
      class="mduck-chip${grp && grp.length ? ' on' : grp ? ' cfg' : ''}"
      title="send group - this knob moves every member fader at its stored dB offset (VCA-style)">GRP${grp && grp.length ? ' ' + grp.length : ''}</button>`;
  if (!grp) return `<span class="mdg-sec">${chip}</span>`;
  const members = grp.map((mem, i) => {
    const off = Number(mem.offset_db || 0);
    const where = mem.row === 3 ? 'OUT' : mem.row === 2 ? 'PB' : (mem.submix || '');
    return `<span class="mgrp-mem" title="${_esc(mem.channel)}${where ? ' @ ' + _esc(where) : ''} - follows at ${off >= 0 ? '+' : ''}${off.toFixed(1)}dB">
      ${_esc(mem.channel)}${where ? '<i>' + _esc(where) + '</i>' : ''}
      <b>${off >= 0 ? '+' : ''}${off.toFixed(1)}</b>
      <a onclick="groupRemove('${name}',${i})" title="remove from group">&times;</a>
    </span>`;
  }).join('');
  const g = window._pickerGroups;
  const chOpts = g
    ? `<optgroup label="Inputs">${[...g.inputs.stereo, ...g.inputs.mono].map(n => `<option value="1|${_esc(n)}">${_esc(n)}</option>`).join('')}</optgroup>`
      + `<optgroup label="Playback">${[...g.outputs.stereo, ...g.outputs.mono].map(n => `<option value="2|${_esc(n)}">${_esc(n)}</option>`).join('')}</optgroup>`
      + `<optgroup label="Outputs">${[...g.outputs.stereo, ...g.outputs.mono].map(n => `<option value="3|${_esc(n)}">${_esc(n)}</option>`).join('')}</optgroup>`
    : ((window._picker || {}).inputs || []).map(c => `<option value="1|${_esc(c.name)}">${_esc(c.name)}</option>`).join('');
  const subOpts = ((window._picker || {}).outputs || [])
    .map(o => `<option value="${_esc(o.name)}">${_esc(o.name)}</option>`).join('');
  return `<span class="mdg-sec on mgrp">
    ${chip}
    ${members}
    <select id="grp-ch:${name}" class="mduck-key" title="member channel">${chOpts}</select>
    <select id="grp-sub:${name}" class="mduck-key" title="member submix (for input/playback sends)">${subOpts}</select>
    <button class="mgrp-act" onclick="groupAdd('${name}')" title="add this send to the group">+ ADD</button>
    <button class="mgrp-act" onclick="groupCapture('${name}')"
        title="capture the balance: each member's offset becomes its CURRENT level relative to this knob">CAPTURE</button>
  </span>`;
}

// (named apart from toggleGroup above, which collapses a WORKSPACE group in
// the macro grid — assigning this over it made that header button a no-op)
window.toggleSendGroup = function (name) {
  const m = macros[name];
  const step = m && _knobStepOf(m);
  if (!step || !step.operation) return;
  const grp = _groupOf(m);
  if (!grp) step.operation.group = [];
  else if (!grp.length) delete step.operation.group;
  else {
    if (!confirm(`Remove the ${grp.length}-member group from "${name}"?`)) return;
    delete step.operation.group;
  }
  renderCards();
  _duckSave(name);
};

window.groupAdd = function (name) {
  const grp = _groupOf(macros[name]);
  const chSel = document.getElementById(`grp-ch:${name}`);
  const subSel = document.getElementById(`grp-sub:${name}`);
  if (!grp || !chSel || !chSel.value) return;
  const bar = chSel.value.indexOf('|');
  const row = parseInt(chSel.value.slice(0, bar), 10) || 1;
  const channel = chSel.value.slice(bar + 1);
  const mem = { channel, offset_db: 0 };
  if (row === 3) mem.row = 3;
  else { mem.submix = subSel ? subSel.value : ''; if (row === 2) mem.row = 2; }
  // the knob's own target is already the master - refuse a duplicate
  const step = _knobStepOf(macros[name]);
  const t = step.target || {};
  const dup = grp.some(x => x.channel === mem.channel && x.submix === mem.submix && x.row === mem.row)
    || (t.channel === mem.channel && t.submix === mem.submix && (t.row || 1) === (mem.row || 1));
  if (dup) return;
  grp.push(mem);
  renderCards();
  _duckSave(name);
  // capture the live balance for the new member right away
  setTimeout(() => groupCapture(name, true), 600);
};

window.groupRemove = function (name, i) {
  const grp = _groupOf(macros[name]);
  if (!grp) return;
  grp.splice(i, 1);
  renderCards();
  _duckSave(name);
};

window.groupCapture = async function (name, quiet) {
  try {
    const r = await fetch(`/api/knobs/${encodeURIComponent(name)}/group_capture`,
                          { method: 'POST' }).then(x => x.json());
    if (r.status === 'ok') {
      const step = _knobStepOf(macros[name]);
      if (step && step.operation) step.operation.group = r.group;
      window._lastLocalSave = { name, ts: Date.now() };
      renderCards();
    } else if (!quiet) {
      alert(r.status === 'no_primary_state'
        ? 'Capture needs the knob\u2019s own send to be up (readback unknown or at -inf).'
        : 'No group to capture.');
    }
  } catch (e) { console.warn('[GRP] capture failed:', e.message); }
};

let _duckSaveTimers = {};
function _duckSave(name) {
  // claim the local edit IMMEDIATELY - a WS-triggered macros refresh
  // inside the debounce window would revert the mutation before it saves
  window._lastLocalSave = { name, ts: Date.now() };
  clearTimeout(_duckSaveTimers[name]);
  _duckSaveTimers[name] = setTimeout(async () => {
    try {
      await API.saveMacro(name, _cleanMacro(macros[name]));
      window._lastLocalSave = { name, ts: Date.now() };
    } catch (e) { console.warn('[DUCK] save failed:', e.message); }
  }, 350);
}

window.toggleDuck = function (name) {
  const m = macros[name];
  const step = m && _knobStepOf(m);
  if (!step || !step.operation) return;
  let duck = step.operation.duck;
  if (!duck) {
    const g = window._pickerGroups;
    const firstIn = g ? (g.inputs.stereo[0] || g.inputs.mono[0] || '')
      : ((((window._picker || {}).inputs || [])[0]) || {}).name || '';
    duck = step.operation.duck = { enabled: true, key: { row: 1, channel: firstIn },
                                   threshold: -30, depth: 12, attack: 20, release: 250 };
  } else duck.enabled = !duck.enabled;
  renderCards();
  _duckSave(name);
};

window._duckKey = function (name, v) {
  const duck = _duckCfg(macros[name]);
  if (!duck) return;
  const bar = v.indexOf('|');
  duck.key = { row: parseInt(v.slice(0, bar), 10) || 1, channel: v.slice(bar + 1) };
  _duckSave(name);
};

window._duckSlide = function (name, field, v01) {
  const duck = _duckCfg(macros[name]);
  const def = _DUCK_FIELDS.find(d => d[0] === field);
  if (!duck || !def) return;
  const real = def[2](parseFloat(v01));
  duck[field] = Math.round(real * 10) / 10;
  const el = document.getElementById(`duck-v:${name}-${field}`);
  if (el) el.textContent = def[4](duck[field]);
  _duckSave(name);
};

function _modulWire(name) {
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step) return;
  const param = (step.target && step.target.param) || 'volume';
  const getV = () => _knobNormOf(macros[name] || {}, step.operation);
  ModulKnob.set(name, getV());
  ModulKnob.wire(name, {
    get: getV,
    send: val => knobInput(name, val),
    reset: () => _knobDefaultNorm(name),   // double-tap -> the param's DEFAULT
  });
  (COMPANION_FOR[param] || []).filter(cp => !ENUM_LABELS[cp]).forEach(cp => {
    const getC = () => { const x = parseFloat(((macros[name] || {}).companions || {})[cp]); return Number.isFinite(x) ? x : 0.5; };
    ModulKnob.set(name, getC(), cp);
    ModulKnob.wire(name, { get: getC, send: val => companionInput(name, cp, val),
      reset: () => _compDefault(cp) }, cp);
  });
  _wireKnobGraph(name);
}

// Graph wiring, shared by the MODUL module and the strip layouts
function _wireKnobGraph(name) {
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step) return;
  const param = (step.target && step.target.param) || 'volume';
  const gEl = document.getElementById(`mgraph:${name}`);
  if (gEl && window.ModulGraph && window.uPlot) {
    // phones: a taller plot = a real finger lane for the cutoff drag;
    // the RACK layout gives the graph hero height on desktop too
    const mobile = window.matchMedia && matchMedia('(max-width: 480px)').matches;
    const rack = document.documentElement.getAttribute('data-skin') === 'modul';
    const gkind = _graphKindOf(param);
    // council spec extended to the full HP range: well height encodes
    // dimensionality per size; below 8HP the wells lose axis labels
    const HSIZE = { 4:  { filter: 48, gain: 48, level: 32, pan: 24 },
                    6:  { filter: 64, gain: 64, level: 40, pan: 28 },
                    8:  { filter: 84, gain: 84, level: 56, pan: 44 },
                    12: { filter: 112, gain: 112, level: 72, pan: 56 },
                    16: { filter: 112, gain: 112, level: 72, pan: 56 },
                    24: { filter: 180, gain: 180, level: 120, pan: 96 } };
    const hp = rack ? _unitHP(m) : null;
    const height = mobile ? (gkind === 'level' || gkind === 'pan' ? 56 : 132)
                 : hp ? ((HSIZE[hp] || HSIZE[8])[gkind] || 96)
                 : 96;
    const noLabels = !mobile && hp != null && hp < 8;
    if (hp === 2) return;   // 2HP has no well
    const band = param.slice(-1);
    const rng = Array.isArray(step.operation && step.operation.range) ? step.operation.range.map(Number) : [0, 1];
    const _cpWrite = cp => v => {
      companionInput(name, cp, v);   // refreshes the curve itself (instant)
      if (window.ModulKnob) ModulKnob.set(name, v, cp);
    };
    if (gkind === 'filter') {
      // the module's window = what its parameter can IMPACT, plus one
      // octave of shoulder for the flat side
      const taper = _MODUL_TAPER[param];
      const toHz = t => taper[0] * Math.pow(taper[1] / taper[0], Math.max(0, Math.min(1, t)));
      ModulGraph.filterInit(`f:${name}`, gEl, { name, toKnob: _modulToKnob(step, param), dragKey: name,
                                                frange: [taper[0], Math.min(20000, taper[1] * 2)],
                                                bounds: [toHz(rng[0]), toHz(rng[1])],
                                                height, noLabels,
                                                setQ: _cpWrite(`eq_q_${band}`),
                                                setGain: _cpWrite(`eq_gain_${band}`) });
    } else if (gkind === 'gain') {
      // the band's curve with axes swapped: horizontal = the FREQ
      // companion, vertical (bell/shelf gain) = THIS knob
      const knobFromGain = gDbNorm => _knobNormOf({ device_value: gDbNorm }, step.operation);
      ModulGraph.filterInit(`f:${name}`, gEl, { name, dragKey: name,
        frange: [20, 20000],
        height, noLabels,
        onFreq: f => {
          const t = Math.log(Math.max(20, Math.min(20000, f)) / 20) / Math.log(1000);
          _cpWrite(`eq_freq_${band}`)(Math.max(0, Math.min(1, t)));
        },
        setGain: v => {
          const kn = knobFromGain(v);
          knobInput(name, kn);
          if (window.ModulKnob) ModulKnob.set(name, kn);
        },
        setQ: _cpWrite(`eq_q_${band}`) });
    } else if (gkind === 'level' || gkind === 'pan') {
      const opts = { name, dragKey: name, height, noLabels,
        bounds: rng,
        onX: pv => {
          const kn = _knobNormOf({ device_value: pv }, step.operation);
          knobInput(name, kn);
          if (window.ModulKnob) ModulKnob.set(name, kn);
        } };
      if (gkind === 'level') {
        const marks = [-60, -30, -12, -6, 0, 6];
        opts.axis = { splits: marks.map(db => _faderLin(db)),
                      labels: marks.map(db => db === 0 ? '0dB' : String(db)) };
        opts.zero = FADER_UNITY;
        opts.fill = 'left';
      } else {
        opts.axis = { splits: [0, 0.25, 0.5, 0.75, 1],
                      labels: ['L100', 'L50', 'C', 'R50', 'R100'] };
        opts.zero = 0.5;
        opts.fill = 'center';
      }
      ModulGraph.levelInit(`f:${name}`, gEl, opts);
    }
    _updateKnobGraph(name, m, step, true);
  }
}

// ── Drag a module to reorder the rack (#user request) ─────────────────
// HTML5 DnD from the grip; the new knob order splices into the FULL
// macro key order (non-knob macros keep their positions) and persists
// via POST /api/config/macros-order. Pointer devices only for now.
function _wireKnobReorder() {
  const row = document.getElementById('knob-row');
  if (!row) return;
  let dragName = null;
  row.querySelectorAll('.mgrip').forEach(grip => {
    const card = grip.closest('.mmodule');
    grip.addEventListener('dragstart', ev => {
      dragName = card.id.replace('card:', '');
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', dragName);
      card.classList.add('mdragging');
    });
    grip.addEventListener('dragend', () => {
      dragName = null;
      row.querySelectorAll('.mdragging, .mdrop').forEach(el =>
        el.classList.remove('mdragging', 'mdrop'));
    });
  });
  row.querySelectorAll('.mmodule').forEach(card => {
    card.addEventListener('dragover', ev => {
      if (!dragName) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
      row.querySelectorAll('.mdrop').forEach(el => el.classList.remove('mdrop'));
      if (card.id !== `card-${dragName}`) card.classList.add('mdrop');
    });
    card.addEventListener('drop', async ev => {
      ev.preventDefault();
      const src = dragName; dragName = null;
      row.querySelectorAll('.mdragging, .mdrop').forEach(el =>
        el.classList.remove('mdragging', 'mdrop'));
      const dst = card.id.replace('card:', '');
      if (!src || src === dst) return;
      const knobs = [...row.querySelectorAll('.mmodule')].map(c => c.id.replace('card:', ''));
      knobs.splice(knobs.indexOf(src), 1);
      knobs.splice(knobs.indexOf(dst) + (ev.offsetY > card.offsetHeight / 2 ? 1 : 0), 0, src);
      await _persistKnobOrder(knobs);
    });
  });
}

// Static unit sizes, full Eurorack range (#user: adjustable down to
// 2HP): 2/4/6/8/12/16/24HP on a 12-column grid. Persisted on the macro
// as `size` (number; legacy 's'/'m'/'l' read as 8/12/24).
const HP_SIZES = [2, 4, 6, 8, 12, 16, 24];
function _unitHP(m) {
  const legacy = { s: 8, m: 12, l: 24 }[m.size];
  const hp = legacy || parseInt(m.size, 10);
  return HP_SIZES.includes(hp) ? hp : 8;
}
window.setUnitSize = async function (name, hp) {
  const m = macros[name];
  hp = parseInt(hp, 10);
  if (!m || !HP_SIZES.includes(hp) || _unitHP(m) === hp) return;
  m.size = hp;
  renderCards();   // graph wells re-init at the new size's height
  try {
    await API.saveMacro(name, _cleanMacro(m));
    window._lastLocalSave = { name, ts: Date.now() };
  } catch (e) { console.warn('[UI] size save failed:', e.message); }
};

// Drag the module's right edge to resize (#user request) - snaps to the
// static HP stops (12-col grid; span per HP below). Live preview via
// data-size (pure CSS reflow); commit persists + re-inits the wells.
const _HP_SPAN = { 2: 1, 4: 2, 6: 3, 8: 4, 12: 6, 16: 8, 24: 12 };
function _wireKnobResize() {
  const row = document.getElementById('knob-row');
  if (!row) return;
  row.querySelectorAll('.mresize').forEach(h => {
    const card = h.closest('.mmodule');
    const name = card.id.replace('card:', '');
    let startX = 0, startSpan = 0, colW = 0, preview = null;
    h.addEventListener('pointerdown', ev => {
      const hp = _unitHP(macros[name] || {});
      startX = ev.clientX;
      startSpan = _HP_SPAN[hp];
      colW = row.getBoundingClientRect().width / 12;
      preview = hp;
      try { h.setPointerCapture(ev.pointerId); } catch (_) {}
      ev.preventDefault();
    });
    h.addEventListener('pointermove', ev => {
      if (!preview) return;
      const want = Math.max(1, Math.min(12, Math.round(startSpan + (ev.clientX - startX) / colW)));
      // nearest available span at-or-below, else smallest
      let hp = 2;
      for (const [k, sp] of Object.entries(_HP_SPAN)) if (sp <= want) hp = parseInt(k, 10);
      if (hp !== preview) {
        preview = hp;
        card.dataset.size = hp;   // live CSS reflow
        const sel = card.querySelector('.msize');
        if (sel) sel.value = String(hp);
      }
    });
    const done = () => {
      if (preview != null) { const hp = preview; preview = null; setUnitSize(name, hp); }
    };
    h.addEventListener('pointerup', done);
    h.addEventListener('pointercancel', done);
  });
}

async function _persistKnobOrder(knobOrder) {
  // splice the reordered knobs back into the full macro key order
  const all = Object.keys(macros);
  const isKnob = n => knobOrder.includes(n);
  let ki = 0;
  const full = all.map(n => isKnob(n) ? knobOrder[ki++] : n);
  const reordered = {};
  full.forEach(n => { reordered[n] = macros[n]; });
  window.macros = macros = reordered;
  renderCards();
  try {
    const res = await fetch('/api/config/macros-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: full }),
    });
    if (!res.ok) throw new Error(await res.text());
  } catch (e) {
    console.warn('[UI] reorder failed:', e.message);
    loadMacros();   // re-sync to server truth
  }
}

// Route a model refresh to the right plot kind (filter curve vs level strip)
function _updateKnobGraph(name, m, step, instant) {
  const param = (step.target && step.target.param) || 'volume';
  const gkind = _graphKindOf(param);
  if (!gkind || !window.ModulGraph) return;
  const model = _modulModel(name, m, step);
  if (!model) return;
  if (model.levelKind) ModulGraph.levelUpdate(`f:${name}`, model);
  else ModulGraph.filterUpdate(`f:${name}`, model, instant ? { instant: true } : undefined);
}

// Instant curve refresh from LOCAL writes: knobInput and companionInput
// call this on every tick, so the curve tracks the finger directly in
// every layout (#user report: dragging was not smooth - the curve only
// followed server echoes, through the 170ms morph).
window.refreshKnobGraph = function (name) {
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step || !document.getElementById(`mgraph:${name}`)) return;
  _updateKnobGraph(name, m, step, true);
};

// Strip-layout curve follow (updateKnobCard calls this outside MODUL)
window._syncKnobGraph = function (name) {
  if (window._knobDrag === name) return;   // drag frames own the curve
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step || !document.getElementById(`mgraph:${name}`)) return;
  _updateKnobGraph(name, m, step, false);
};

// One sync entry point, called by updateKnobCard on every knob_update
// while MODUL is active: knob positions, curve morph, power switch,
// companion plates and readouts.
window._modulSync = function (name) {
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step || !document.getElementById(`mknob:${name}`)) return;
  const param = (step.target && step.target.param) || 'volume';
  const draggingThis = window._knobDrag === name;
  if (!draggingThis) ModulKnob.set(name, _knobNormOf(m, step.operation));
  (COMPANION_FOR[param] || []).forEach(cp => {
    if (draggingThis) return;   // drag frames own the module (#jump report)
    const cv = parseFloat((m.companions || {})[cp]);
    if (!Number.isFinite(cv)) return;
    if (ENUM_LABELS[cp]) {
      const plate = document.getElementById(`knob-cp:${name}-${cp}`);
      if (plate && plate.classList.contains('mplate') && document.activeElement !== plate) {
        const labels = ENUM_LABELS[cp];
        plate.selectedIndex = 1 + Math.round(cv * (labels.length - 1));
      }
    } else {
      if (window._knobDrag !== `${name}:${cp}`) ModulKnob.set(name, cv, cp);
      const lbl = document.getElementById(`knob-cpv:${name}-${cp}`);
      const def = PARAM_DEFS[cp] || {};
      if (lbl && lbl.classList.contains('mmini-val'))
        lbl.textContent = def.fmt ? def.fmt(cv) : Math.round(cv * 100) + '%';
    }
  });
  if (!draggingThis) _updateKnobGraph(name, m, step, false);
  const pw = document.getElementById(`knob-en:${name}`);
  if (pw && pw.classList.contains('mpower')) {
    pw.classList.toggle('on', m.enable_value === true);
    pw.classList.toggle('off', m.enable_value === false);
    pw.classList.toggle('unk', m.enable_value == null);
    const txt = pw.querySelector('.mpower-txt');
    if (txt) txt.textContent = m.enable_value === true ? 'ON' : m.enable_value === false ? 'OFF' : '\u2014';
  }
};

// Names: every macro has a machine-safe KEY (the mappings key - DOM ids,
// URLs, the MQTT topic totalmix/macro/<key>) and an optional free-text
// LABEL ("Lo Cut", any characters) shown everywhere in the UI. Double-click
// edits the label; the key never changes, so HA automations keep working.
function _displayName(key, m) {
  const l = m && typeof m.label === 'string' ? m.label.trim() : '';
  return l || key;
}

// Derive a key from a typed name: "Lo Cut!" -> "Lo_Cut"
function _slugKey(text) {
  return String(text).trim().replace(/[^A-Za-z0-9_\-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64);
}

function _nameHTML(name) {
  const shown = _displayName(name, macros[name]);
  return `<h3 id="name:${name}" ondblclick="startRename('${name}')" title="Double-click to rename${shown !== name ? ' (key: ' + _esc(name) + ')' : ''}"
      class="text-sm font-bold text-white truncate flex-1 font-mono tracking-tight cursor-text">${_esc(shown)}</h3>`;
}

window.startRename = function (name) {
  const h = document.getElementById(`name:${name}`);
  if (!h || h.dataset.editing) return;
  h.dataset.editing = '1';
  const input = document.createElement('input');
  input.value = _displayName(name, macros[name]);
  input.className = 'text-sm font-bold text-white font-mono tracking-tight bg-zinc-950 border border-orange-400 rounded-lg px-2 py-0.5 flex-1 min-w-0 focus:outline-none';
  input.setAttribute('maxlength', '64');
  input.title = 'any text · Enter to save · Esc to cancel';
  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    const label = input.value.trim();
    const current = _displayName(name, macros[name]);
    if (!commit || label === current) { input.replaceWith(h); delete h.dataset.editing; return; }
    if (!label) {
      input.classList.add('border-red-500'); done = false;
      input.title = 'Name cannot be empty'; return;
    }
    try {
      // label only - the key stays, so ids/URLs/MQTT topics are untouched
      const m = { ...(macros[name] || {}), label };
      if (label === name) delete m.label;        // back to the bare key
      await API.saveMacro(name, _cleanMacro(m));
      macros[name] = m;
      window._lastLocalSave = { name, ts: Date.now() };
      renderCards();
    } catch (e) {
      input.classList.add('border-red-500'); done = false;
      input.title = `Rename failed: ${e.message}`;
    }
  };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
  h.replaceWith(input);
  input.focus();
  input.select();
};

// ── MODUL waveforms: ramp/LFO steps render their shape on the card ─────
// (rule 5 — show the signal). Wired only under data-skin="modul"; the
// playhead rides the curve during a run (see animateProgress below).
function _modulWaveStepsOf(m, knobStep) {
  if (knobStep || !_graphsEnabled()) return [];
  return (m.steps || [])
    .map((s, i) => ({ s, i }))
    .filter(x => ['ramp', 'lfo'].includes(((x.s || {}).operation || {}).type))
    .slice(0, 2);   // cap: a card is a key, not a rack
}

// Value formatter for a wave step: real units when the param is known
// (Hz, dB, ...), honest percent otherwise (generic sends).
function _mwaveFmt(step) {
  // a TARGETED step without a param is a send/volume (the convention
  // everywhere): those now read in real dB via the fader law. Raw OSC
  // steps (no target) keep honest percent.
  let par = (step && step.target) ? (step.target.param || 'volume') : null;
  // legacy raw classic fader steps (/1/volumeN) are the fader law too
  if (!par && step && typeof step.osc === 'string' && step.osc.startsWith('/1/volume')) par = 'volume';
  const def = PARAM_DEFS[par];
  return def && def.fmt ? (v => def.fmt(v)) : (v => Math.round(v * 100) + '%');
}
// Idle text: the step's travel. One-way ramps park at the destination
// (arrow); triangles and LFOs come back (double arrow).
function _mwaveIdleText(step) {
  const op = (step || {}).operation || {};
  const rng = Array.isArray(op.range) ? op.range.map(Number) : [0, 1];
  const fmt = _mwaveFmt(step);
  const arrow = (op.type === 'ramp' && op.curve === 'linear') ? ' → ' : ' ⇄ ';
  return fmt(rng[0]) + arrow + fmt(rng[1] ?? 1);
}

function _modulWireWaves() {
  if (!_graphsEnabled() || !window.ModulGraph) return;
  const mobile = window.matchMedia && matchMedia('(max-width: 480px)').matches;
  Object.keys(macros).forEach(name => {
    _modulWaveStepsOf(macros[name], _knobStepOf(macros[name])).forEach(({ s, i }) => {
      const el = document.getElementById(`mwave:${name}-${i}`);
      if (el) ModulGraph.waveformInit(`w:${name}:${i}`, el, s.operation,
                                      { height: mobile ? 56 : 36 });
    });
  });
}

function createMacroCardHTML(name, m) {
  const knobStep    = _knobStepOf(m);
  const midiLabel   = getMidiTriggerLabel(m);
  const routingLabel = m.routing_label || '—';
  const issues = macroTargetIssues(m);
  return `
<div id="card:${name}" class="card bg-zinc-900 border border-zinc-800 hover:border-zinc-700 p-5 rounded-2xl transition-colors duration-200">
    <!-- Header: LED · name/desc · warn badge · MIDI badge -->
    <div class="flex items-center gap-3 mb-1">
        <span id="led-dot:${name}" class="w-3 h-3 rounded-full bg-zinc-700 transition-all duration-150 shrink-0"></span>
        ${_nameHTML(name)}
        <span class="warn-slot shrink-0">${_warnIconHTML(issues)}</span>
        ${midiLabel ? `<div class="text-[10px] font-mono bg-zinc-800 text-zinc-500 px-2 py-0.5 rounded-md shrink-0 border border-zinc-700/60">${midiLabel}</div>` : ''}
    </div>
    <!-- Description + routing label -->
    <div class="pl-6 mb-3">
        ${m.description ? `<p class="text-zinc-500 text-xs leading-snug mb-1">${_esc(m.description)}</p>` : ''}
        <p class="routing-label text-orange-400/80 text-[11px] font-medium tracking-wide">${_esc(routingLabel)}</p>
        <div class="health-line-slot">${_healthLineHTML(m.last_fire)}</div>
    </div>
    ${_modulWaveStepsOf(m, knobStep).map(({ s, i }) =>
      `<div id="mwave:${name}-${i}" class="mwave"><span id="mwaveval:${name}-${i}" class="mwave-val">${_mwaveIdleText(s)}</span></div>`).join('')}
    <!-- Progress bar -->
    <div class="h-1 bg-zinc-800 rounded-full overflow-hidden mb-3">
      <div id="progress-bar:${name}" class="h-full bg-gradient-to-r from-amber-400 to-orange-500 transition-none" style="width:0%;"></div>
    </div>
    <!-- Action buttons -->
    <!-- (the grid wrapper stays: the skins style .grid-cols-3 > button as the
         performance key; the old RAMP key did exactly what FIRE does, #42) -->
    <div class="grid grid-cols-3 gap-2">
        <button onclick="fireMacro('${name}')"
            class="fire-btn col-span-3 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-zinc-500 active:scale-95 active:bg-zinc-600 text-zinc-400 hover:text-white font-medium py-2.5 rounded-xl text-xs tracking-widest transition-all">
            FIRE
        </button>
    </div>
    <!-- Details toggle -->
    <button onclick="toggleDetail('${name}')"
        class="mt-3 w-full text-zinc-700 hover:text-zinc-400 text-[10px] font-medium flex items-center justify-center gap-1 transition-colors tracking-widest">
        DETAILS <i id="detail-arrow:${name}" class="fas fa-chevron-down text-[9px] transition-transform duration-150"></i>
    </button>
    <div id="detail:${name}" class="hidden mt-3 p-3 bg-zinc-950/80 rounded-xl border border-zinc-800 text-xs"></div>
</div>`;
}

