/* midi.js — Web MIDI init, CC routing, device selector, MIDI activity display */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput  = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

// ── Last-CC tracking (replaces CC/s rate — "when did something last arrive?") ─
let _lastCCInfo = null;   // { cc, channel, value }
let _lastCCTime = null;   // epoch ms

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
  if ((status & 0xF0) !== 0xB0) return;   // CC only

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

// ── MIDI init / connect / disconnect / rescan ─────────────────────────────────
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
  selector.innerHTML = '<option value="">— select MIDI input —</option>';
  Array.from(midiAccess.inputs.values()).forEach(i => {
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
