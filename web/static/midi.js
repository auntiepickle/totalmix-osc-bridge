/* midi.js — Web MIDI init, CC routing, device selector */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

// ── Signal activity flash on the MIDI pill ────────────────────────────────────
function flashMIDIActivity() {
  const el = document.getElementById('midi-status');
  if (!el) return;
  el.classList.add('!bg-green-400', '!text-black');
  setTimeout(() => el.classList.remove('!bg-green-400', '!text-black'), 120);
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
  const badge = document.getElementById(`last-trigger-${name}`);
  if (!badge) return;
  const ts = new Date().toLocaleTimeString();
  badge.textContent = `${ts} • val ${value}`;
  badge.classList.remove('text-zinc-600');
  badge.classList.add('bg-green-500/20', 'text-green-400');
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
