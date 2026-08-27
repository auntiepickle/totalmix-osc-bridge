/* midi.js — Web MIDI init, CC routing, device selector, MIDI activity display */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput  = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

// ── Last-CC tracking ──────────────────────────────────────────────────────────
let _lastCCInfo = null;   // { cc, channel, value }
let _lastCCTime = null;   // epoch ms

// ── MIDI clock BPM detection ──────────────────────────────────────────────────
// Cirklon (and most DAWs) sends 0xF8 timing clock at 24 pulses per quarter note
let _clockTicks = [];     // timestamps of recent clock ticks

function _processMIDIClock() {
  const now = Date.now();
  _clockTicks.push(now);
  if (_clockTicks.length > 25) _clockTicks.shift();
  if (_clockTicks.length < 4) return;

  // Average interval between adjacent ticks → BPM
  let sum = 0;
  for (let i = 1; i < _clockTicks.length; i++) sum += _clockTicks[i] - _clockTicks[i - 1];
  const avgMs = sum / (_clockTicks.length - 1);
  const bpm   = Math.round(60000 / (24 * avgMs));
  if (bpm < 20 || bpm > 400) return;

  window._detectedBPM = bpm;   // exposed for fireMacro() to send with trigger
  const el = document.getElementById('midi-bpm');
  if (el) el.textContent = `${bpm} BPM`;
}

// Live age display: updates every 100ms so you can see recency at a glance
setInterval(() => {
  const ageEl = document.getElementById('midi-cc-age');
  if (!ageEl || !_lastCCTime) return;
  const ms = Date.now() - _lastCCTime;
  ageEl.textContent = ms < 60000 ? `${(ms / 1000).toFixed(1)}s ago` : '>1m ago';
}, 100);

function _trackActivity(label) {
  _lastCCTime = Date.now();
  const lastEl  = document.getElementById('midi-cc-last');
  const statsEl = document.getElementById('midi-cc-stats');
  if (lastEl)  lastEl.textContent = label;
  if (statsEl) statsEl.classList.remove('hidden');
}

function _trackCC(cc, channel, value) {
  _lastCCInfo = { cc, channel, value };
  _trackActivity(`CC${cc} ch${channel}`);
}

// ── 14-bit CC pairing (#23) ──────────────────────────────────────────────────
// MIDI spec: CC 0-31 are coarse (MSB), CC 32-63 the matching fine (LSB) at
// N+32. A control_change_14 trigger names the MSB; the latest pair combines
// on every arrival, so MSB-only movements still work (fine byte holds).
const _cc14 = {};   // "channel:msb" -> { msb, lsb }

// The MSB a 14-bit trigger listens to for this cc/channel, or null
function _cc14MsbFor(cc, channel) {
  const msb = cc < 32 ? cc : cc - 32;
  for (const name of Object.keys(macros)) {
    for (const t of macros[name].midi_triggers || []) {
      if (t.type === 'control_change_14' && t.number === msb
          && t.channel === channel) return msb;
    }
  }
  return null;
}

// Fire every macro whose trigger passes `match` (first matching trigger per
// macro wins, same as the historical per-type loops)
function _fireTriggers(match, value, logLabel) {
  Object.keys(macros).forEach(name => {
    for (const trigger of macros[name].midi_triggers || []) {
      if (match(trigger)) {
        // KNOB macro + value-carrying trigger: render locally + stream the
        // value (NOT fire). knobFromMidi paints the on-screen knob on the spot
        // so it never waits for the server echo (#user: MIDI lag/jumpiness).
        if (_knobStepOf(macros[name]) && trigger.use_value_as_param) {
          (window.knobFromMidi || window.sendKnob)(name, value);
          return;
        }
        console.log(`[MIDI] ${logLabel} → ${name}`);
        fireMacro(name, trigger.use_value_as_param ? value : 1.0);
        pulseLED(name, Date.now() / 1000);
        return;
      }
    }
  });
}

