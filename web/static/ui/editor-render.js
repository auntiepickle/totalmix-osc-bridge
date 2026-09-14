/* ui/editor-render.js — step + trigger management, channel identify, description staleness, advanced/simple editor render, save/cancel, delete, New Macro flow (MACRO_NAME_RE), BPM clock toggle.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

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
  // The step takes the routing picker's CURRENT selections (#24: name-only).
  const submixSel = document.getElementById(`routing-submix:${name}`);
  const sendSel   = document.getElementById(`routing-send:${name}`);
  const paramSel  = document.getElementById(`routing-param:${name}`);
  const raw = sendSel ? sendSel.value : '';
  const param = paramSel ? paramSel.value : 'volume';
  const def = PARAM_DEFS[param] || {};
  if (!raw || !submixSel || !submixSel.value) {
    // no picker inventory — raw fallback is all we can offer
    m.steps.push({ osc: '', value: kind === 'operation' ? '{{param}}' : '1.0',
                   ...(kind === 'operation' ? { operation: { type: 'ramp', bars: 2, bpm: 140, curve: 'triangle' } } : {}) });
    editDetail(name);
    return;
  }
  const isOut = raw.startsWith('__out3__');
  const isPb  = raw.startsWith('__pb__');
  const target = { channel: isOut ? raw.slice(8) : isPb ? raw.slice(6) : raw };
  if (param !== 'volume') target.param = param;
  if (param !== 'mute' && !def.channelDetail) target.submix = submixSel.value;
  if (isPb) target.row = 2;
  if (isOut) target.row = 3;
  if (kind === 'operation') {
    const op = { type: 'ramp', bars: 2, bpm: 140, curve: 'triangle' };
    if (def.mod?.threshold) op.threshold = 0.5;
    if (def.mod?.range) op.range = [def.min ?? 0, def.max ?? 1];
    m.steps.push({ osc: def.channelDetail ? def.addr : '', target,
                   value: '{{param}}', operation: op });
  } else {
    m.steps.push({ osc: def.channelDetail ? def.addr : '', target,
                   value: String(def.default ?? 1.0) });
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
  const btn = document.getElementById(`learn-btn:${name}-${i}`);
  if (window._midiLearn) {          // second click cancels
    window._midiLearn = null;
    if (btn) btn.textContent = 'learn';
    return;
  }
  if (btn) btn.textContent = 'waiting…';
  window._midiLearn = (captured) => {
    const m = _harvestEditor(name);
    if (!m) return;   // editor closed while armed — nothing to fill
    const trig = (m.midi_triggers || [])[i];
    if (trig) {
      // #23: generic capture — CC/CC14/PC carry number, notes carry note,
      // bend/aftertouch carry neither
      trig.type = captured.type;
      trig.channel = captured.channel;
      delete trig.number;
      delete trig.note;
      if ('number' in captured) trig.number = captured.number;
      if ('note' in captured) trig.note = captured.note;
    }
    editDetail(name);
  };
};

// ── Channel identify (#8) ───────────────────────────────────────────────────
// Wiggle-to-learn (world → screen): arm, then move any fader or click any
// control in TotalMix — the changed channel fills the routing picker. Same
// mental model as MIDI learn, but the "controller" is the mixer itself.
// The activity feed only contains value CHANGES from device-originated
// messages (own bridge writes never echo), so audio passing through a
// channel can't trigger it — only deliberate moves do.
window._wiggleLearn = null;

window.learnChannel = async function (name) {
  const btn = document.getElementById(`wiggle-btn:${name}`);
  if (window._wiggleLearn) {              // second click cancels
    clearInterval(window._wiggleLearn.timer);
    window._wiggleLearn = null;
    if (btn) btn.textContent = 'wiggle';
    return;
  }
  let since;
  try { since = (await API.getDeviceActivity(0)).now; }
  catch (e) {
    if (btn) { btn.textContent = 'no feed'; btn.title = String(e.message || e);
               setTimeout(() => { btn.textContent = 'wiggle'; }, 2000); }
    return;
  }
  if (btn) btn.textContent = 'move a fader…';
  // 30s: enough to arm here and walk to the rack before wiggling
  const state = { name, deadline: Date.now() + 30000 };
  state.timer = setInterval(async () => {
    if (Date.now() > state.deadline) {
      clearInterval(state.timer); window._wiggleLearn = null;
      const b = document.getElementById(`wiggle-btn:${name}`);
      if (b) b.textContent = 'wiggle';
      return;
    }
    let data;
    try { data = await API.getDeviceActivity(since); } catch (e) { return; }
    // ≥3 changes = a deliberate wiggle; a single stray click doesn't count
    const hit = (data.channels || []).find(c => c.count >= 3 && c.name);
    if (!hit) return;
    clearInterval(state.timer); window._wiggleLearn = null;
    _applyWiggle(name, hit);
  }, 500);
  window._wiggleLearn = state;
};

function _applyWiggle(name, hit) {
  const sendSel   = document.getElementById(`routing-send:${name}`);
  const submixSel = document.getElementById(`routing-submix:${name}`);
  const paramSel  = document.getElementById(`routing-param:${name}`);
  if (!sendSel) return;
  const def = PARAM_DEFS[paramSel ? paramSel.value : ''] || {};
  if (hit.row_key === 'outputs') {
    if (def.channelDetail) sendSel.value = `__out3__${hit.name}`;
    else if (submixSel && !submixSel.disabled) {
      submixSel.value = hit.name;
      updateSendPickerOptions(name, sendSel.value);
    }
  } else if (hit.row_key === 'playbacks') {
    sendSel.value = `__pb__${hit.name}`;
  } else {
    sendSel.value = hit.name;
  }
  // a value that matched no <option> leaves the select empty — applyRouting
  // no-ops safely in that case and the button just resets
  applyRouting(name);
}

// Pulse (screen → world): blip the selected send so you can hear/see which
// physical channel the picker is pointing at. The bridge restores the exact
// prior level; it refuses when the current level is unknowable.
window.pulseRouting = async function (name) {
  const btn = document.getElementById(`pulse-btn:${name}`);
  const sendSel   = document.getElementById(`routing-send:${name}`);
  const submixSel = document.getElementById(`routing-submix:${name}`);
  const paramSel  = document.getElementById(`routing-param:${name}`);
  if (!sendSel || !sendSel.value) return;
  const def = PARAM_DEFS[paramSel ? paramSel.value : ''] || {};
  if (def.global) return;      // global FX — nothing channel-shaped to blip
  const raw = sendSel.value;
  const body = raw.startsWith('__out3__') ? { channel: raw.slice(8), row: 3 }
    : raw.startsWith('__pb__') ? { channel: raw.slice(6), row: 2,
                                   submix: submixSel ? submixSel.value : '' }
    : { channel: raw, row: 1, submix: submixSel ? submixSel.value : '' };
  if (btn) btn.textContent = '…';
  try {
    await API.pulseChannel(body);
    if (btn) btn.textContent = '♪';
  } catch (e) {
    if (btn) { btn.textContent = '!'; btn.title = String(e.message || e); }
  }
  setTimeout(() => { const b = document.getElementById(`pulse-btn:${name}`);
                     if (b) b.textContent = 'pulse'; }, 1500);
};

// ── Description staleness (#22) ─────────────────────────────────────────────
// Re-pointing a macro's routing leaves the freeform description describing
// the OLD routing (seen live on an12_lfo_test). Rule: NEVER silently
// rewrite the user's text — nudge with a hint + one-click rewrite instead.
window._descStale = window._descStale || {};

function _describeRouting(m) {
  const step = (m.steps || []).find(s => s.target);
  if (!step) return '';
  const t = step.target || {};
  const p = t.param || 'volume';
  const def = PARAM_DEFS[p] || {};
  const mode = step.operation
    ? (step.operation.type === 'lfo' ? 'LFO' : step.operation.type === 'knob' ? 'knob' : 'ramp') : 'set';
  if (def.global) return `${def.label || p} ${mode}`;
  const rowTag = t.row === 2 ? ' (playback)' : t.row === 3 ? ' (output)' : '';
  const dest = t.submix ? ` → ${t.submix}` : '';
  return `${t.channel || '?'}${rowTag}${dest} ${(def.label || p).toLowerCase()} ${mode}`;
}

// Called by applyRouting after a retarget: hint only when there IS a
// description and the target genuinely changed
function _noteRetarget(name, beforeJson, afterTarget, description) {
  if (description && beforeJson !== JSON.stringify(afterTarget || null)) {
    window._descStale[name] = true;
  }
}

window.rewriteDescription = function (name) {
  const m = _harvestEditor(name);
  m.description = _describeRouting(m) || m.description;
  delete window._descStale[name];
  editDetail(name);
};

// Description input row shared by both editors: rewrite button always one
// click away; amber stale hint appears after a retarget
function _descriptionRow(name, m, ic) {
  const hint = window._descStale[name]
    ? `<div class="text-[10px] text-amber-400 flex items-center gap-2">
         <i class="fas fa-triangle-exclamation text-[9px]"></i>
         routing changed — description may be stale
       </div>` : '';
  return `<div class="flex gap-2 items-center">
      <input data-field="description" value="${_esc(m.description)}"
          class="${ic}" placeholder="Description">
      <button onclick="rewriteDescription('${name}')"
          title="Rewrite the description from the current routing (${_esc(_describeRouting(m) || 'no routed step')})"
          class="shrink-0 text-xs px-2 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-400 hover:text-white transition-all"><i class="fas fa-rotate"></i></button>
    </div>${hint}`;
}

// The two identify buttons, shared by both editors' picker rows
function _identifyButtons(name) {
  const cls = 'shrink-0 text-xs px-2 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-cyan-400 hover:text-white transition-all';
  return `<button id="wiggle-btn:${name}" onclick="learnChannel('${name}')"
      title="Learn from the device: arm, then wiggle a fader (or click any control) in TotalMix — the moved channel fills this picker"
      class="${cls}">wiggle</button>
    <button id="pulse-btn:${name}" onclick="pulseRouting('${name}')"
      title="Blip the selected send briefly so you can hear/see which physical channel it is (level is restored exactly)"
      class="${cls}">pulse</button>`;
}

window.addEditorTrigger = function (name) {
  const m = _harvestEditor(name);
  m.midi_triggers = m.midi_triggers || [];
  m.midi_triggers.push({ type: 'control_change', number: 0, channel: 1,
                         use_value_as_param: true });
  editDetail(name);
};

// #23: switching trigger type moves the identifier to the right field and
// re-renders the row (bend/aftertouch have no number at all)
window.changeTriggerType = function (name, i, newType) {
  const m = _harvestEditor(name);
  const trig = (m.midi_triggers || [])[i];
  if (!trig) return;
  trig.type = newType;
  const num = trig.number ?? trig.note ?? 0;
  delete trig.number;
  delete trig.note;
  if (newType === 'note_on' || newType === 'note_off') {
    trig.note = num;
  } else if (newType !== 'pitch_bend' && newType !== 'aftertouch') {
    trig.number = Math.min(num, newType === 'control_change_14' ? 31 : 127);
  }
  editDetail(name);
};

window.removeEditorTrigger = function (name, i) {
  const m = _harvestEditor(name);
  (m.midi_triggers || []).splice(i, 1);
  editDetail(name);
};

// Shared editor input classes (advanced editor, simple editor, and helpers)
const EDIT_IC = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-full';
const EDIT_SC = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none';
const EDIT_NC = 'bg-zinc-900 border border-zinc-700 focus:border-orange-400 rounded-lg px-2.5 py-1.5 text-sm text-white focus:outline-none w-20 text-center';

// Per-macro editor mode ('simple' | 'advanced'), decided on open: simple when
// the macro matches the canonical patch shape, advanced otherwise (#9)
window._editorModes = window._editorModes || {};

window.setEditorMode = function (name, mode) {
  _harvestEditor(name);
  window._editorModes[name] = mode;
  editDetail(name);
};

// Simple | Advanced toggle shown at the top of both editors
function _editorModeToggle(name, active) {
  const simpleable = !!simpleModeShape(window._editBuffers[name] || macros[name]);
  const btn = (mode, label, on, enabled, title) => enabled
    ? `<button onclick="setEditorMode('${name}','${mode}')" title="${title}"
        class="text-xs px-3 py-1 rounded-lg transition-all ${on
          ? 'bg-orange-500 text-black font-bold'
          : 'bg-zinc-800 border border-zinc-700 text-zinc-400 hover:text-white'}">${label}</button>`
    : `<button disabled title="${title}"
        class="text-xs px-3 py-1 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-600">${label}</button>`;
  return `<div class="flex gap-2 items-center">
    ${btn('simple', 'Simple', active === 'simple', simpleable,
          simpleable ? 'One routing, one behavior, one trigger'
                     : 'Advanced macro — multiple steps, raw OSC or several triggers cannot be shown in simple mode')}
    ${btn('advanced', 'Advanced', active === 'advanced', true,
          'Full step list, raw OSC, multiple triggers')}
  </div>`;
}

// Bars / BPM / clock row for an operation step — shared by both editors
function _timingControls(name, i, op) {
  const nc = EDIT_NC;
  return `<div class="flex gap-2 items-center">
    <input data-field="steps.${i}.operation.bars" type="number" min="1" value="${_esc(op.bars??2)}" class="${nc}">
    <span class="text-zinc-500 text-xs shrink-0">bars @</span>
    <input data-field="steps.${i}.operation.bpm" id="bpm-input:${name}-${i}"
        type="${op.bpm==='clock'?'text':'number'}" min="20" max="400"
        value="${op.bpm==='clock'?'clock':_esc(op.bpm??140)}"
        class="${nc}" ${op.bpm==='clock'?'disabled':''}>
    <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none shrink-0"
        title="Sync to live MIDI clock tempo">
      <input type="checkbox" id="bpm-clock-cb:${name}-${i}" class="w-3 h-3 accent-orange-500"
          ${op.bpm==='clock'?'checked':''}
          onchange="toggleBPMClock('${name}',${i})">
      clock
    </label>
  </div>`;
}

// One MIDI trigger row — shared by both editors. 'use value' feeds the CC
// value / velocity into {{param}} (was always saved true but never editable).
function _triggerRow(name, t, i) {
  const sc = EDIT_SC, nc = EDIT_NC;
  const type    = t.type || 'control_change';
  const isNote  = type === 'note_on' || type === 'note_off';
  const hasNum  = type !== 'pitch_bend' && type !== 'aftertouch';
  const numField = isNote ? `midi_triggers.${i}.note`   : `midi_triggers.${i}.number`;
  const numValue = isNote ? (t.note ?? 0)               : (t.number ?? 0);
  const numMax   = type === 'control_change_14' ? 31 : 127;  // MSB CCs only
  const numHtml = hasNum
    ? `<span class="text-zinc-500 text-xs shrink-0">#</span>
       <input data-field="${numField}" type="number" min="0" max="${numMax}" value="${_esc(numValue)}" class="${nc}"
           title="${type === 'control_change_14' ? 'Coarse (MSB) CC number 0-31 — the fine CC is +32 automatically' : type === 'program_change' ? 'Program number 0-127' : ''}">`
    : '';
  return `<div class="flex gap-2 items-center flex-wrap bg-zinc-900/80 border border-zinc-800 px-2.5 py-2 rounded-xl">
    <select data-field="midi_triggers.${i}.type" class="${sc} shrink-0"
        onchange="changeTriggerType('${name}', ${i}, this.value)">
      <option value="control_change"${type==='control_change'?' selected':''}>CC</option>
      <option value="control_change_14"${type==='control_change_14'?' selected':''}>CC 14-bit</option>
      <option value="note_on"${type==='note_on'?' selected':''}>Note On</option>
      <option value="note_off"${type==='note_off'?' selected':''}>Note Off</option>
      <option value="program_change"${type==='program_change'?' selected':''}>Prog Change</option>
      <option value="pitch_bend"${type==='pitch_bend'?' selected':''}>Pitch Bend</option>
      <option value="aftertouch"${type==='aftertouch'?' selected':''}>Aftertouch</option>
    </select>
    ${numHtml}
    <span class="text-zinc-500 text-xs shrink-0">ch</span>
    <input data-field="midi_triggers.${i}.channel" type="number" min="1" max="16" value="${_esc(t.channel)}" class="${nc}">
    <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none shrink-0"
        title="Use the CC value (or note velocity) as the macro's parameter at fire time">
      <input type="checkbox" data-field="midi_triggers.${i}.use_value_as_param"
          class="w-3 h-3 accent-orange-500"${t.use_value_as_param ? ' checked' : ''}>
      use value
    </label>
    <button id="learn-btn:${name}-${i}" onclick="learnTrigger('${name}',${i})"
        title="Capture the next CC or note from any device (or the MIDI emulator)"
        class="shrink-0 text-xs px-2 py-1 rounded-lg bg-zinc-800 border border-zinc-700 text-cyan-400 hover:text-white transition-all">learn</button>
    <button onclick="removeEditorTrigger('${name}',${i})" title="Remove trigger"
        class="shrink-0 w-8 text-zinc-600 hover:text-red-400 transition-colors"><i class="fas fa-xmark"></i></button>
  </div>`;
}

// Save / Cancel / Delete footer — shared by both editors
function _editorFooter(name) {
  return `<div class="flex gap-2 pt-2 border-t border-zinc-800">
    <button id="edit-save:${name}" onclick="saveInlineEdit('${name}')"
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
  </div>`;
}

function editDetail(name) {
  // #31: the editor lives in the drawer. A refused switch (another macro's
  // unsaved edits kept open) must abort, not render into the card's own node.
  if (window._drawerOpenFor !== name && macros[name] && !_openDetailFor(name)) return;
  const panel = document.getElementById(`detail:${name}`);
  const arrow = document.getElementById(`detail-arrow:${name}`);
  if (!panel || !macros[name]) return;
  if (!window._editBuffers[name]) {
    window._editBuffers[name] = JSON.parse(JSON.stringify(macros[name]));
  }
  const m = window._editBuffers[name];

  panel.classList.remove('hidden');
  if (arrow) arrow.style.transform = 'rotate(180deg)';

  // Pick the editor: simple when representable (and not overridden), else
  // advanced. A macro that stops being representable falls back safely.
  const simpleable = !!simpleModeShape(m);
  let mode = window._editorModes[name];
  if (mode !== 'simple' && mode !== 'advanced') mode = simpleable ? 'simple' : 'advanced';
  if (mode === 'simple' && !simpleable) mode = 'advanced';
  window._editorModes[name] = mode;

  if (mode === 'simple') _renderSimpleEditor(name, m, panel);
  else _renderAdvancedEditor(name, m, panel);
}

function _renderAdvancedEditor(name, m, panel) {
  // Shared input CSS classes
  const ic = EDIT_IC, sc = EDIT_SC, nc = EDIT_NC;

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
        <option value="knob"${mode==='knob'?' selected':''}>KNOB</option>
      </select>`;
    if (step.operation) {
      const op = step.operation;
      return `<div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
        ${targetHtml}
        <div class="flex gap-2 items-center">
          ${addrField}
          ${modeSel(_opMode(op))}
          ${_removeBtn(i)}
        </div>
        ${op.type === 'knob' ? '' : _timingControls(name, i, op)}
        ${_opExtraControls(name, i, op, sc)}
        ${_opModeDesc(_opMode(op))}
        ${op.type === 'knob' ? _knobControls(name, i, step, sc) : (step.target ? _modControls(name, i, step) : '')}
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
  const midiHtml = (m.midi_triggers || []).map((t, i) => _triggerRow(name, t, i)).join('');

  // Routing picker — restored to the macro's current routing, only when a
  // discovered channel map is loaded
  const hasChannelMap = ((window._picker || {}).outputs || []).length > 0;
  const routing = _currentRouting(m);
  const routingPickerHtml = hasChannelMap ? `<div>
    <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">What — Routing (from your device)</div>
    <div class="flex gap-2 items-center flex-wrap">
      <div class="flex-1 min-w-[120px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Input channel</div>
        <select id="routing-send:${name}" class="${sc} w-full"></select>
      </div>
      <span class="text-zinc-500 text-xs shrink-0">→</span>
      <div class="flex-1 min-w-[160px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Output submix</div>
        <select id="routing-submix:${name}" onchange="updateSendPickerOptions('${name}')" class="${sc} w-full">
          ${_buildSubmixPickerOptions(routing.submix)}
        </select>
      </div>
      <div class="shrink-0">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Parameter</div>
        <select id="routing-param:${name}" onchange="updateParamScope('${name}')" class="${sc}">
          ${_buildParamOptions(routing.param)}
        </select>
      </div>
      <button onclick="applyRouting('${name}')" title="Store this routing by name — the strip index is resolved live from device feedback when the macro fires"
          class="shrink-0 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-300 hover:text-white text-xs px-3 py-2 rounded-lg transition-all">
        Set routing
      </button>
      ${_identifyButtons(name)}
    </div>
  </div>` : '';

  panel.innerHTML = `<div class="space-y-3 text-sm">

    ${_editorModeToggle(name, 'advanced')}
    ${_issueStripHTML(macroTargetIssues(m), false)}

    ${_descriptionRow(name, m, ic)}

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
        <select id="snapshot-select:${name}" data-field="snapshot" class="${ic}">
          ${_buildSnapshotOptions(m.workspace, m.snapshot)}
        </select>
      </div>
    </div>

    ${routingPickerHtml}

    <div>
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">How — Steps</div>
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
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest mb-1.5">When — MIDI Triggers</div>
      <div class="space-y-1.5">${midiHtml || '<div class="text-zinc-600 text-xs italic">no triggers — fire from the UI or MQTT only</div>'}</div>
      <button onclick="addEditorTrigger('${name}')"
          class="text-xs text-zinc-500 hover:text-orange-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800 mt-2">
        <i class="fas fa-plus text-[9px]"></i> MIDI trigger
      </button>
    </div>

    ${_editorFooter(name)}

  </div>`;

  // Populate the cascading send picker, restoring the macro's current send,
  // and set the submix dropdown's enabled state for the current parameter
  if (hasChannelMap) {
    updateSendPickerOptions(name, routing.row === 3 ? `__out3__${routing.channel}`
      : routing.row === 2 ? `__pb__${routing.channel}` : routing.channel);
    updateParamScope(name);
  }
}

// Simple patch editor (#9): one routing, one behavior, one trigger — a
// projection over the same macro object the advanced editor writes. Renders
// only for macros that pass simpleModeShape(); everything else stays advanced.
function _renderSimpleEditor(name, m, panel) {
  const ic = EDIT_IC, sc = EDIT_SC, nc = EDIT_NC;
  const shape = simpleModeShape(m);
  if (!shape) { _renderAdvancedEditor(name, m, panel); return; }
  const step = shape.step;
  const i = shape.stepIndex;

  const hasChannelMap = ((window._picker || {}).outputs || []).length > 0;
  const routing = _currentRouting(m);

  // WHAT — the same picker selects (same ids) as the advanced editor, so
  // updateSendPickerOptions / updateParamScope / applyRouting work verbatim.
  // No "Set routing" button: every change applies immediately.
  const whatHtml = hasChannelMap ? `<div class="flex gap-2 items-center flex-wrap">
      <div class="flex-1 min-w-[120px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Input channel</div>
        <select id="routing-send:${name}" onchange="applyRouting('${name}')" class="${sc} w-full"></select>
      </div>
      <span class="text-zinc-500 text-xs shrink-0">→</span>
      <div class="flex-1 min-w-[160px]">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Output submix</div>
        <select id="routing-submix:${name}" onchange="updateSendPickerOptions('${name}');applyRouting('${name}')" class="${sc} w-full">
          ${_buildSubmixPickerOptions(routing.submix)}
        </select>
      </div>
      <div class="shrink-0">
        <div class="text-[10px] text-zinc-600 mb-1 uppercase tracking-widest">Parameter</div>
        <select id="routing-param:${name}" onchange="updateParamScope('${name}');applyRouting('${name}')" class="${sc}">
          ${_buildParamOptions(routing.param)}
        </select>
      </div>
      <div class="shrink-0 flex gap-1">${_identifyButtons(name)}</div>
    </div>`
    : `<div class="text-xs text-zinc-500 italic">no channels discovered yet — run discovery from the gear menu first</div>`;

  // HOW — mode cards from OP_DEFS, then the active mode's controls
  const mode = _opMode(step.operation);
  const cards = ['set', 'ramp', 'lfo', 'knob'].map(k => {
    const d = OP_DEFS[k];
    const on = k === mode;
    return `<button onclick="changeStepMode('${name}',${i},'${k}')" title="${d.desc}"
        class="flex-1 py-2 rounded-xl text-center transition-all ${on
          ? 'bg-orange-500 text-black font-bold'
          : 'bg-zinc-900/80 border border-zinc-800 text-zinc-400 hover:text-white'}">
      <div>${d.glyph}</div>
      <div class="text-xs">${d.label}</div>
    </button>`;
  }).join('');
  const behaviorControls = mode === 'set'
    ? `<div class="flex gap-2 items-center">${_valueControl(name, i, step, nc, sc)}</div>`
    : mode === 'knob'
    ? _knobControls(name, i, step, sc)
    : `${_timingControls(name, i, step.operation)}
       ${_opExtraControls(name, i, step.operation, sc)}
       ${_modControls(name, i, step)}`;

  // WHEN — at most one trigger in simple mode
  const t = shape.trigger;
  const whenHtml = t ? _triggerRow(name, t, 0)
    : `<button onclick="addEditorTrigger('${name}')"
        class="text-xs text-zinc-500 hover:text-orange-400 transition-colors px-2 py-1 rounded-lg hover:bg-zinc-800">
        <i class="fas fa-plus text-[9px]"></i> MIDI trigger
      </button>`;

  panel.innerHTML = `<div class="space-y-3 text-sm">

    ${_editorModeToggle(name, 'simple')}
    ${_issueStripHTML(macroTargetIssues(m), false)}

    ${_descriptionRow(name, m, ic)}

    <div class="flex gap-2">
      <div class="flex-1">
        <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Workspace</div>
        <select data-field="workspace" class="${ic}" onchange="updateSnapshotOptions('${name}', this.value)">
          ${_buildWorkspaceOptions(m.workspace)}
        </select>
      </div>
      <div class="flex-1">
        <div class="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Snapshot</div>
        <select id="snapshot-select:${name}" data-field="snapshot" class="${ic}">
          ${_buildSnapshotOptions(m.workspace, m.snapshot)}
        </select>
      </div>
    </div>

    <div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest">What — routing</div>
      ${whatHtml}
    </div>

    <div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest">How — behavior</div>
      <div class="flex gap-2">${cards}</div>
      ${_opModeDesc(mode)}
      ${behaviorControls}
    </div>

    <div class="bg-zinc-900/80 border border-zinc-800 p-2.5 rounded-xl space-y-2">
      <div class="text-[10px] text-zinc-500 uppercase tracking-widest">When — trigger</div>
      ${whenHtml}
    </div>

    ${_editorFooter(name)}

  </div>`;

  if (hasChannelMap) {
    updateSendPickerOptions(name, routing.row === 3 ? `__out3__${routing.channel}`
      : routing.row === 2 ? `__pb__${routing.channel}` : routing.channel);
    updateParamScope(name);
  }
}

// Read every [data-field] input in the open editor back into the edit buffer.
// Returns the buffer. Skip elements with no editor open (routing picker
// selects carry no data-field, so they never pollute the macro).
function _harvestEditor(name) {
  const panel = document.getElementById(`detail:${name}`);
  const m = window._editBuffers[name];
  if (!panel || !m) return m;

  panel.querySelectorAll('[data-field]').forEach(el => {
    const parts = el.dataset.field.split('.');
    let obj = m;
    for (let i = 0; i < parts.length - 1; i++) {
      const k = isNaN(parts[i]) ? parts[i] : Number(parts[i]);
      if (obj[k] === undefined || obj[k] === null) {
        // Auto-vivify: a control rendered for a field the macro doesn't
        // have yet must still harvest. Bailing here silently dropped the
        // ramp 'to' slider after re-pointing a raw step (field report:
        // 38% saved as 100% — the step had no operation.range to land in)
        obj[k] = isNaN(parts[i + 1]) ? {} : [];
      }
      obj = obj[k];
    }
    const lastRaw = parts[parts.length - 1];
    const last = isNaN(lastRaw) ? lastRaw : Number(lastRaw);
    if (el.type === 'checkbox') {
      obj[last] = el.checked;
    } else if (el.dataset.parseParam !== undefined) {
      // unit-aware text field ("8k", "-6", "Q0.7"); blank = key absent,
      // NOT zero (an explicit 0 default and "no default" must differ)
      const pv = _parseParamText(el.dataset.parseParam, el.value);
      if (pv == null) delete obj[last];
      else obj[last] = parseFloat(pv.toFixed(4));
    } else if (el.type === 'number' || el.type === 'range' || el.dataset.numeric !== undefined) {
      // data-numeric: selects whose values are numbers (LFO rate) — harvesting
      // them as strings would break strict-=== consumers and JSON hygiene.
      // A select's blank option means "no value" (the companion pin's
      // "not pinned"): key absent, NOT 0 — 0 would pin Bell / 6 dB/oct.
      if (el.tagName === 'SELECT' && el.value === '') delete obj[last];
      else obj[last] = el.value === '' ? 0 : parseFloat(el.value);
    } else {
      obj[last] = el.value;
    }
  });
  return m;
}

async function saveInlineEdit(name) {
  const btn = document.getElementById(`edit-save:${name}`);
  const m = _harvestEditor(name);
  if (!m) return;

  if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }

  try {
    // Belt-and-braces with the server-side strip: never send runtime fields
    await API.saveMacro(name, _cleanMacro(m));
    window._lastLocalSave = { name, ts: Date.now() };  // suppress own WS echo
    macros[name] = JSON.parse(JSON.stringify(m));
    cancelInlineEdit(name);
    // Reopen in read-only mode to show the saved state
    setTimeout(() => showDetail(name), 30);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
    if (btn) { btn.textContent = 'Save'; btn.disabled = false; }
  }
}

function cancelInlineEdit(name) {
  delete window._editBuffers[name];
  delete window._descStale[name];   // stale hint is per-edit-session
  _disarmMidiLearn();
  const panel = document.getElementById(`detail:${name}`);
  const arrow = document.getElementById(`detail-arrow:${name}`);
  if (!panel) return;
  if (window._drawerOpenFor === name && macros[name]) {
    panel.innerHTML = _detailHTML(name, macros[name]);   // #31: back to read-only, drawer stays
    return;
  }
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
    delete window._editorModes[name];
    delete macros[name];
    renderCards();
  } catch (e) {
    alert(`Delete failed: ${e.message}`);
  }
}

// ── New Macro flow ────────────────────────────────────────────────────────────
const MACRO_NAME_RE = /^[A-Za-z0-9_\-]{1,64}$/;

// A fresh KNOB: first input's send to the first output, following a CC
// (learn replaces the placeholder), hold + auto-enable on
function _blankKnobTemplate() {
  const picker = window._picker || {};
  const firstOut = (picker.outputs || [])[0];
  const firstIn  = (picker.inputs || [])[0];
  const step = { osc: '', value: '{{param}}',
                 // full travel tops out at UNITY, not +6 dB overgain
                 // (#user design note) - widen to [0, 1] in DETAILS to
                 // opt back into the overgain headroom
                 operation: { type: 'knob', hold: true, range: [0, 0.8172] } };
  if (firstOut && firstIn) step.target = { submix: firstOut.name, channel: firstIn.name };
  return { description: '', steps: [step],
           midi_triggers: [{ type: 'control_change', number: 20, channel: 1,
                             use_value_as_param: true }] };
}

window.openNewKnob = function () {
  openNewMacro();                      // resets kind + title...
  window._newMacroKind = 'knob';       // ...then claim it for a knob
  const title = document.querySelector('#new-macro-modal h2');
  if (title) title.innerHTML = '<i class="fas fa-sliders text-orange-400 text-sm"></i> New Knob';
};

function _blankMacroTemplate() {
  const picker = window._picker || {};
  const firstOut = (picker.outputs || [])[0];
  const firstIn  = (picker.inputs || [])[0];
  const step = {
    osc: '',
    value: '{{param}}',
    operation: { type: 'ramp', bars: 2, bpm: 140, curve: 'triangle' },
  };
  // Name-based target — live-resolved at fire time (strip indices drift
  // with stereo-link state, names don't)
  if (firstOut && firstIn) {
    step.target = { submix: firstOut.name, channel: firstIn.name };
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
  window._newMacroKind = null;
  const title = document.querySelector('#new-macro-modal h2');
  if (title) title.innerHTML = '<i class="fas fa-plus text-orange-400 text-sm"></i> New Macro';
  const tmpl  = document.getElementById('new-macro-template');
  const nameEl = document.getElementById('new-macro-name');
  const errEl  = document.getElementById('new-macro-error');
  if (!modal) return;
  if (tmpl) {
    tmpl.innerHTML = '<option value="">Blank (send + ramp template)</option>' +
      Object.keys(macros).sort()
        .map(n => `<option value="${_esc(n)}">Duplicate: ${_esc(_displayName(n, macros[n]))}</option>`)
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
  const typed = (nameEl ? nameEl.value : '').trim();
  const name = _slugKey(typed);               // machine-safe key

  if (!MACRO_NAME_RE.test(name)) {
    return showErr('Name needs at least one letter, digit, _ or - (max 64)');
  }
  if (macros[name]) {
    return showErr(`"${name}" already exists`);
  }

  const source = tmpl && tmpl.value ? macros[tmpl.value] : null;
  const body = source ? _cleanMacro(source)
             : window._newMacroKind === 'knob' ? _blankKnobTemplate()
             : _blankMacroTemplate();
  window._newMacroKind = null;
  if (typed !== name) body.label = typed;   // "Lo Cut" shown, key Lo_Cut
  else delete body.label;

  try {
    await API.saveMacro(name, body);
    macros[name] = body;
    closeNewMacro();
    renderCards();
    // Scroll to the new card and open it straight in edit mode
    requestAnimationFrame(() => {
      const card = document.getElementById(`card:${name}`);
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // New patches open simple (#9); duplicates of advanced macros open advanced
      window._editorModes[name] = simpleModeShape(body) ? 'simple' : 'advanced';
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
  const cb  = document.getElementById(`bpm-clock-cb:${macroName}-${stepIndex}`);
  const inp = document.getElementById(`bpm-input:${macroName}-${stepIndex}`);
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

