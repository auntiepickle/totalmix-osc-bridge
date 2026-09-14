/* ui/editor.js — structured detail panel, inline editor state (RUNTIME_FIELDS), routing picker, parameter descriptors, RME fader law.
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ── Structured detail panel ───────────────────────────────────────────────────
// _snapshotMap is loaded once on init (see app.js loadSnapshotMap)
window._snapshotMap = window._snapshotMap || {};

// The read-only details view of a macro (steps, triggers, workspace, JSON).
// Rendered into the card's #detail-<name> node, which lives in the drawer
// while open (#31, see the drawer section at the end of this file).
function _detailHTML(name, m) {
  const durationSec = (calculateDurationMs(m) / 1000).toFixed(2);
  const fireMode = (m.fire_mode || 'ignore').toUpperCase();
  const fireModeColors = {
    RESTART: 'text-red-400 bg-red-900/30 border border-red-800/50',
    QUEUE:   'text-yellow-400 bg-yellow-900/30 border border-yellow-800/50',
    IGNORE:  'text-zinc-400 bg-zinc-800 border border-zinc-700',
  };
  const fireModeClass = fireModeColors[fireMode] || fireModeColors.IGNORE;

  let html = `<div class="space-y-3 text-zinc-300 text-sm">`;

  // Pre-flight issues: stored names vs the loaded snapshot + channel maps
  html += _issueStripHTML(macroTargetIssues(m), true);

  // Top row: fire mode badge + duration + Edit button (knobs: neither
  // applies — a knob follows its control continuously)
  const isKnob = !!_knobStepOf(m);
  html += `<div class="flex items-center justify-between gap-2">
    ${isKnob
      ? `<span class="text-xs font-bold px-2.5 py-1 rounded-lg tracking-widest text-cyan-400 bg-cyan-900/30 border border-cyan-800/50">KNOB</span>
         <span class="text-zinc-500 text-xs font-mono">follows its control live</span>`
      : `<span class="text-xs font-bold px-2.5 py-1 rounded-lg tracking-widest ${fireModeClass}">${fireMode}</span>
         <span class="text-zinc-500 text-xs font-mono">⏱ ${durationSec}s</span>`}
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
      const t = step.target;
      const pLabel = t && t.param && t.param !== 'volume'
        ? ` · ${(PARAM_DEFS[t.param] || {}).label || t.param}` : '';
      const addr = t
        ? `${_esc(t.channel)}${t.submix ? ' → ' + _esc(t.submix) : (t.row === 3 ? ' (output)' : '')}${_esc(pLabel)} ⚡live`
        : _esc(step.osc || '?');
      if (step.operation && step.operation.type === 'knob') {
        const op = step.operation;
        const def = PARAM_DEFS[(t && t.param) || 'volume'] || {};
        const rng = Array.isArray(op.range) ? op.range : null;
        const rngText = rng && def.fmt ? `${def.fmt(parseFloat(rng[0]))}–${def.fmt(parseFloat(rng[1]))}` : 'full range';
        const flags = [op.hold !== false ? 'hold' : null, op.auto_enable !== false && ENABLE_FOR[(t && t.param) || ''] ? 'auto-on' : null].filter(Boolean).join(' · ');
        html += `<div class="flex items-center gap-2 font-mono bg-zinc-900/60 px-2.5 py-1.5 rounded-lg">
          <span class="text-zinc-500 text-xs">◎</span>
          <span class="text-orange-300 text-xs flex-1 truncate">${addr}</span>
          <span class="text-cyan-400 text-xs font-bold">KNOB</span>
          <span class="text-zinc-600 text-xs">${rngText}${flags ? ' · ' + flags : ''}</span>
        </div>`;
      } else if (step.operation) {
        const op = step.operation;
        const opType = (op.type || '').toUpperCase();
        const bars  = op.bars || 2;
        const bpm   = op.bpm;
        const bpmLabel = bpm === 'clock' ? '<span class="text-orange-400/80">clock</span>' : (bpm || 140);
        const curve = op.type === 'lfo'
          ? ` · ${op.rate ?? 1}×/beat`
          : (op.curve ? ` · ${op.curve}` : '');
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
          <span class="text-zinc-400 text-xs">= ${_esc(val)}</span>
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
      html += `<span class="text-xs font-mono bg-zinc-800 text-zinc-300 px-2.5 py-1 rounded-lg border border-zinc-700">${_triggerLabel(t)}</span>`;
    });
    html += `</div></div>`;
  }

  // Workspace / Snapshot — names only, no raw indices
  const snapMap = window._snapshotMap || {};
  const wsEntry = snapMap[m.workspace];
  const wsResolved = !m.workspace || !Object.keys(snapMap).length || !!wsEntry;
  const ssResolved = !m.snapshot || !Object.keys(snapMap).length
    || (!!wsEntry && _snapshotNames(wsEntry)
        .some(n => n.toLowerCase() === String(m.snapshot).toLowerCase()));
  const wsColor  = wsResolved  ? 'text-zinc-400' : 'text-red-400/70';
  const ssColor  = ssResolved  ? 'text-zinc-400' : 'text-red-400/70';
  const wsLabel  = m.workspace || '—';
  const ssLabel  = m.snapshot  || '—';
  html += `<div class="flex items-center gap-1.5 border-t border-zinc-800 pt-2 font-mono text-xs flex-wrap">
    <span class="${wsColor}">${_esc(wsLabel)}</span>
    <span class="text-zinc-700">/</span>
    <span class="${ssColor}">${_esc(ssLabel)}</span>
    ${!wsResolved || !ssResolved ? `<span class="text-red-500/60 text-[10px]">(not in snapshot map)</span>` : ''}
  </div>`;

  // Full JSON — collapsible
  html += `<details class="group">
    <summary class="cursor-pointer text-xs text-zinc-600 hover:text-orange-400 transition-colors flex items-center gap-1 select-none">
      <i class="fas fa-code text-[10px]"></i> Full JSON
    </summary>
    <pre class="mt-2 text-xs overflow-auto max-h-52 bg-zinc-950 text-zinc-400 p-3 rounded-lg border border-zinc-800 leading-relaxed">${_esc(JSON.stringify(m, null, 2))}</pre>
  </details>`;

  html += `</div>`;
  return html;
}

// ── Inline card editor ────────────────────────────────────────────────────────

// Escape value for use in HTML attribute (double-quote safe)
// Safe transport for names through inline onclick JS strings: percent-
// encode everything INCLUDING single quotes (encodeURIComponent leaves
// them alone), decode inside the handler call (review finding: a quote
// in a workspace/snapshot name broke out of the inline JS string)
function _jsArg(v) {
  return encodeURIComponent(String(v == null ? '' : v)).replace(/'/g, '%27');
}

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

// Snapshot maps come in two shapes — {"2": "Live"} and
// {"reset": {"index": 4}} / {"2": {"name": "Live"}} — mirror the bridge's
// dual-shape handling: dict values fall back to their KEY as the name.
// (#22 fix: Object.values on the dict shape stringified to
// "[object Object]", making valid snapshots look missing.)
function _snapshotNames(wsEntry) {
  return Object.entries((wsEntry || {}).snapshots || {}).map(([k, v]) =>
    (v && typeof v === 'object') ? String(v.name || k) : String(v));
}

// Build <option> list for snapshot select given a workspace.
// Matching is case-insensitive — the bridge lowercases snapshot names.
function _buildSnapshotOptions(workspace, current) {
  const snapMap = window._snapshotMap || {};
  const wsEntry = snapMap[workspace];
  const snapshots = wsEntry ? _snapshotNames(wsEntry) : [];
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
  const sel = document.getElementById(`snapshot-select:${name}`);
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
                        'routing_label', 'last_fire', 'knob_value', 'device_value', 'enable_value', 'companions'];

function _cleanMacro(m) {
  const c = JSON.parse(JSON.stringify(m));
  RUNTIME_FIELDS.forEach(f => delete c[f]);
  // an editor round-trip with every pin left at "not pinned" leaves an empty
  // companions map behind — drop it rather than persist noise
  (c.steps || []).forEach(s => {
    const op = s && s.operation;
    if (op && op.companions && !Object.keys(op.companions).length) delete op.companions;
  });
  return c;
}

// ── Routing picker (fed by the discovered channel map) ──────────────────────
// Macros store routing as NAMES ({"target": {submix, channel}}): the bridge
// live-resolves the strip index from OSC feedback at fire time, because
// /1/volume{N} is strip-positional and shifts with stereo-link state.
// The step's osc address is kept as a fallback for when feedback is absent.

function _buildSubmixPickerOptions(selected) {
  // #24: live-fed — the outputs ARE the submixes, one per output channel
  const outs = (window._picker || {}).outputs || [];
  const g = window._pickerGroups;
  if (g && g.outputs && (g.outputs.stereo.length || g.outputs.mono.length)) {
    const opt = n => `<option value="${_esc(n)}"${n === selected ? ' selected' : ''}>${_esc(n)}</option>`;
    return `<optgroup label="Stereo">${g.outputs.stereo.map(opt).join('')}</optgroup>`
         + `<optgroup label="Mono">${g.outputs.mono.map(opt).join('')}</optgroup>`;
  }
  return outs
    .map(o => `<option value="${_esc(o.name)}"${o.name === selected ? ' selected' : ''}>${_esc(o.name)}${o.hw != null ? ` (ch ${_esc(o.hw)})` : ''}</option>`)
    .join('');
}

window.updateSendPickerOptions = function (name, selectedChannel) {
  const sendSel = document.getElementById(`routing-send:${name}`);
  if (!sendSel) return;
  // A rebuild with no explicit choice keeps the current one — the submix
  // onchange callers pass nothing, and losing the selection re-targeted the
  // macro to the first input in the list (a wrong fader on a live rig)
  if (selectedChannel === undefined) selectedChannel = sendSel.value;
  const picker = window._picker || {};
  const inputs = picker.inputs || [];
  const outs   = picker.outputs || [];
  const paramSel = document.getElementById(`routing-param:${name}`);
  const def = PARAM_DEFS[paramSel ? paramSel.value : ''] || {};
  // Alias groups from the physical table (#user bug: the live snapshot
  // only shows one name-shape - offer every known form, stereo first).
  // The bridge resolves any alias to its hardware channel at write time.
  const g = window._pickerGroups;
  const inStereo = g && g.inputs ? g.inputs.stereo : [];
  const inMono   = g && g.inputs ? g.inputs.mono
                 : inputs.map(i => i.name);   // fallback: live names
  const opt = (val, label, sel) =>
    `<option value="${_esc(val)}"${val === sel ? ' selected' : ''}>${_esc(label)}</option>`;
  const grp = (label, names, mkVal, mkLbl) => names.length
    ? `<optgroup label="${label}">${names.map(n => opt(mkVal(n), mkLbl(n), selectedChannel)).join('')}</optgroup>`
    : '';
  if (def.channelDetail) {
    // EQ/dynamics exist on hardware INPUTS and OUTPUTS, not software
    // playback. Outputs aim by their measured hw start on the bridge side.
    const outStereo = g && g.outputs ? g.outputs.stereo : [];
    const outMono   = g && g.outputs ? g.outputs.mono : outs.map(o => o.name);
    sendSel.innerHTML =
      grp('Inputs — stereo', inStereo, n => n, n => n) +
      grp('Inputs — mono', inMono, n => n, n => n) +
      grp('Outputs — stereo', outStereo, n => `__out3__${n}`, n => n) +
      grp('Outputs — mono', outMono, n => `__out3__${n}`, n => n);
    return;
  }
  // The input channel list is the SAME for every submix (page-1 sends are
  // row-relative) — plus the software-playback variants of those channels
  const html =
    grp('Inputs — stereo', inStereo, n => n, n => n) +
    grp('Inputs — mono', inMono, n => n, n => n) +
    grp('Playback — stereo', inStereo, n => `__pb__${n}`, n => `${n} (playback)`) +
    grp('Playback — mono', inMono, n => `__pb__${n}`, n => `${n} (playback)`);
  sendSel.innerHTML = html
    || '<option value="">no channels — run the channel measurement</option>';
};

// A macro is "simple-representable" when it matches the canonical patch shape:
// exactly one routed step (SET value or {{param}}-driven op) and at most one
// MIDI trigger. Reads only steps/midi_triggers, so runtime fields merged into
// macros{} by WS updates can't change the verdict.
function simpleModeShape(m) {
  const steps = (m && m.steps) || [];
  if (steps.length !== 1) return null;
  const step = steps[0];
  if (!step || !step.target) return null;
  const v = step.value;
  const valueOk = v === '{{param}}' ||
    (v !== '' && v != null && Number.isFinite(parseFloat(v)));
  if (!valueOk) return null;
  const triggers = m.midi_triggers || [];
  if (triggers.length > 1) return null;
  return { step, stepIndex: 0, trigger: triggers[0] || null };
}

// The step applyRouting should retarget: the {{param}} step as always — or, in
// simple mode, the macro's single routed step even when it is a SET (numeric
// value). Without this, re-routing a SET patch would push a second step.
function _routableStep(m, name) {
  const steps = (m && m.steps) || [];
  return steps.find(s => s.value === '{{param}}')
    || ((window._editorModes || {})[name] === 'simple'
        ? steps.find(s => s.target) : null);
}

// Current routing of the buffer's param step — used to restore picker state
function _currentRouting(m) {
  // Prefer the {{param}} step; fall back to any routed step so a SET-only
  // patch (numeric value, no operation) still restores its picker state
  const paramStep = (m.steps || []).find(s => s.value === '{{param}}')
    || (m.steps || []).find(s => s.target);
  if (paramStep && paramStep.target) {
    return { submix: paramStep.target.submix,
             channel: paramStep.target.channel,
             row: paramStep.target.row,
             param: paramStep.target.param || 'volume' };
  }
  // Legacy raw-step macros: match the /setSubmix index against the
  // picker's hw starts (#24: index == hw start)
  const submixStep = (m.steps || []).find(s => s.osc === '/setSubmix');
  const submix = submixStep
    ? ((window._picker || {}).outputs || [])
        .find(o => String(o.hw) === String(parseInt(submixStep.value)))?.name
    : undefined;
  return { submix, channel: undefined };
}

// ── Parameter descriptors (#12) ──────────────────────────────────────────────
// Each parameter class declares how its VALUE should be edited and shown.
// Adding a new mod target (EQ band, gain, ...) means adding a descriptor
// here — the step editor renders the right control generically.
// ── RME fader law (CalcFaderDB / CalcFaderLin) ─────────────────────────
// Mirrored verbatim from global_units.py (wire-verified: HW-2 confirmed
// classic volume values are IDENTITY with global faderlin). 0..1 fader
// position <-> dB. Top of travel = +6.0 dB; UNITY (0.0 dB) = 836/1023.
const FADER_UNITY = 836 / 1023;                    // 0.817204...
function _faderDb(v) {
  const pos = Math.max(0, Math.min(1, v)) * 1023.0;
  const db = pos >= 649.0
    ? pos * 0.0320855615 - 26.8235294118
    : (pos * pos) * (-1.0 / 11033.0) + pos * 0.1497326203 - 65.0;
  return db < -64.9 ? -Infinity : db;
}
function _faderLin(db) {
  if (db <= -64.9) return 0;
  const pos = db >= -6.0
    ? (db - (-26.8235294118)) / 0.0320855615
    : 826.0 - Math.sqrt(-34869.0 - 11033.0 * db);
  return Math.max(0, Math.min(1, pos / 1023.0));
}
function _fmtFaderDb(v) {
  const db = _faderDb(v);
  if (db === -Infinity) return '-inf';
  return (db > 0 ? '+' : '') + db.toFixed(1) + 'dB';
}

