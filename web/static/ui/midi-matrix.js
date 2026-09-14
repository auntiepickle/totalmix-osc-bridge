/* ui/midi-matrix.js — MIDI binding matrix overlay.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// == MIDI BINDING MATRIX (#user request) ======================================
// "an overlay view where i can see a matrix to reassign midi. and then a
//  group below it exposing literally every other thing we could possibly
//  bind to midi in the rme."
// One full-screen overlay: a 128-cell number grid per MIDI channel (CC and
// note bindings live in the same number space, distinguished by glyph), an
// unbound rail, and the complete bindable-parameter catalog generated from
// PARAM_DEFS x the device's channel inventory. All name-based, all through
// the existing macro CRUD - no new server surface.

window._mm = { open: false, ch: 1, armed: null, create: null, flash: null };

// Trigger families share a number space per kind: a CC 20 and a Note 20 on
// the same channel are DIFFERENT addresses and may coexist.
const _MM_FAM = { control_change: 'cc', control_change_14: 'cc',
                  program_change: 'cc', note_on: 'note', note_off: 'note' };

function _mmBindings() {
  const out = [];
  Object.entries(macros || {}).forEach(([name, m]) => {
    (m.midi_triggers || []).forEach((t, ti) => {
      const fam = _MM_FAM[t.type || 'control_change'];
      out.push({ name, ti, t, fam,
                 num: fam === 'note' ? (t.note ?? null) : (t.number ?? null),
                 knob: (m.steps || []).some(x => x.operation && x.operation.type === 'knob') });
    });
  });
  return out;
}

function _mmNextFreeCC(ch) {
  const used = new Set(_mmBindings()
    .filter(b => b.fam === 'cc' && b.t.channel === ch && b.num != null)
    .map(b => b.num));
  for (let n = 1; n < 120; n++) if (!used.has(n)) return n;  // 120-127 = channel mode
  return 119;
}

function _mmEnsureDom() {
  if (document.getElementById('midi-overlay')) return;
  const st = document.createElement('style');
  st.id = 'midi-overlay-style';
  st.textContent = `
#midi-overlay { position: fixed; inset: 0; z-index: 90; overflow-y: auto;
  background: var(--mo-bg, #0c0d0f); color: var(--mo-txt, #d7d4cd);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; padding: 18px 22px 40px; }
#midi-overlay .mmx-head { display: flex; align-items: baseline; gap: 14px; margin-bottom: 14px; }
#midi-overlay .mmx-title { font-size: 13px; letter-spacing: 0.3em; color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-sub { font-size: 10px; color: var(--mo-mut, #6d7076); }
#midi-overlay .mmx-x { margin-left: auto; cursor: pointer; font-size: 12px;
  color: var(--mo-mut, #6d7076); border: 1px solid var(--mo-edge, #26282c);
  padding: 4px 10px; border-radius: 2px; background: none; }
#midi-overlay .mmx-x:hover { color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-chs { display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap; }
#midi-overlay .mmx-ch { font-size: 9px; padding: 4px 8px; cursor: pointer;
  border: 1px solid var(--mo-edge, #26282c); border-radius: 2px;
  color: var(--mo-mut, #6d7076); background: var(--mo-well, #131418); position: relative; }
#midi-overlay .mmx-ch.on { color: var(--mo-ink, #ff4d00); border-color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-ch .dot { position: absolute; top: 2px; right: 2px; width: 4px; height: 4px;
  border-radius: 50%; background: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-grid { display: grid; grid-template-columns: repeat(16, minmax(0, 1fr));
  gap: 3px; margin-bottom: 6px; }
#midi-overlay .mmx-cell { min-height: 44px; border: 1px solid var(--mo-edge, #26282c);
  border-radius: 2px; background: var(--mo-well, #131418); padding: 3px 4px;
  cursor: pointer; overflow: hidden; position: relative; }
#midi-overlay .mmx-cell:hover { border-color: #4a4d52; }
#midi-overlay .mmx-cell .n { font-size: 8px; color: #4a4d52; }
#midi-overlay .mmx-cell .b { display: block; font-size: 9px; line-height: 1.25;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--mo-txt, #d7d4cd); }
#midi-overlay .mmx-cell .b.knob { color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-cell.bound { border-color: #3a3e43; }
#midi-overlay .mmx-cell.armed { border-color: var(--mo-ink, #ff4d00);
  box-shadow: 0 0 0 1px var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-cell.deny { animation: mmxdeny 0.3s; }
@keyframes mmxdeny { 50% { border-color: #e33; box-shadow: 0 0 0 1px #e33; } }
#midi-overlay .mmx-strip { display: flex; gap: 10px; align-items: center; min-height: 30px;
  font-size: 10px; color: var(--mo-mut, #6d7076); margin-bottom: 16px; flex-wrap: wrap; }
#midi-overlay .mmx-strip b { color: var(--mo-txt, #d7d4cd); font-weight: 600; }
#midi-overlay .mmx-act { cursor: pointer; border: 1px solid var(--mo-edge, #26282c);
  padding: 3px 8px; border-radius: 2px; background: none; font-size: 9px;
  color: var(--mo-mut, #6d7076); font-family: inherit; }
#midi-overlay .mmx-act:hover { color: var(--mo-ink, #ff4d00); border-color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-sec { font-size: 10px; letter-spacing: 0.2em; color: var(--mo-mut, #6d7076);
  border-bottom: 1px solid var(--mo-seam, #1c1e22); padding-bottom: 4px;
  margin: 18px 0 8px; }
#midi-overlay .mmx-chips { display: flex; flex-wrap: wrap; gap: 4px; }
#midi-overlay .mmx-chip { font-size: 9px; padding: 4px 8px; cursor: pointer;
  border: 1px solid var(--mo-edge, #26282c); border-radius: 2px;
  background: var(--mo-well, #131418); color: var(--mo-txt, #d7d4cd); }
#midi-overlay .mmx-chip:hover { border-color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-chip.sel { color: var(--mo-ink, #ff4d00); border-color: var(--mo-ink, #ff4d00); }
#midi-overlay .mmx-chip.unb { color: #8bb8d9; }
#midi-overlay .mmx-build { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  border: 1px solid var(--mo-edge, #26282c); border-radius: 2px;
  background: var(--mo-well, #131418); padding: 10px 12px; margin: 10px 0; font-size: 10px; }
#midi-overlay .mmx-build select, #midi-overlay .mmx-build input {
  background: var(--mo-bg, #0c0d0f); color: var(--mo-txt, #d7d4cd);
  border: 1px solid var(--mo-edge, #26282c); border-radius: 2px;
  font-family: inherit; font-size: 10px; padding: 3px 6px; max-width: 180px; }
#midi-overlay .mmx-go { cursor: pointer; border: 1px solid var(--mo-ink, #ff4d00);
  color: var(--mo-ink, #ff4d00); background: none; padding: 4px 12px;
  border-radius: 2px; font-size: 10px; font-family: inherit; letter-spacing: 0.15em; }
#midi-overlay .mmx-go:hover { background: var(--mo-ink, #ff4d00); color: #0c0d0f; }
`;
  document.head.appendChild(st);
  const el = document.createElement('div');
  el.id = 'midi-overlay';
  el.style.display = 'none';
  document.body.appendChild(el);
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && window._mm.open) closeMidiMatrix();
  });
}

window.openMidiMatrix = function () {
  _mmEnsureDom();
  window._mm.open = true;
  // land on the busiest channel
  const counts = {};
  _mmBindings().forEach(b => { counts[b.t.channel] = (counts[b.t.channel] || 0) + 1; });
  const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  if (best) window._mm.ch = parseInt(best[0], 10);
  document.getElementById('midi-overlay').style.display = 'block';
  _mmRender();
};
window.closeMidiMatrix = function () {
  window._mm.open = false;
  window._mm.armed = null;
  window._mm.create = null;
  const el = document.getElementById('midi-overlay');
  if (el) el.style.display = 'none';
};

// The complete bindable catalog: PARAM_DEFS grouped by section. Sends
// (volume) lead - the core live-mix move - then the strip, then the
// channel-detail modules, then the global FX section.
function _mmCatalog() {
  const g = (title, keys) => ({ title, keys: keys.filter(k => PARAM_DEFS[k]) });
  return [
    g('SENDS + STRIP', ['volume', 'pan', 'mute']),
    g('EQ', ['eq_enable', 'eq_type_1', 'eq_gain_1', 'eq_freq_1', 'eq_q_1',
             'eq_gain_2', 'eq_freq_2', 'eq_q_2',
             'eq_type_3', 'eq_gain_3', 'eq_freq_3', 'eq_q_3']),
    g('LOW CUT', ['lowcut_enable', 'lowcut_freq', 'lowcut_grade']),
    g('DYNAMICS', ['dyn_enable', 'dyn_gain', 'comp_thresh', 'comp_ratio',
                   'exp_thresh', 'exp_ratio', 'dyn_attack', 'dyn_release']),
    g('AUTO LEVEL', ['alev_enable', 'alev_headroom', 'alev_maxgain', 'alev_risetime']),
    g('INPUT STAGE', ['input_gain', 'input_gain_r', 'phase', 'phase_r']),
    g('REVERB (GLOBAL)', ['reverb_enable', 'reverb_time', 'reverb_volume',
                          'reverb_width', 'reverb_predelay']),
    g('ECHO (GLOBAL)', ['echo_enable', 'echo_time', 'echo_feedback',
                        'echo_volume', 'echo_width']),
  ];
}

function _mmParamLabel(k) {
  const d = PARAM_DEFS[k] || {};
  return d.label || { volume: 'Send Level', pan: 'Pan', mute: 'Mute' }[k] || k;
}

function _mmChannelOptions(sel) {
  const gps = window._pickerGroups;
  const opt = (v, l) => `<option value="${_esc(v)}"${v === sel ? ' selected' : ''}>${_esc(l)}</option>`;
  if (!gps) {
    return ((window._picker || {}).inputs || [])
      .map(c => opt(c.name, c.name)).join('');
  }
  const grp = (label, names, pfx) => names.length
    ? `<optgroup label="${label}">${names.map(n => opt(pfx + n, n)).join('')}</optgroup>` : '';
  return grp('Inputs - stereo', gps.inputs.stereo, '')
       + grp('Inputs - mono', gps.inputs.mono, '')
       + grp('Outputs (hardware)', [...gps.outputs.stereo, ...gps.outputs.mono], '__out3__');
}

function _mmSubmixOptions(sel) {
  return ((window._picker || {}).outputs || [])
    .map(o => `<option value="${_esc(o.name)}"${o.name === sel ? ' selected' : ''}>${_esc(o.name)}</option>`)
    .join('');
}

function _mmRender() {
  const el = document.getElementById('midi-overlay');
  if (!el || !window._mm.open) return;
  const mm = window._mm;
  const all = _mmBindings();
  const chsUsed = new Set(all.map(b => b.t.channel));
  const here = all.filter(b => b.t.channel === mm.ch && b.num != null);
  const byNum = {};
  here.forEach(b => (byNum[b.num] = byNum[b.num] || []).push(b));
  const numless = all.filter(b => b.t.channel === mm.ch && b.num == null);
  const unbound = Object.entries(macros || {})
    .filter(([, m]) => !(m.midi_triggers || []).length).map(([n]) => n);

  const chTabs = Array.from({ length: 16 }, (_, i) => i + 1).map(c =>
    `<button class="mmx-ch${c === mm.ch ? ' on' : ''}" onclick="_mmSetCh(${c})">CH ${c}${chsUsed.has(c) ? '<span class="dot"></span>' : ''}</button>`
  ).join('');

  let cells = '';
  for (let n = 0; n < 128; n++) {
    const bs = byNum[n] || [];
    const armedHere = bs.some(b => mm.armed && mm.armed.name === b.name && mm.armed.ti === b.ti);
    cells += `<div class="mmx-cell${bs.length ? ' bound' : ''}${armedHere ? ' armed' : ''}${mm.flash === n ? ' deny' : ''}"
        onclick="_mmCellClick(${n})" title="${bs.length ? _esc(bs.map(b => `${b.name} (${b.t.type} ch${b.t.channel})`).join(', ')) : `number ${n} - free on ch ${mm.ch}`}">
      <span class="n">${n}</span>
      ${bs.map(b => `<span class="b${b.knob ? ' knob' : ''}">${b.fam === 'note' ? '&#9834; ' : ''}${_esc(b.name)}</span>`).join('')}
    </div>`;
  }

  const armed = mm.armed;
  const armedB = armed && all.find(b => b.name === armed.name && b.ti === armed.ti);
  const strip = armedB
    ? `<span>ARMED: <b>${_esc(armedB.name)}</b> - ${armedB.t.type} ${armedB.num != null ? '#' + armedB.num : ''} ch${armedB.t.channel}. Click any free cell (any channel tab) to move it.</span>
       <button class="mmx-act" onclick="_mmUnbindArmed()">unbind</button>
       <button class="mmx-act" onclick="window._mm.armed=null;_mmRender()">cancel</button>`
    : armed && armed.bindNew
    ? `<span>BINDING: <b>${_esc(armed.bindNew)}</b> - click a free cell to give it a CC.</span>
       <button class="mmx-act" onclick="window._mm.armed=null;_mmRender()">cancel</button>`
    : `<span>Click a bound cell to arm it, then a free cell to move it. Orange = knob (CC value drives the parameter), pale = fire trigger, &#9834; = note.</span>`;

  const numlessHtml = numless.length
    ? `<div class="mmx-strip">channel-wide on ch ${mm.ch}: ${numless.map(b =>
        `<b>${_esc(b.name)}</b> (${b.t.type})`).join(', ')}</div>` : '';

  const unboundHtml = unbound.length
    ? `<div class="mmx-sec">UNBOUND MACROS - click one, then a free cell</div>
       <div class="mmx-chips">${unbound.map(n =>
         `<span class="mmx-chip unb${armed && armed.bindNew === n ? ' sel' : ''}" onclick="_mmArmNew('${_esc(n)}')">${_esc(n)}</span>`).join('')}</div>`
    : '';

  const cr = mm.create;
  const catalog = _mmCatalog().map(sec =>
    `<div class="mmx-sec">${sec.title}</div>
     <div class="mmx-chips">${sec.keys.map(k =>
       `<span class="mmx-chip${cr && cr.param === k ? ' sel' : ''}" onclick="_mmChip('${k}')">${_esc(_mmParamLabel(k))}</span>`).join('')}</div>`
  ).join('');

  let builder = '';
  if (cr) {
    const def = PARAM_DEFS[cr.param] || {};
    const needsCh = !def.global;
    const needsSub = !def.global && !def.channelDetail && cr.param !== 'mute';
    builder = `<div class="mmx-build">
      <b style="letter-spacing:0.15em">${_esc(_mmParamLabel(cr.param))}</b>
      ${needsCh ? `<span>ch</span><select id="mmx-b-ch" onchange="_mmBuildTouch()">${_mmChannelOptions(cr.channel)}</select>` : ''}
      ${needsSub ? `<span>&rarr;</span><select id="mmx-b-sub" onchange="_mmBuildTouch()">${_mmSubmixOptions(cr.submix)}</select>` : ''}
      <span>name</span><input id="mmx-b-name" value="${_esc(cr.name || '')}" size="18" maxlength="64">
      <span>CC</span><input id="mmx-b-cc" type="number" min="0" max="119" value="${cr.cc}" style="width:56px">
      <span>midi ch</span><input id="mmx-b-mch" type="number" min="1" max="16" value="${cr.mch}" style="width:44px">
      <button class="mmx-go" onclick="_mmCreate()">BIND</button>
      <button class="mmx-act" onclick="window._mm.create=null;_mmRender()">cancel</button>
    </div>`;
  }

  el.innerHTML = `
    <div class="mmx-head">
      <span class="mmx-title">MIDI MATRIX</span>
      <span class="mmx-sub">${all.length} binding${all.length === 1 ? '' : 's'} - number grid for one MIDI channel at a time</span>
      <button class="mmx-x" onclick="closeMidiMatrix()">ESC CLOSE</button>
    </div>
    <div class="mmx-chs">${chTabs}</div>
    <div class="mmx-grid">${cells}</div>
    <div class="mmx-strip">${strip}</div>
    ${numlessHtml}
    ${unboundHtml}
    <div class="mmx-sec" style="margin-top:26px; color: var(--mo-ink, #ff4d00)">BIND ANYTHING - the full RME parameter surface. Pick a parameter, aim it, take a CC.</div>
    ${builder}
    ${catalog}
  `;
  window._mm.flash = null;
}

window._mmSetCh = function (c) { window._mm.ch = c; _mmRender(); };

window._mmArmNew = function (name) {
  const mm = window._mm;
  mm.armed = (mm.armed && mm.armed.bindNew === name) ? null : { bindNew: name };
  _mmRender();
};

async function _mmSaveMacro(name) {
  try {
    await API.saveMacro(name, _cleanMacro(macros[name]));
    window._lastLocalSave = { name, ts: Date.now() };
  } catch (e) { console.warn('[MMX] save failed:', e.message); }
}

window._mmCellClick = async function (n) {
  const mm = window._mm;
  const all = _mmBindings();
  const here = all.filter(b => b.t.channel === mm.ch && b.num === n);
  if (mm.armed && mm.armed.bindNew) {           // bind an unbound macro here
    if (here.some(b => b.fam === 'cc')) { mm.flash = n; _mmRender(); return; }
    const name = mm.armed.bindNew;
    const m = macros[name];
    if (!m) { mm.armed = null; _mmRender(); return; }
    const knob = (m.steps || []).some(x => x.operation && x.operation.type === 'knob');
    m.midi_triggers = m.midi_triggers || [];
    m.midi_triggers.push({ type: 'control_change', number: n, channel: mm.ch,
                           ...(knob ? { use_value_as_param: true } : {}) });
    mm.armed = null;
    _mmRender(); renderCards();
    await _mmSaveMacro(name);
    return;
  }
  if (mm.armed) {                                // move the armed binding here
    const a = all.find(b => b.name === mm.armed.name && b.ti === mm.armed.ti);
    if (!a) { mm.armed = null; _mmRender(); return; }
    const clash = here.some(b => b.fam === a.fam
      && !(b.name === a.name && b.ti === a.ti));
    if (clash) { mm.flash = n; _mmRender(); return; }
    const trig = (macros[a.name].midi_triggers || [])[a.ti];
    if (trig) {
      if (a.fam === 'note') trig.note = n; else trig.number = n;
      trig.channel = mm.ch;
    }
    mm.armed = null;
    _mmRender(); renderCards();
    await _mmSaveMacro(a.name);
    return;
  }
  if (here.length) { mm.armed = { name: here[0].name, ti: here[0].ti }; _mmRender(); }
};

window._mmUnbindArmed = async function () {
  const mm = window._mm;
  if (!mm.armed) return;
  const { name, ti } = mm.armed;
  const m = macros[name];
  if (m && m.midi_triggers) m.midi_triggers.splice(ti, 1);
  mm.armed = null;
  _mmRender(); renderCards();
  await _mmSaveMacro(name);
};

window._mmChip = function (param) {
  const mm = window._mm;
  if (mm.create && mm.create.param === param) { mm.create = null; _mmRender(); return; }
  const firstIn = (((window._pickerGroups || {}).inputs || {}).stereo || [])[0]
    || ((((window._picker || {}).inputs || [])[0]) || {}).name || '';
  const firstOut = ((((window._picker || {}).outputs || [])[0]) || {}).name || '';
  mm.create = { param, channel: firstIn, submix: firstOut,
                cc: _mmNextFreeCC(mm.ch), mch: mm.ch,
                name: _mmSuggestName(param, firstIn) };
  _mmRender();
};

function _mmSuggestName(param, channel) {
  const def = PARAM_DEFS[param] || {};
  const base = (def.global ? param : `${channel}_${param}`)
    .toLowerCase().replace(/__out3__/g, '').replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '').slice(0, 60) || param;
  let name = base, i = 2;
  while (macros[name]) name = `${base}_${i++}`;
  return name;
}

window._mmBuildTouch = function () {
  // keep the suggested name tracking the aim until the user edits it
  const mm = window._mm;
  if (!mm.create) return;
  const chSel = document.getElementById('mmx-b-ch');
  const subSel = document.getElementById('mmx-b-sub');
  const nameIn = document.getElementById('mmx-b-name');
  const prevAuto = _mmSuggestName(mm.create.param, mm.create.channel);
  if (chSel) mm.create.channel = chSel.value;
  if (subSel) mm.create.submix = subSel.value;
  if (nameIn && (nameIn.value === prevAuto || !nameIn.value)) {
    mm.create.name = _mmSuggestName(mm.create.param, mm.create.channel);
    nameIn.value = mm.create.name;
  } else if (nameIn) mm.create.name = nameIn.value;
};

window._mmCreate = async function () {
  const mm = window._mm;
  const cr = mm.create;
  if (!cr) return;
  const def = PARAM_DEFS[cr.param] || {};
  const nameIn = document.getElementById('mmx-b-name');
  const ccIn = document.getElementById('mmx-b-cc');
  const mchIn = document.getElementById('mmx-b-mch');
  const name = (nameIn ? nameIn.value : cr.name).trim();
  const cc = Math.max(0, Math.min(119, parseInt(ccIn ? ccIn.value : cr.cc, 10) || 0));
  const mch = Math.max(1, Math.min(16, parseInt(mchIn ? mchIn.value : cr.mch, 10) || 1));
  if (!MACRO_NAME_RE.test(name)) { alert('Name: letters, digits, _ or - only.'); return; }
  if (macros[name]) { alert(`"${name}" already exists.`); return; }
  const clash = _mmBindings().some(b => b.fam === 'cc' && b.t.channel === mch && b.num === cc);
  if (clash && !confirm(`CC ${cc} on ch ${mch} is already bound. Bind anyway (both will move)?`)) return;

  const raw = document.getElementById('mmx-b-ch') ? document.getElementById('mmx-b-ch').value : cr.channel;
  const isOut = String(raw).startsWith('__out3__');
  const channel = isOut ? String(raw).slice(8) : String(raw);
  const submix = document.getElementById('mmx-b-sub') ? document.getElementById('mmx-b-sub').value : cr.submix;

  const step = { osc: '', value: '{{param}}',
                 operation: { type: 'knob', hold: true,
                              range: [0, cr.param === 'volume' ? FADER_UNITY : 1] } };
  if (def.global) {
    step.osc = def.addr; step.target = { param: cr.param };
  } else if (def.channelDetail) {
    step.osc = def.addr; step.target = { channel, param: cr.param };
    if (isOut) step.target.row = 3;
  } else if (cr.param === 'mute') {
    step.target = { channel, param: 'mute' };
  } else {
    step.target = { submix, channel };
    if (cr.param !== 'volume') step.target.param = cr.param;
  }
  macros[name] = {
    description: `${_mmParamLabel(cr.param)}${def.global ? '' : ` - ${channel}`} (bound from the matrix)`,
    steps: [step],
    midi_triggers: [{ type: 'control_change', number: cc, channel: mch,
                      use_value_as_param: true }],
  };
  mm.create = null;
  mm.ch = mch;
  _mmRender(); renderCards();
  await _mmSaveMacro(name);
};