// ── Signal activity flash — MIDI status dot pulses white on any CC ────────────
function flashMIDIActivity() {
  const dot = document.getElementById('midi-status-dot');
  if (!dot) return;
  dot.classList.add('!bg-white', '!shadow-[0_0_8px_#fff]');
  setTimeout(() => dot.classList.remove('!bg-white', '!shadow-[0_0_8px_#fff]'), 100);
}

// ── MIDI message handler ──────────────────────────────────────────────────────
function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;

  // MIDI Clock (0xF8) — detect BPM from Cirklon/DAW timing clock
  if (status === 0xF8) { _processMIDIClock(); return; }

  // MIDI-learn (#7, #23): an armed learn callback consumes the next
  // CC/note/PC/bend/aftertouch instead of firing macros — works with real
  // devices and the emulator. 14-bit CC auto-detects: an MSB CC (0-31) is
  // held for 80ms, and if its LSB partner (N+32) lands in that window the
  // capture upgrades to control_change_14.
  if (window._midiLearn) {
    const t = status & 0xF0;
    const ch = (status & 0x0F) + 1;
    const _finish = (captured) => {
      clearTimeout(window._learnHoldTimer);
      window._learnHold = null;
      const cb = window._midiLearn;
      window._midiLearn = null;
      if (cb) cb(captured);
    };
    if (t === 0xB0) {
      if (window._learnHold && data1 === window._learnHold.number + 32
          && ch === window._learnHold.channel) {
        _finish({ type: 'control_change_14',
                  number: window._learnHold.number, channel: ch });
        return;
      }
      if (data1 < 32) {
        clearTimeout(window._learnHoldTimer);
        window._learnHold = { number: data1, channel: ch };
        window._learnHoldTimer = setTimeout(() =>
          _finish({ type: 'control_change', number: data1, channel: ch }), 80);
        return;
      }
      _finish({ type: 'control_change', number: data1, channel: ch });
      return;
    }
    if (t === 0x90 || t === 0x80) {
      _finish({ type: (t === 0x90 && data2 > 0) ? 'note_on' : 'note_off',
                note: data1, channel: ch });
      return;
    }
    if (t === 0xC0) { _finish({ type: 'program_change', number: data1, channel: ch }); return; }
    if (t === 0xE0) { _finish({ type: 'pitch_bend', channel: ch }); return; }
    if (t === 0xD0) { _finish({ type: 'aftertouch', channel: ch }); return; }
  }

  const msgType = status & 0xF0;
  const channel = (status & 0x0F) + 1;

  // ── Control Change (0xB0) — 14-bit pairs claim the message first ──────────
  if (msgType === 0xB0) {
    const cc = data1;
    flashMIDIActivity();
    const msb14 = _cc14MsbFor(cc, channel);
    if (msb14 !== null) {
      const key = `${channel}:${msb14}`;
      const pair = _cc14[key] || (_cc14[key] = { msb: 0, lsb: 0 });
      if (cc < 32) pair.msb = data2; else pair.lsb = data2;
      const v14 = (pair.msb << 7) | pair.lsb;
      _trackActivity(`CC14:${msb14} ch${channel}`);
      _fireTriggers(t => t.type === 'control_change_14'
          && t.number === msb14 && t.channel === channel,
        v14 / 16383.0, `CC14:${msb14} ch${channel} val=${v14}`);
      return;   // consumed — plain-CC triggers never see a claimed pair
    }
    _trackCC(cc, channel, data2);
    _fireTriggers(t => t.type === 'control_change'
        && t.number === cc && t.channel === channel,
      data2 / 127.0, `CC${cc} ch${channel} val=${data2}`);
    return;
  }

  // ── Note On (0x90) / Note Off (0x80) ─────────────────────────────────────
  if (msgType === 0x90 || msgType === 0x80) {
    const note = data1;
    // Note On with velocity 0 is treated as Note Off per MIDI spec
    const trigType = (msgType === 0x90 && data2 > 0) ? 'note_on' : 'note_off';
    _fireTriggers(t => t.type === trigType
        && t.note === note && t.channel === channel,
      data2 / 127.0, `${trigType} note ${note} ch${channel} vel=${data2}`);
    return;
  }

  // ── Program Change (0xC0) — scene selection (#23) ─────────────────────────
  // A discrete event with no value: param is always 1.0 (macros carry their
  // own workspace/snapshot targets, so PC → macro → scene switch)
  if (msgType === 0xC0) {
    flashMIDIActivity();
    _trackActivity(`PC${data1} ch${channel}`);
    _fireTriggers(t => t.type === 'program_change'
        && t.number === data1 && t.channel === channel,
      1.0, `PC${data1} ch${channel}`);
    return;
  }

  // ── Pitch Bend (0xE0) — 14-bit native, springs to center (#23) ────────────
  if (msgType === 0xE0) {
    const v14 = data1 | (data2 << 7);
    flashMIDIActivity();
    _trackActivity(`BEND ch${channel}`);
    _fireTriggers(t => t.type === 'pitch_bend' && t.channel === channel,
      v14 / 16383.0, `BEND ch${channel} val=${v14}`);
    return;
  }

  // ── Channel Aftertouch (0xD0) — pressure as a source (#23) ────────────────
  if (msgType === 0xD0) {
    flashMIDIActivity();
    _trackActivity(`AT ch${channel}`);
    _fireTriggers(t => t.type === 'aftertouch' && t.channel === channel,
      data1 / 127.0, `AT ch${channel} val=${data1}`);
    return;
  }
}

