/* ui/editor-controls.js — typed value entry + default resets, PARAM_DEFS, per-operation step controls (knob / mod / value).
   Split out of ui.js by panel (#27 phase 4, frontend half); classic scripts sharing one global
   scope, so index.html load order matters: api.js -> app.js -> ui/*.js (this order) -> midi.js. */

// ── Typed value entry + default resets (#user requests) ────────────────
// _parseParamText: human text -> param-norm 0..1, per unit family.
// Inverse of the fmt functions; unknown params parse as percent.
function _parseParamText(param, text, op) {
  const t = String(text).trim().toLowerCase().replace(/\s+/g, '');
  if (!t) return null;
  const num = parseFloat(t.replace(/^\+/, ''));
  if (/^eq_freq_\d$/.test(param) || param === 'lowcut_freq') {
    if (!Number.isFinite(num)) return null;
    const lo = 20, hi = param === 'lowcut_freq' ? 500 : 20000;
    const toNorm = hz => Math.log(Math.max(lo, Math.min(hi, hz)) / lo) / Math.log(hi / lo);
    if (/k(hz)?$/.test(t)) return toNorm(num * 1000);   // explicit kHz
    if (/hz$/.test(t))     return toNorm(num);          // explicit Hz
    // bare number: prefer the reading that lands INSIDE the knob's bounds
    // (user report: typing "20" on a 5k-20k hi-cut meant 20k, not 20Hz)
    const rng = op && Array.isArray(op.range) ? op.range.map(Number) : [0, 1];
    const inBounds = n => n >= rng[0] - 0.002 && n <= rng[1] + 0.002;
    for (const hz of [num, num * 1000]) {
      if (hz >= lo && hz <= hi && inBounds(toNorm(hz))) return toNorm(hz);
    }
    return toNorm(num);
  }
  if (/^eq_gain_\d$/.test(param)) {
    if (!Number.isFinite(num)) return null;
    return Math.max(0, Math.min(1, (num + 20) / 40));          // +/-20 dB
  }
  if (/^eq_q_\d$/.test(param)) {
    const q = Number.isFinite(num) ? num : parseFloat(t.replace(/^q/, ''));
    if (!Number.isFinite(q)) return null;
    return Math.max(0, Math.min(1, (q - 0.4) / 9.5));          // 0.4..9.9
  }
  if (param === 'volume') {
    if (t === 'u' || t === 'unity' || t === '0db' || t === '0.0db') return FADER_UNITY;
    if (t === '-inf' || t === 'inf-' || t === 'off') return 0;
    if (t.endsWith('%')) return Math.max(0, Math.min(1, num / 100));
    if (!Number.isFinite(num)) return null;
    return _faderLin(Math.max(-65, Math.min(6, num)));   // bare number = dB
  }
  if (param === 'pan') {
    if (t === 'c' || t === 'center') return 0.5;
    const mm = t.match(/^([lr])(\d+)$/);
    if (mm) return Math.max(0, Math.min(1, 0.5 + (mm[1] === 'r' ? 1 : -1) * parseInt(mm[2], 10) / 200));
  }
  if (!Number.isFinite(num)) return null;
  const v = /%$/.test(t) || num > 1 ? num / 100 : num;          // percent-family
  return Math.max(0, Math.min(1, v));
}

