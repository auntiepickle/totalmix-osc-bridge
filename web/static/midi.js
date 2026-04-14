/* midi.js — Web MIDI init, CC routing, device selector */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

// ── Signal activity flash — dot briefly pulses bright white on CC ─────────────
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
  const cc = data1;
  const value = data2 / 127.0;

  flashMIDIActivity();

  let triggered = false;
  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === 'control_change' && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] Triggered ${name} (CC${cc} ch${channel} val=${data2})`);
        fireMacro(name, value, false);
        updateCardLastTrigger(name, cc, data2, message.target ? message.target.name : midiConnectedDevice, channel);
        triggered = true;
        return;
      }
    }
  });
}

function updateCardLastTrigger(name, cc, value, deviceName, channel) {
  // Light up the LED indicator immediately on MIDI trigger (before WS roundtrip)
  pulseLED(name, Date.now() / 1000);
}

// ── MIDI init / connect / disconnect / rescan ─────────────────────────────────
async function initWebMIDI() {
  if (!navigator.requestMIDIAccess || midiInput) return;
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: false });
    _populateSelector();
    const target = Array.from(midiAccess.inputs.values()).find(i => i.name === lastMidiDevice)
      || Array.from(midiAccess.inputs.values())[0];
    if (target) {
      _connectInput(target);
    }
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
  console.log('[MIDI] Disconnected');
  updateStatusHeader();
};

window.rescanMIDI = async () => {
  midiInput = null;
  midiAccess = null;
  midiConnectedDevice = '';
  await initWebMIDI();
};
