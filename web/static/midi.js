/* midi.js - clean production version (M2_branch) */

let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  const channel = (status & 0x0F) + 1;
  const cc = data1;
  if ((status & 0xF0) !== 0xB0) return;
  const value = data2 / 127.0;
  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === "control_change" && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] Triggered ${name} (CC${cc} ch${channel})`);
        fireMacro(name, value, false);
        const badge = document.getElementById(`last-trigger-${name}`);
        if (badge) badge.innerHTML = `✅ MIDI`;
        return;
      }
    }
  });
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
      console.log(`[MIDI] Auto-connected to ${target.name}`);
    }
  } catch (err) { console.error('[MIDI] Failed:', err); }
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
    localStorage.setItem('lastMidiDevice', input.name);
    console.log(`[MIDI] Connected to ${input.name}`);
  }
};