// Default for a knob, in KNOB-norm (PARAM_DEFS default is param-norm and
// must inverse-map through the knob's range; device value is the fallback)
// Priority: explicit operation.default (param-norm, set in DETAILS) >
// the switch-off end for cut-filter knobs (NEUTRAL = no filtering: a
// hi-cut opens to the top, a lo-cut parks at the bottom) > the param's
// generic default IF it lands inside the knob's bounds (a clamped
// default is meaningless - user report) > mid-travel.
function _knobDefaultNorm(name) {
  const m = macros[name], step = m && _knobStepOf(m);
  if (!step) return null;
  const param = (step.target && step.target.param) || 'volume';
  const op = step.operation || {};
  const rng = Array.isArray(op.range) ? op.range.map(Number) : [0, 1];
  const span = (rng[1] - rng[0]) || 1;
  const clamp01 = v => Math.max(0, Math.min(1, v));
  const ex = parseFloat(op.default);
  if (Number.isFinite(ex)) return clamp01((ex - rng[0]) / span);
  if (op.off_at === 'min' || (!op.off_at && op.off_at_min)) return 0;
  if (op.off_at === 'max') return 1;
  const d = (PARAM_DEFS[param] || {}).default;
  if (Number.isFinite(d)) {
    const t = (d - rng[0]) / span;
    if (t >= -0.001 && t <= 1.001) return clamp01(t);
  }
  return 0.5;
}
// Companion reset targets: RME-neutral Q is 0.7, not the generic mid
const RESET_DEFAULTS = { eq_q_1: 0.0316, eq_q_2: 0.0316, eq_q_3: 0.0316 };
function _compDefault(cp) {
  if (Number.isFinite(RESET_DEFAULTS[cp])) return RESET_DEFAULTS[cp];
  const d = (PARAM_DEFS[cp] || {}).default;
  return Number.isFinite(d) ? d : 0.5;
}
window.resetKnobToDefault = function (name) {
  const v = _knobDefaultNorm(name);
  if (v == null) return;
  knobInput(name, v);
  const sl = document.getElementById(`knob:${name}`);
  if (sl) sl.value = v;
  if (window.ModulKnob) ModulKnob.set(name, v);
};
window.resetCompToDefault = function (name, cp) {
  const v = _compDefault(cp);
  companionInput(name, cp, v);
  const sl = document.getElementById(`knob-cps:${name}-${cp}`);
  if (sl) sl.value = v;
  if (window.ModulKnob) ModulKnob.set(name, v, cp);
};

// Tap the readout -> type a value ("150", "2.5k", "-6", "Q1.4", "38%").
function _valEditSwap(span, initial, commit) {
  const input = document.createElement('input');
  input.type = 'text';
  input.value = initial;
  input.className = 'mval-edit';
  input.style.cssText = 'width:4.5rem;font:inherit;color:inherit;background:#101113;' +
    'border:1px solid #444;border-radius:3px;padding:0 4px;text-align:center;';
  span.textContent = '';
  span.appendChild(input);
  input.focus(); input.select();
  let done = false;
  const finish = (apply) => {
    if (done) return; done = true;
    commit(apply ? input.value : null);
  };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}
window.startKnobValEdit = function (name) {
  if (window._valEdit) return;
  const m = macros[name], step = m && _knobStepOf(m);
  const span = document.getElementById(`knob-val:${name}`);
  if (!span || !step) return;
  const param = (step.target && step.target.param) || 'volume';
  window._valEdit = name;
  _valEditSwap(span, span.textContent, (text) => {
    window._valEdit = null;
    if (text != null) {
      const p = _parseParamText(param, text, step.operation);
      if (p != null) {
        const kn = _knobNormOf({ device_value: p }, step.operation);
        knobInput(name, kn);
        const sl = document.getElementById(`knob:${name}`);
        if (sl) sl.value = kn;
        if (window.ModulKnob) ModulKnob.set(name, kn);
        span.textContent = fmtParamValue(param, _shapeKnob(kn, step.operation));
        return;
      }
    }
    const v = _knobNormOf(m, step.operation);
    span.textContent = fmtParamValue(param, _shapeKnob(v, step.operation));
  });
};
// MIN/MAX/gate readouts in the editors are typeable too (#user request).
// The typed value lands in the paired slider (which owns the data-field),
// so the normal save harvest picks it up.
window.startModValEdit = function (name, i, which, param) {
  const key = `${name}:mod:${i}:${which}`;
  if (window._valEdit) return;
  const span = document.getElementById(`mod-${which}:${name}-${i}`);
  const slider = document.getElementById(`mod-${which}-sl:${name}-${i}`);
  if (!span || !slider) return;
  const def = PARAM_DEFS[param] || {};
  const fmt = which === 'thr' ? (v => Math.round(v * 100) + '%')
            : (def.fmt ? def.fmt : (v => Math.round(v * 100) + '%'));
  window._valEdit = key;
  _valEditSwap(span, span.textContent, (text) => {
    window._valEdit = null;
    if (text != null) {
      const pv = _parseParamText(which === 'thr' ? '__gate__' : param, text);
      if (pv != null) {
        slider.value = pv;
        span.textContent = fmt(pv);
        return;
      }
    }
    span.textContent = fmt(parseFloat(slider.value));
  });
};

