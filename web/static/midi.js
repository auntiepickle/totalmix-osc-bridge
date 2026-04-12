/* midi.js - all MIDI functions (M2_branch — verbose logging added for real-hardware debug) */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  const channel = (status & 0x0F) + 1;
  const cc = data1;
  const valueRaw = data2;

  /* === VERBOSE LOGGING (this is the new debug part) === */
  console.log(`[MIDI RAW] status=0x${status.toString(16)} data1=${data1} data2=${data2} → channel=${channel} CC=${cc} value=${valueRaw}`);

  if ((status & 0xF0) !== 0xB0) {
    console.log(`[MIDI] Ignored non-CC message (status 0x${status.toString(16)})`);
    return;
  }

  const value = valueRaw / 127.0;

  let triggered = false;
  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === "control_change" && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] ✅ Triggered ${name} (CC${cc} ch${channel} value=${valueRaw})`);
        fireMacro(name, value, false);
        updateCardLastTrigger(name, cc, valueRaw, message.target ? message.target.name : "Unknown", channel);
        triggered = true;
        return;
      }
    }
  });

  if (!triggered) {
    console.log(`[MIDI] No matching macro for CC${cc} ch${channel}`);
  }
}

function updateCardLastTrigger(name, cc, value, deviceName, channel) {
  const badge = document.getElementById(`last-trigger-${name}`);
  if (badge) {
    badge.innerHTML = `<span class="font-semibold">${deviceName}</span><br>CC${cc} • ch${channel}`;
    badge.classList.add('bg-green-500/20');
  }
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
    const target = inputs.find(i => i.name === lastMidiDevice) || inputs[0];
    if (target) {
      if (midiInput) midiInput.onmidimessage = null;
      midiInput = target;
      midiInput.onmidimessage = handleMIDIMessage;
      midiConnectedDevice = target.name;
      console.log(`[MIDI] Auto-connected to ${target.name}`);
      updateStatusHeader();
    }
  } catch (err) {
    console.error('[MIDI] Failed:', err);
  }
}

function updateMIDIBadge(deviceName) {
  let badge = document.getElementById('midi-connected-badge');
  if (!badge) {
    const header = document.querySelector('header') || document.querySelector('.flex');
    if (!header) return;
    badge = document.createElement('div');
    badge.id = 'midi-connected-badge';
    badge.className = 'px-4 py-1 bg-green-600 text-white text-sm font-medium rounded-2xl flex items-center gap-2 ml-auto';
    header.appendChild(badge);
  }
  badge.innerHTML = `MIDI ${deviceName}`;
  midiConnectedDevice = deviceName;
  updateStatusHeader();
}

window.connectSelectedMIDI = async () => {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  const selectedId = selector.value;
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selectedId);
  if (input) {
    if (midiInput) midiInput.onmidimessage = null;
    midiInput = input;
    midiInput.onmidimessage = handleMIDIMessage;
    lastMidiDevice = input.name;
    midiConnectedDevice = input.name;
    localStorage.setItem('lastMidiDevice', input.name);
    updateMIDIBadge(`Connected: ${input.name}`);
    updateStatusHeader();
    console.log(`[MIDI] Connected to ${input.name}`);
  }
};
