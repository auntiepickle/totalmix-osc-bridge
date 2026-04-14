/* midi.js — Web MIDI init, CC routing, device selector, CC rate stats */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

// ── CC rate tracking (rolling 3s window) ──────────────────────────────────────
const _ccTimestamps = [];  // epoch ms of recent CC events
const CC_RATE_WINDOW_MS = 3000;
let _lastCCInfo = null;    // { cc, channel, value }

function _trackCC(cc, channel, value) {
  const now = Date.now();
  _ccTimestamps.push(now);
  // Prune events older than the window
  const cutoff = now - CC_RATE_WINDOW_MS;
  while (_ccTimestamps.length && _ccTimestamps[0] < cutoff) _ccTimestamps.shift();
  _lastCCInfo = { cc, channel, value };
  _updateMIDIStats();
}

function _updateMIDIStats() {
  const rateEl = document.getElementById('midi-cc-rate');
  const lastEl = document.getElementById('midi-cc-last');
  const statsEl = document.getElementById('midi-cc-stats');
  if (rateEl) {
    const rate = (_ccTimestamps.length / (CC_RATE_WINDOW_MS / 1000)).toFixed(1);
    rateEl.textContent = rate; // "CC/s" label is static in HTML
  }
  if (lastEl && _lastCCInfo) {
    lastEl.textContent = `CC${_lastCCInfo.cc}`;
  }
  if (statsEl) statsEl.classList.remove('hidden');
}

// Decay rate display toward 0 when no new CCs arrive
setInterval(() => {
  const now = Date.now();
  const cutoff = now - CC_RATE_WINDOW_MS;
  while (_ccTimestamps.length && _ccTimestamps[0] < cutoff) _ccTimestamps.shift();
  const rateEl = document.getElementById('midi-cc-rate');
  if (rateEl) {
    const rate = (_ccTimestamps.length / (CC_RATE_WINDOW_MS / 1000)).toFixed(1);
    rateEl.textContent = rate;
  }
}, 500);

// ── Signal activity flash — dot briefly pulses bright white on CC ──────────────
function flashMIDIActivity() {
  const dot = document.getElementById('midi-status-dot');
  if (!dot) return;
  dot.classList.add('!bg-white', '!shadow-[0_0_8px_#fff]');
  setTimeout(() => dot.classList.remove('!bg-white', '!shadow-[0_0_8px_#fff]'), 100);
}

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  // Only handle Control Change (0xB0); silently ignore clock, active sensing, etc.
  if ((status & 0xF0) !== 0xB0) return;

  const channel = (status & 0x0F) + 1;
  const cc      = data1;
  const value   = data2 / 127.0;

  flashMIDIActivity();
  _trackCC(cc, channel, data2);

  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === 'control_change' && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] Triggered ${name} (CC${cc} ch${channel} val=${data2})`);
        fireMacro(name, value, false);
        pulseLED(name, Date.now() / 1000);
        return;
      }
    }
  });
}

// ── MIDI init / connect / disconnect / rescan ──────────────────────────────────
async function initWebMIDI() {
  if (!navigator.requestMIDIAccess || midiInput) return;
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
  const inputs = Array.from(midiAccess.inputs.values());
  selector.innerHTML = '<option value="">— select MIDI input —</option>';
  inputs.forEach(i => {
    const opt = document.createElement('option');
    opt.value = i.id;
    opt.textContent = i.name;
    if (i.name === lastMidiDevice) opt.selected = true;
    selector.appendChild(opt);
  });
}

function _connectInput(input) {
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = input;
  midiInput.onmidimessage = handleMIDIMessage;
  midiConnectedDevice = input.name;
  lastMidiDevice = input.name;
  localStorage.setItem('lastMidiDevice', input.name);
  console.log(`[MIDI] Connected to ${input.name}`);
  // Show CC stats badge now that we have a device
  const statsEl = document.getElementById('midi-cc-stats');
  if (statsEl) statsEl.classList.remove('hidden');
  updateStatusHeader();
}

window.connectSelectedMIDI = async () => {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selector.value);
  if (input) _connectInput(input);
};

window.disconnectMIDI = () => {
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = null;
  midiConnectedDevice = '';
  const statsEl = document.getElementById('midi-cc-stats');
  if (statsEl) statsEl.classList.add('hidden');
  console.log('[MIDI] Disconnected');
  updateStatusHeader();
};

window.rescanMIDI = async () => {
  midiInput = null;
  midiAccess = null;
  midiConnectedDevice = '';
  await initWebMIDI();
};