// ── MIDI Emulator (#15) — a virtual test device ───────────────────────────────
// Injects synthetic messages straight into handleMIDIMessage, exercising the
// full pipeline (trigger matching, use_value_as_param, BPM clock detection,
// LEDs, macro fire) with zero hardware and zero OS drivers. Works over HTTP
// too — no Web MIDI API involved. Drivable from the panel or the console:
//   MIDIEmu.cc(44, 100)  MIDIEmu.noteOn(60)  MIDIEmu.clockStart(140)
window.MIDIEmu = {
  _clockTimer: null,

  connect() {
    if (midiInput) midiInput.onmidimessage = null;
    midiInput = null;
    midiConnectedDevice = 'MIDI Emulator';
    document.getElementById('midi-cc-stats')?.classList.remove('hidden');
    document.getElementById('midi-bpm-badge')?.classList.remove('hidden');
    console.log('[MIDI] Emulator connected (virtual device)');
    updateStatusHeader();
  },

  _send(bytes) {
    if (midiConnectedDevice !== 'MIDI Emulator') this.connect();
    handleMIDIMessage({ data: bytes });
  },

  cc(number, value = 127, channel = 1) {
    this._send([0xB0 | ((channel - 1) & 0x0F), number & 0x7F, value & 0x7F]);
  },
  noteOn(note, velocity = 127, channel = 1) {
    this._send([0x90 | ((channel - 1) & 0x0F), note & 0x7F, velocity & 0x7F]);
  },
  noteOff(note, channel = 1) {
    this._send([0x80 | ((channel - 1) & 0x0F), note & 0x7F, 0]);
  },
  pc(program, channel = 1) {
    this._send([0xC0 | ((channel - 1) & 0x0F), program & 0x7F]);
  },
  bend(value14 = 8192, channel = 1) {          // 0..16383, 8192 = center
    this._send([0xE0 | ((channel - 1) & 0x0F), value14 & 0x7F, (value14 >> 7) & 0x7F]);
  },
  aftertouch(pressure = 127, channel = 1) {
    this._send([0xD0 | ((channel - 1) & 0x0F), pressure & 0x7F]);
  },
  cc14(number, value14, channel = 1) {         // number = MSB CC (0-31)
    this._send([0xB0 | ((channel - 1) & 0x0F), number & 0x1F, (value14 >> 7) & 0x7F]);
    this._send([0xB0 | ((channel - 1) & 0x0F), (number & 0x1F) + 32, value14 & 0x7F]);
  },
  clockStart(bpm = 120) {
    this.clockStop();
    // 24 pulses per quarter note, same as the Cirklon
    this._clockTimer = setInterval(() => this._send([0xF8]), 60000 / (bpm * 24));
    console.log(`[MIDI] Emulator clock started @ ${bpm} BPM`);
  },
  clockStop() {
    if (this._clockTimer) { clearInterval(this._clockTimer); this._clockTimer = null; }
  },
};