window.startCompValEdit = function (name, cp) {
  if (window._valEdit) return;
  const span = document.getElementById(`knob-cpv:${name}-${cp}`);
  if (!span) return;
  const def = PARAM_DEFS[cp] || {};
  window._valEdit = `${name}:${cp}`;
  _valEditSwap(span, span.textContent, (text) => {
    window._valEdit = null;
    if (text != null) {
      const p = _parseParamText(cp, text);
      if (p != null) {
        companionInput(name, cp, p);
        const sl = document.getElementById(`knob-cps:${name}-${cp}`);
        if (sl) sl.value = p;
        if (window.ModulKnob) ModulKnob.set(name, p, cp);
        span.textContent = def.fmt ? def.fmt(p) : Math.round(p * 100) + '%';
        return;
      }
    }
    const cv = parseFloat(((macros[name] || {}).companions || {})[cp]);
    span.textContent = Number.isFinite(cv) ? (def.fmt ? def.fmt(cv) : Math.round(cv * 100) + '%') : '\u2014';
  });
};

const PARAM_DEFS = {
  volume: {
    // Real dB via the RME fader law (top of the raw scale is +6 dB
    // OVERGAIN - #user design note: 100% of a knob should be unity
    // unless specified otherwise; new knobs default range [0, unity])
    widget: 'slider', min: 0, max: 1, step: 0.01, default: FADER_UNITY,
    fmt: _fmtFaderDb,
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
// Readouts in the device's REAL units - wire-measured 2026-08-21 (#25
// HW-5): EQ gain +/-20 dB linear, EQ freq 20..20k Hz LOG taper, Q 0.4..9.9
// linear, low cut 20..500 Hz log. Mirrors global_units.py exactly.
const _fmtHz = (lo, hi) => v => {
  const hz = lo * Math.pow(hi / lo, Math.max(0, Math.min(1, v)));
  return hz >= 1000 ? `${(hz / 1000).toFixed(hz >= 10000 ? 1 : 2)}k` : `${Math.round(hz)}Hz`;
};
const _fmtDb = (lo, hi) => v => {
  const db = lo + Math.max(0, Math.min(1, v)) * (hi - lo);
  return `${db > 0 ? '+' : ''}${db.toFixed(1)}dB`;
};
const _fmtQ = v => `Q${(0.4 + Math.max(0, Math.min(1, v)) * 9.5).toFixed(1)}`;
const _eqSlider = (label, addr, fmt, dflt = 0.5) => ({
  widget: 'slider', min: 0, max: 1, step: 0.01, default: dflt, fmt,
  mod: { range: true }, channelDetail: true, label, addr,
});
Object.assign(PARAM_DEFS, {
  eq_enable: { widget: 'toggle', default: 1.0, channelDetail: true,
               label: 'EQ On/Off', addr: '/2/eqEnable',
               options: [['1.0', 'On'], ['0.0', 'Off']],
               mod: { threshold: true } },
  eq_gain_1:   _eqSlider('EQ Band 1 Gain', '/2/eqGain1', _fmtDb(-20, 20)),
  eq_freq_1:   _eqSlider('EQ Band 1 Freq', '/2/eqFreq1', _fmtHz(20, 20000)),
  eq_q_1:      _eqSlider('EQ Band 1 Q',    '/2/eqQ1',    _fmtQ),
  eq_gain_2:   _eqSlider('EQ Band 2 Gain', '/2/eqGain2', _fmtDb(-20, 20)),
  eq_freq_2:   _eqSlider('EQ Band 2 Freq', '/2/eqFreq2', _fmtHz(20, 20000)),
  eq_q_2:      _eqSlider('EQ Band 2 Q',    '/2/eqQ2',    _fmtQ),
  eq_gain_3:   _eqSlider('EQ Band 3 Gain', '/2/eqGain3', _fmtDb(-20, 20)),
  eq_freq_3:   _eqSlider('EQ Band 3 Freq', '/2/eqFreq3', _fmtHz(20, 20000)),
  eq_q_3:      _eqSlider('EQ Band 3 Q',    '/2/eqQ3',    _fmtQ),
  lowcut_freq: _eqSlider('Low Cut Freq',   '/2/lowcutFreq', _fmtHz(20, 500), 0.0),
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
  // Four-position enum, hardware-mapped (#20; device wording — Bell not
  // Peak). eqType2 does not exist: band 2 is always Bell.
  eq_type_1: { widget: 'toggle', default: 0.0, channelDetail: true,
               label: 'EQ Band 1 Type', addr: '/2/eqType1',
               options: [['0.0', 'Bell'], ['0.3333', 'Shelf'],
                         ['0.6667', 'High Pass'], ['1.0', 'Low Pass']] },
  eq_type_3: { widget: 'toggle', default: 0.0, channelDetail: true,
               label: 'EQ Band 3 Type', addr: '/2/eqType3',
               options: [['0.0', 'Bell'], ['0.3333', 'Shelf'],
                         ['0.6667', 'High Pass'], ['1.0', 'Low Pass']] },
  lowcut_grade:  _unitSlider('Low Cut Slope',       '/2/lowcutGrade',
                             v => `${6 + Math.round(v * 3) * 6} dB/oct`, 1.0),
  input_gain:    _eqSlider('Input Gain',            '/2/gain', 0.0),
  input_gain_r:  _eqSlider('Input Gain R',          '/2/gainRight', 0.0),
  phase:         _detailToggle('Phase Invert',      '/2/phase'),
  phase_r:       _detailToggle('Phase Invert R',    '/2/phaseRight'),
});

// Behavior descriptor registry (#19) — one entry per step mode, consumed by
// both the advanced step editor and the simple patch editor. Defaults must
// match operations.py (curve 'triangle', rate 1.0). Glyphs are inline SVG on
// currentColor — no Tailwind involvement, so deploy stays pull-only.
const _glyphSvg = (inner) =>
  `<svg viewBox="0 0 40 12" width="40" height="12" aria-hidden="true"
      style="vertical-align:middle;flex-shrink:0">${inner}</svg>`;
const OP_DEFS = {
  set: {
    label: 'SET',
    desc: 'Jump straight to a value',
    glyph: _glyphSvg('<polyline points="1,10 20,10 20,2 39,2" fill="none" stroke="currentColor" stroke-width="1.5"/>'),
  },
  ramp: {
    label: 'RAMP',
    desc: 'Glide over time — one-way or up-and-back',
    glyph: _glyphSvg('<polyline points="1,10 28,2 39,2" fill="none" stroke="currentColor" stroke-width="1.5"/>'),
    curves: [['linear', 'One-way — parks at the destination'],
             ['triangle', 'Up & back — parks where it started']],
    defaultCurve: 'triangle',
  },
  lfo: {
    label: 'LFO',
    desc: 'Wave on the beat until the bars run out',
    glyph: _glyphSvg('<path d="M1,6 Q6,0 11,6 T21,6 T31,6 T39,6" fill="none" stroke="currentColor" stroke-width="1.5"/>'),
    rates: [[0.25, '¼ per beat'], [0.5, '½ per beat'], [1, '1 per beat'],
            [2, '2 per beat'], [4, '4 per beat']],
    defaultRate: 1,
  },
  knob: {
    label: 'KNOB',
    desc: 'Follow a MIDI control live — the knob IS the fader',
    glyph: _glyphSvg('<circle cx="20" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="20" y1="7" x2="23.5" y2="3.5" stroke="currentColor" stroke-width="1.5"/>'),
  },
};

// Which behavior a step is in — the one place the op-type→mode mapping lives
function _opMode(op) {
  if (!op) return 'set';
  return op.type === 'lfo' ? 'lfo' : op.type === 'knob' ? 'knob' : 'ramp';
}

// KNOB behavior controls (shared by both editors): the sweep range maps the
// physical knob's travel onto a window of the parameter; 'hold' re-asserts
// the last value after every snapshot/workspace switch (snapshot-agnostic).
function _knobControls(name, i, step, sc) {
  const op = step.operation || {};
  return `${_modControls(name, i, step)}
    <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer select-none"
        title="After a snapshot or workspace switch, re-assert this knob's last value so the recall can't yank it back">
      <input type="checkbox" data-field="steps.${i}.operation.hold" class="w-3 h-3 accent-orange-500"${op.hold !== false ? ' checked' : ''}>
      hold across snapshots
    </label>
    ${ENABLE_FOR[(step.target && step.target.param) || ''] ? `<label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer select-none"
        title="If the section (EQ / low cut / dynamics / FX) is off when the knob moves, switch it on first">
      <input type="checkbox" data-field="steps.${i}.operation.auto_enable" class="w-3 h-3 accent-orange-500"${op.auto_enable !== false ? ' checked' : ''}>
      turn on with knob move
    </label>
    <div class="flex gap-2 items-center">
      <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-24 shrink-0" title="Like a console filter knob: one end of travel switches the section off; leaving it switches it back on">switch off at</span>
      <select data-field="steps.${i}.operation.off_at" class="${sc}">
        <option value=""${!(op.off_at || op.off_at_min) ? ' selected' : ''}>never (chip only)</option>
        <option value="min"${(op.off_at === 'min' || (!op.off_at && op.off_at_min)) ? ' selected' : ''}>lowest position (low cut)</option>
        <option value="max"${op.off_at === 'max' ? ' selected' : ''}>highest position (high cut)</option>
      </select>
    </div>` : ''}
    <div class="flex gap-2 items-center">
      <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-24 shrink-0"
          title="Where double-tap/double-click parks the knob. Blank = automatic: the switch-off end for cut filters, else the parameter's default">reset default</span>
      <input type="text" data-field="steps.${i}.operation.default"
          data-parse-param="${(step.target && step.target.param) || 'volume'}"
          value="${Number.isFinite(parseFloat(op.default)) ? fmtParamValue((step.target && step.target.param) || 'volume', parseFloat(op.default)) : ''}"
          placeholder="auto" class="${sc} w-24">
    </div>
    ${(COMPANION_FOR[(step.target && step.target.param) || ''] || []).filter(cp => ENUM_LABELS[cp]).map(cp => {
      const labels = ENUM_LABELS[cp];
      const pinned = (op.companions || {})[cp];
      const pinIdx = Number.isFinite(parseFloat(pinned)) ? Math.round(parseFloat(pinned) * (labels.length - 1)) : -1;
      return `<div class="flex gap-2 items-center">
        <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-24 shrink-0" title="Pin this setting: re-asserted on every move and after snapshot recalls">${_esc(cp.replace(/_/g, ' '))}</span>
        <select data-field="steps.${i}.operation.companions.${cp}" data-numeric class="${sc}">
          <option value=""${pinIdx < 0 ? ' selected' : ''}>— not pinned —</option>
          ${labels.map((l, k) => `<option value="${(k / (labels.length - 1)).toFixed(4)}"${k === pinIdx ? ' selected' : ''}>${_esc(l)}</option>`).join('')}
        </select>
      </div>`;
    }).join('')}
    <div class="text-[10px] text-zinc-500">Pair with a CC, 14-bit CC, bend or aftertouch trigger with <b>use value</b> on — every move writes straight to the device. Drag the card's slider to control it from here.</div>`;
}

// Curve / rate select for an operation step, from the OP_DEFS descriptor.
// A stored value outside the preset list gets its own option — otherwise the
// select would silently rewrite it to the first preset on the next harvest.
function _opExtraControls(name, i, op, sc) {
  if (op.type === 'lfo') {
    const def = OP_DEFS.lfo;
    const rate = Number.isFinite(parseFloat(op.rate)) ? parseFloat(op.rate) : def.defaultRate;
    let opts = def.rates.map(([v, lbl]) =>
      `<option value="${v}"${rate === v ? ' selected' : ''}>${lbl}</option>`).join('');
    if (!def.rates.some(([v]) => v === rate)) {
      opts += `<option value="${rate}" selected>${rate} per beat</option>`;
    }
    return `<div class="flex gap-2 items-center">
      <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0"
          title="How fast the wave cycles — bars set how long it runs">rate</span>
      <select data-field="steps.${i}.operation.rate" data-numeric class="${sc} flex-1">${opts}</select>
    </div>`;
  }
  const def = OP_DEFS.ramp;
  const curve = op.curve || def.defaultCurve;
  const opts = def.curves.map(([v, lbl]) =>
    `<option value="${v}"${curve === v ? ' selected' : ''}>${lbl}</option>`).join('');
  return `<div class="flex gap-2 items-center">
    <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0"
        title="Where the glide ends up when the bars run out">curve</span>
    <select data-field="steps.${i}.operation.curve" class="${sc} flex-1">${opts}</select>
  </div>`;
}

// One-line mode description + waveform glyph, shown under the mode select
function _opModeDesc(mode) {
  const def = OP_DEFS[mode] || OP_DEFS.set;
  return `<div class="flex gap-2 items-center text-[10px] text-zinc-600">
    <span class="text-zinc-500">${def.glyph}</span><span>${def.desc}</span>
  </div>`;
}

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
      <input id="mod-thr-sl:${name}-${i}" data-field="steps.${i}.operation.threshold" type="range" min="0" max="1" step="any" value="${t}"
          class="flex-1 accent-orange-500" title="double-click resets to 50%"
          oninput="document.getElementById('mod-thr:${name}-${i}').textContent = Math.round(this.value*100)+'%'"
          ondblclick="this.value=0.5; document.getElementById('mod-thr:${name}-${i}').textContent='50%'">
      <span id="mod-thr:${name}-${i}" onclick="startModValEdit('${name}',${i},'thr','${param}')" title="tap to type a value" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0 cursor-text">${Math.round(t * 100)}%</span>
    </div>`;
  }
  if (def.mod.range) {
    const isKnob = op.type === 'knob';
    const loLbl = isKnob ? 'min' : 'from', hiLbl = isKnob ? 'max' : 'to';
    const rng = Array.isArray(op.range) ? op.range : [def.min, def.max];
    const lo = Number.isFinite(parseFloat(rng[0])) ? parseFloat(rng[0]) : def.min;
    const hi = Number.isFinite(parseFloat(rng[1])) ? parseFloat(rng[1]) : def.max;
    // Two stacked rows — both sliders on one line was unreadably cramped
    // at card width (user screenshot)
    html += `<div class="space-y-2">
      <div class="flex gap-2 items-center">
        <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0">${loLbl}</span>
        <input id="mod-lo-sl:${name}-${i}" data-field="steps.${i}.operation.range.0" type="range" min="${def.min}" max="${def.max}" step="any" value="${lo}"
            class="flex-1 accent-orange-500" title="sweep start — double-click resets to ${def.fmt(def.min)}"
            oninput="document.getElementById('mod-lo:${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
            ondblclick="this.value=${def.min}; document.getElementById('mod-lo:${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
        <span id="mod-lo:${name}-${i}" onclick="startModValEdit('${name}',${i},'lo','${param}')" title="tap to type a value" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0 cursor-text">${def.fmt(lo)}</span>
      </div>
      <div class="flex gap-2 items-center">
        <span class="text-[10px] text-zinc-500 uppercase tracking-widest w-10 shrink-0">${hiLbl}</span>
        <input id="mod-hi-sl:${name}-${i}" data-field="steps.${i}.operation.range.1" type="range" min="${def.min}" max="${def.max}" step="any" value="${hi}"
            class="flex-1 accent-orange-500" title="sweep end — double-click resets to ${def.fmt(def.max)}"
            oninput="document.getElementById('mod-hi:${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
            ondblclick="this.value=${def.max}; document.getElementById('mod-hi:${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
        <span id="mod-hi:${name}-${i}" onclick="startModValEdit('${name}',${i},'hi','${param}')" title="tap to type a value" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0 cursor-text">${def.fmt(hi)}</span>
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
      step.value = String(+((def.default ?? 1.0).toFixed ? (def.default ?? 1.0).toFixed(4) : def.default ?? 1.0));
    }
  } else if (mode === 'knob') {
    // canonical knob op: no timing, no curve/rate — just range + hold
    const prev = step.operation || {};
    step.operation = { type: 'knob', hold: prev.hold !== false };
    if (Array.isArray(prev.range)) step.operation.range = prev.range;
    else if (((step.target && step.target.param) || 'volume') === 'volume') {
      step.operation.range = [0, 0.8172];   // full travel = unity, not +6 dB
    }
    else if (def.mod?.range) step.operation.range = [def.min ?? 0, def.max ?? 1];
    if (def.mod?.threshold) step.operation.threshold = prev.threshold ?? 0.5;
    step.value = '{{param}}';
  } else {
    step.operation = { ...(step.operation || { bars: 2, bpm: 140 }), type: mode };
    step.value = '{{param}}';
    // Seed the parameter's modulation shaping controls (#13)
    if (def.mod?.threshold && step.operation.threshold == null) step.operation.threshold = 0.5;
    if (def.mod?.range && !Array.isArray(step.operation.range)) {
      step.operation.range = [def.min ?? 0, def.max ?? 1];
    }
    // Seed the mode's own control and drop the other's (#19) — keeps the
    // stored JSON canonical (a ramp never carries a rate, an LFO no curve)
    if (mode === 'ramp') {
      if (step.operation.curve == null) step.operation.curve = OP_DEFS.ramp.defaultCurve;
      delete step.operation.rate;
    } else if (mode === 'lfo') {
      if (step.operation.rate == null) step.operation.rate = OP_DEFS.lfo.defaultRate;
      delete step.operation.curve;
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
    // Select the NEAREST option: exact float compare silently rewrote any
    // non-canonical stored value (0.333333 vs '0.3333') to the first
    // option on every harvest/re-render cycle (review finding)
    const selOv = def.options.reduce((a, b) =>
      Math.abs(parseFloat(b[0]) - val) < Math.abs(parseFloat(a[0]) - val) ? b : a)[0];
    return `<div class="flex gap-2 items-center flex-1">
      <select data-field="steps.${i}.value" class="${sc}">
        ${def.options.map(([ov, ol]) =>
          `<option value="${ov}"${ov === selOv ? ' selected' : ''}>${ol}</option>`).join('')}
      </select>
      ${followBox}
    </div>`;
  }
  // slider
  return `<div class="flex gap-2 items-center flex-1">
    <input data-field="steps.${i}.value" type="range"
        min="${def.min}" max="${def.max}" step="${def.step}" value="${val}"
        class="flex-1 accent-orange-500" title="double-click resets to ${def.fmt(def.default)}"
        oninput="document.getElementById('val-label:${name}-${i}').textContent = fmtParamValue('${param}', this.value)"
        ondblclick="this.value=${def.default}; document.getElementById('val-label:${name}-${i}').textContent = fmtParamValue('${param}', this.value)">
    <span id="val-label:${name}-${i}" class="text-xs text-zinc-300 font-mono w-10 text-center shrink-0">${def.fmt(val)}</span>
    ${followBox}
  </div>`;
}

