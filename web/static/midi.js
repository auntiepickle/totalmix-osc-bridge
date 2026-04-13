/* midi.js - cleaned up for production (M2_branch — April 2026) */
/* Duplicate declaration of midiConnectedDevice REMOVED (already in app.js) */
/* Clock / non-CC messages remain silently ignored */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  const channel = (status & 0x0F) + 1;
  const cc = data1;

  if ((status & 0xF0) !== 0xB0) {
    // Silent ignore for non-CC (MIDI clock 0xF8, active sensing, etc.)
    return;
  }

  const value = data2 / 127.0;

  let triggered = false;
  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === "control_change" && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] ✅ Triggered ${name} (CC${cc} ch${channel})`);
        fireMacro(name, value, false);
        updateCardLastTrigger(name, cc, data2, message.target ? message.target.name : "U6MIDI Pro", channel);
        triggered = true;
        return;
      }
    }
  });

  if (!triggered) {
    // Optional: uncomment only if you want to see unmatched CCs
    // console.log(`[MIDI] No matching macro for CC${cc} ch${channel}`);
  }
}

function updateCardLastTrigger(name, cc, value, deviceName, channel) {
  const badge = document.getElementById(`last-trigger-${name}`);
  if (badge) {
    badge.innerHTML = `<span class="font-semibold">${deviceName}</span><br>CC${cc} • ch${channel}`;
    badge.classList.add('bg-green-500/20', 'text-green-400');
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
      midiConnectedDevice = target.name;           // ← uses global from app.js
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
  midiConnectedDevice = deviceName;                // ← uses global from app.js
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
    midiConnectedDevice = input.name;              // ← uses global from app.js
    localStorage.setItem('lastMidiDevice', input.name);
    updateMIDIBadge(`Connected: ${input.name}`);
    updateStatusHeader();
    console.log(`[MIDI] Connected to ${input.name}`);
  }
};
