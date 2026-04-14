/* midi.js — Web MIDI init, CC routing, device selector */
/* Globals (macros, midiConnectedDevice) and updateStatusHeader() live in app.js */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  // Only handle Control Change (0xB0); silently ignore clock, active sensing, etc.
  if ((status & 0xF0) !== 0xB0) return;

  const channel = (status & 0x0F) + 1;
  const cc = data1;
  const value = data2 / 127.0;

  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === 'control_change' && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] Triggered ${name} (CC${cc} ch${channel} val=${data2})`);
        fireMacro(name, value, false);
        updateCardLastTrigger(name, cc, data2, message.target ? message.target.name : midiConnectedDevice, channel);
        return;
      }
    }
  });
}

function updateCardLastTrigger(name, cc, value, deviceName, channel) {
  const badge = document.getElementById(`last-trigger-${name}`);
  if (!badge) return;
  badge.innerHTML = `<span class="font-semibold">${deviceName}</span><br>CC${cc} • ch${channel}`;
  badge.classList.add('bg-green-500/20', 'text-green-400');
}

async function initWebMIDI() {
  if (!navigator.requestMIDIAccess || midiInput) return;
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: false });
    const inputs = Array.from(midiAccess.inputs.values());
    const selector = document.getElementById('midi-device-selector');
    if (selector) {
      selector.innerHTML = '<option value="">— select MIDI input —</option>';
      inputs.forEach(i => {
        const opt = document.createElement('option');
        opt.value = i.id;
        opt.textContent = i.name;
        if (i.name === lastMidiDevice) opt.selected = true;
        selector.appendChild(opt);
      });
    }
    // Auto-connect to last used device, or first available
    const target = inputs.find(i => i.name === lastMidiDevice) || inputs[0];
    if (target) {
      midiInput = target;
      midiInput.onmidimessage = handleMIDIMessage;
      midiConnectedDevice = target.name;
      console.log(`[MIDI] Auto-connected to ${target.name}`);
      updateStatusHeader();
    }
  } catch (err) {
    console.error('[MIDI] requestMIDIAccess failed:', err);
  }
}

window.connectSelectedMIDI = async () => {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selector.value);
  if (!input) return;
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = input;
  midiInput.onmidimessage = handleMIDIMessage;
  lastMidiDevice = input.name;
  midiConnectedDevice = input.name;
  localStorage.setItem('lastMidiDevice', input.name);
  console.log(`[MIDI] Connected to ${input.name}`);
  updateStatusHeader();
};
