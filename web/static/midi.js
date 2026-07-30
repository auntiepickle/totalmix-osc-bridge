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

function _trackCC(cc, channel, value) {
  _lastCCTime = Date.now();
  _lastCCInfo = { cc, channel, value };
  const lastEl  = document.getElementById('midi-cc-last');
  const statsEl = document.getElementById('midi-cc-stats');
  if (lastEl)  lastEl.textContent = `CC${cc} ch${channel}`;
  if (statsEl) statsEl.classList.remove('hidden');
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

  // MIDI-learn (#7): an armed learn callback consumes the next CC or note
  // instead of firing macros — works with real devices and the emulator
  if (window._midiLearn) {
    const t = status & 0xF0;
    const ch = (status & 0x0F) + 1;
    let captured = null;
    if (t === 0xB0) {
      captured = { type: 'control_change', number: data1, channel: ch };
    } else if (t === 0x90 || t === 0x80) {
      captured = { type: (t === 0x90 && data2 > 0) ? 'note_on' : 'note_off',
                   note: data1, channel: ch };
    }
    if (captured) {
      const cb = window._midiLearn;
      window._midiLearn = null;
      cb(captured);
      return;
    }
  }

  const msgType = status & 0xF0;
  const channel = (status & 0x0F) + 1;

  // ── Control Change (0xB0) ─────────────────────────────────────────────────
  if (msgType === 0xB0) {
    const cc    = data1;
    const value = data2 / 127.0;
    flashMIDIActivity();
    _trackCC(cc, channel, data2);
    Object.keys(macros).forEach(name => {
      for (const trigger of macros[name].midi_triggers || []) {
        if (trigger.type === 'control_change' && trigger.number === cc && trigger.channel === channel) {
          console.log(`[MIDI] CC ${name} (CC${cc} ch${channel} val=${data2})`);
          fireMacro(name, trigger.use_value_as_param ? value : 1.0);
          pulseLED(name, Date.now() / 1000);
          return;
        }
      }
    });
    return;
  }

  // ── Note On (0x90) / Note Off (0x80) ─────────────────────────────────────
  if (msgType === 0x90 || msgType === 0x80) {
    const note     = data1;
    const velocity = data2 / 127.0;
    // Note On with velocity 0 is treated as Note Off per MIDI spec
    const isNoteOn = msgType === 0x90 && data2 > 0;
    const trigType = isNoteOn ? 'note_on' : 'note_off';

    Object.keys(macros).forEach(name => {
      for (const trigger of macros[name].midi_triggers || []) {
        if (trigger.type === trigType && trigger.note === note && trigger.channel === channel) {
          console.log(`[MIDI] ${trigType} ${name} (note ${note} ch${channel} vel=${data2})`);
          fireMacro(name, trigger.use_value_as_param ? velocity : 1.0);
          pulseLED(name, Date.now() / 1000);
          return;
        }
      }
    });
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
    const target = Array.from(midiAccess.inputs.values()).find(i => i.name === lastMidiDevice)
      || Array.from(midiAccess.inputs.values())[0];
    if (target) _connectInput(target);
  } catch (err) {
    console.error('[MIDI] requestMIDIAccess failed:', err);
  }
}

function _populateSelector() {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  selector.innerHTML = '<option value="">— select MIDI input —</option>';
  Array.from(midiAccess.inputs.values()).forEach(i => {
    const opt = document.createElement('option');
    opt.value = i.id;
    opt.textContent = i.name;
    if (i.name === lastMidiDevice) opt.selected = true;
    selector.appendChild(opt);
  });
  const emu = document.createElement('option');
  emu.value = '__emu__';
  emu.textContent = '🧪 MIDI Emulator';
  selector.appendChild(emu);
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
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selector.value);
  if (input) _connectInput(input);
};

window.disconnectMIDI = () => {
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