// Mute is global-per-channel — the submix dropdown is meaningless for it
window.updateParamScope = function (name) {
  const paramSel  = document.getElementById(`routing-param:${name}`);
  const submixSel = document.getElementById(`routing-submix:${name}`);
  const sendSel   = document.getElementById(`routing-send:${name}`);
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
  const submixSel = document.getElementById(`routing-submix:${name}`);
  const sendSel   = document.getElementById(`routing-send:${name}`);
  const paramSel0 = document.getElementById(`routing-param:${name}`);
  const def0 = PARAM_DEFS[paramSel0 ? paramSel0.value : ''] || {};
  if (def0.global) {
    // Global FX parameter — no channel/submix, fixed address fallback
    const g = _harvestEditor(name);
    g.steps = (g.steps || []).filter(s => s.osc !== '/setSubmix');
    const pStep = _routableStep(g, name);
    const gTarget = { param: paramSel0.value };
    if (pStep) { _noteRetarget(name, JSON.stringify(pStep.target || null), gTarget, g.description);
                 pStep.target = gTarget; pStep.osc = def0.addr; }
    else g.steps.push({ osc: def0.addr, target: gTarget, value: '{{param}}',
                        operation: { type: 'ramp', bars: 2, bpm: 140,
                                     range: [0, 1] } });
    editDetail(name);
    return;
  }
  if (!submixSel || !sendSel || !sendSel.value) return;
  // #24: the picker is name-only — parse the selection's routing markers
  // (__out3__ = hardware output for EQ, __pb__ = software playback)
  const raw = sendSel.value;
  const isOut = raw.startsWith('__out3__');
  const isPb  = raw.startsWith('__pb__');
  const channelName = isOut ? raw.slice(8) : isPb ? raw.slice(6) : raw;
  if (def0.channelDetail) {
    const target = { channel: channelName, param: paramSel0.value };
    if (isOut) target.row = 3;
    const m0 = _harvestEditor(name);
    m0.steps = (m0.steps || []).filter(s => s.osc !== '/setSubmix');
    const pStep0 = _routableStep(m0, name);
    if (pStep0) { _noteRetarget(name, JSON.stringify(pStep0.target || null), target, m0.description);
                  pStep0.target = target; pStep0.osc = def0.addr; }
    else m0.steps.push({ osc: def0.addr, target,
                         value: String(def0.default ?? 0.5) });
    editDetail(name);
    return;
  }
  const m = _harvestEditor(name);
  m.steps = m.steps || [];
  // The bridge sends /setSubmix itself when resolving a target —
  // a legacy explicit step would double-send it
  m.steps = m.steps.filter(s => s.osc !== '/setSubmix');
  const paramStep = _routableStep(m, name);
  const target = { submix: submixSel.value, channel: channelName };
  if (isPb) target.row = 2;
  const paramSel = document.getElementById(`routing-param:${name}`);
  const param = paramSel ? paramSel.value : 'volume';
  if (param !== 'volume') target.param = param;
  const paramDef = PARAM_DEFS[param] || {};
  // Mute is global-per-channel (#10) — no submix scope
  if (param === 'mute') delete target.submix;
  // No stored strip addresses anymore (#24): strip numbers are snapshot
  // state, so new macros carry no fallback — no feedback means the step
  // skips safely instead of writing a maybe-wrong strip
  if (paramStep) {
    _noteRetarget(name, JSON.stringify(paramStep.target || null), target,
                  m.description);
    paramStep.target = target;
    paramStep.osc = '';
  } else if (param === 'volume') {
    // Volume defaults to a ramp — the classic send macro
    m.steps.push({ osc: '', target, value: '{{param}}',
                   operation: { type: 'ramp', bars: 2, bpm: 140, curve: 'triangle' } });
  } else {
    // Pan/mute default to a SET step so their value widgets (slider,
    // toggle) appear immediately — switch to RAMP/LFO via the mode select
    m.steps.push({ osc: '', target,
                   value: String(paramDef.default ?? 0.5) });
  }
  editDetail(name);
};