window.toggleMIDIEmuPanel = () => {
  const panel = document.getElementById('midi-emu-panel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) window.MIDIEmu.connect();
};

window.emuSendCC = () => {
  const num = parseInt(document.getElementById('emu-cc')?.value ?? 44, 10);
  const val = parseInt(document.getElementById('emu-val')?.value ?? 127, 10);
  const ch  = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  window.MIDIEmu.cc(num, val, ch);
};

// Live mode: dragging the value slider streams CC like twisting a real knob
window.emuValInput = () => {
  const val = document.getElementById('emu-val')?.value ?? 0;
  const lbl = document.getElementById('emu-val-label');
  if (lbl) lbl.textContent = val;
  if (document.getElementById('emu-live')?.checked) window.emuSendCC();
};

window.emuNote = (on) => {
  const note = parseInt(document.getElementById('emu-note')?.value ?? 60, 10);
  const ch   = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  on ? window.MIDIEmu.noteOn(note, 127, ch) : window.MIDIEmu.noteOff(note, ch);
};

// #23 senders — PC uses its own number field; bend/aftertouch scale the
// value slider (0-127) to their native ranges
window.emuSendPC = () => {
  const num = parseInt(document.getElementById('emu-pc')?.value ?? 0, 10);
  const ch  = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  window.MIDIEmu.pc(num, ch);
};
window.emuSendBend = () => {
  const val = parseInt(document.getElementById('emu-val')?.value ?? 64, 10);
  const ch  = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  window.MIDIEmu.bend(Math.round(val / 127 * 16383), ch);
};
window.emuSendAftertouch = () => {
  const val = parseInt(document.getElementById('emu-val')?.value ?? 127, 10);
  const ch  = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  window.MIDIEmu.aftertouch(val, ch);
};
window.emuSendCC14 = () => {
  const num = parseInt(document.getElementById('emu-cc')?.value ?? 1, 10);
  const val = parseInt(document.getElementById('emu-val')?.value ?? 64, 10);
  const ch  = parseInt(document.getElementById('emu-ch')?.value ?? 1, 10);
  window.MIDIEmu.cc14(num & 0x1F, Math.round(val / 127 * 16383), ch);
};

window.emuClock = (start) => {
  if (start) {
    window.MIDIEmu.clockStart(parseInt(document.getElementById('emu-bpm')?.value ?? 120, 10));
  } else {
    window.MIDIEmu.clockStop();
  }
  const st = document.getElementById('emu-clock-state');
  if (st) st.textContent = start ? 'running' : 'stopped';
};

// ── MIDI init / connect / disconnect / rescan ─────────────────────────────────
async function initWebMIDI() {
  if (!navigator.requestMIDIAccess) {
    // Web MIDI only exists in secure contexts — say so instead of a blank
    // "No MIDI" (the classic trap: browsing http://:8088 instead of the
    // HTTPS URL)
    if (!window.isSecureContext) {
      const label = document.getElementById('midi-status-text');
      const pill  = document.getElementById('midi-status');
      if (label) label.textContent = 'MIDI needs HTTPS';
      if (pill)  pill.title = 'Web MIDI requires a secure context — open the https:// URL (see docs/setup.md) to use MIDI devices';
      const sel = document.getElementById('midi-device-selector');
      if (sel) {
        // Real devices need HTTPS, but the emulator works anywhere —
        // it never touches the Web MIDI API
        sel.innerHTML = '<option value="">MIDI needs HTTPS</option>'
          + '<option value="__emu__">🧪 MIDI Emulator</option>';
        sel.disabled = false;
      }
    }
    return;
  }
  if (midiInput) return;
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: false });
    _populateSelector();
    // #23: default is ALL inputs — every connected controller works at
    // once (clock from one, knobs from another), zero configuration
    if (!lastMidiDevice || lastMidiDevice === '__all__') {
      _connectAll();
    } else {
      const target = Array.from(midiAccess.inputs.values())
        .find(i => i.name === lastMidiDevice);
      if (target) _connectInput(target); else _connectAll();
    }
    // Hot-plug (#23): keep the selector fresh and, in all-inputs mode,
    // attach to devices as they appear
    midiAccess.onstatechange = () => {
      _populateSelector();
      if (lastMidiDevice === '__all__' || !lastMidiDevice) _connectAll();
    };
  } catch (err) {
    console.error('[MIDI] requestMIDIAccess failed:', err);
  }
}

function _populateSelector() {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  const allSelected = !lastMidiDevice || lastMidiDevice === '__all__';
  selector.innerHTML =
    `<option value="__all__"${allSelected ? ' selected' : ''}>All MIDI inputs</option>`;
  Array.from(midiAccess.inputs.values()).forEach(i => {
    const opt = document.createElement('option');
    opt.value = i.id;
    opt.textContent = i.name;
    if (!allSelected && i.name === lastMidiDevice) opt.selected = true;
    selector.appendChild(opt);
  });
  const emu = document.createElement('option');
  emu.value = '__emu__';
  emu.textContent = '🧪 MIDI Emulator';
  selector.appendChild(emu);
}

// #23: listen to every input simultaneously — the handler is stateless per
// message (except cc14 pairs, which key on channel+cc), so merging streams
// is safe
function _connectAll() {
  if (!midiAccess) return;
  const inputs = Array.from(midiAccess.inputs.values());
  inputs.forEach(i => { i.onmidimessage = handleMIDIMessage; });
  midiInput = inputs[0] || null;
  midiConnectedDevice = inputs.length
    ? `All inputs (${inputs.length})` : '';
  lastMidiDevice = '__all__';
  localStorage.setItem('lastMidiDevice', '__all__');
  if (inputs.length) {
    console.log(`[MIDI] Listening on ${inputs.length} input(s): ` +
                inputs.map(i => i.name).join(', '));
    document.getElementById('midi-cc-stats')?.classList.remove('hidden');
    document.getElementById('midi-bpm-badge')?.classList.remove('hidden');
  }
  updateStatusHeader();
}

function _connectInput(input) {
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = input;
  midiInput.onmidimessage = handleMIDIMessage;
  midiConnectedDevice = input.name;
  lastMidiDevice = input.name;
  localStorage.setItem('lastMidiDevice', input.name);
  console.log(`[MIDI] Connected to ${input.name}`);
  document.getElementById('midi-cc-stats')?.classList.remove('hidden');
  document.getElementById('midi-bpm-badge')?.classList.remove('hidden');
  updateStatusHeader();
}

window.connectSelectedMIDI = async () => {
  const selector = document.getElementById('midi-device-selector');
  if (!selector) return;
  if (selector.value === '__emu__') { window.MIDIEmu.connect(); return; }
  if (!midiAccess) return;
  if (selector.value === '__all__') {
    _detachAll();
    _connectAll();
    return;
  }
  _detachAll();   // leaving all-mode: drop the extra listeners first
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selector.value);
  if (input) _connectInput(input);
};

function _detachAll() {
  if (!midiAccess) return;
  Array.from(midiAccess.inputs.values()).forEach(i => { i.onmidimessage = null; });
}

window.disconnectMIDI = () => {
  _detachAll();
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = null;
  midiConnectedDevice = '';
  _clockTicks = [];
  document.getElementById('midi-cc-stats')?.classList.add('hidden');
  document.getElementById('midi-bpm-badge')?.classList.add('hidden');
  console.log('[MIDI] Disconnected');
  updateStatusHeader();
};

window.rescanMIDI = async () => {
  midiInput = null;
  midiAccess = null;
  midiConnectedDevice = '';
  await initWebMIDI();
};
