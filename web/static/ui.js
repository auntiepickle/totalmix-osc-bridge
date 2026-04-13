/* ui.js - M2_branch STABLE (April 2026) — MIDI nav bar status + auto init */

let macros = {};

function calculateDurationMs(macro, isRamp) { /* unchanged */ }
function createMacroCardHTML(name, m) { /* unchanged */ }
function renderCards() { /* unchanged */ }
function animateProgress(name, durationMs) { /* unchanged */ }
async function fireMacro(name, value = 1.0, ramp = false) { /* unchanged */ }
function toggleDetail(name) { /* unchanged — full JSON details restored */ }

/* Auto-load + MIDI init */
async function loadMacros() {
  try {
    const res = await fetch('/api/macros');
    macros = await res.json();
    renderCards();
  } catch(e) { console.error(e); }
}

window.addEventListener('load', () => {
  loadMacros();
  initWebMIDI();                    // ← calls your exact midi.js
  console.log('🚀 UI loaded — MIDI nav bar status active');
});

/* Update nav bar status when MIDI connects */
function updateStatusHeader() {
  const statusEl = document.getElementById('midi-status');
  if (statusEl && typeof midiConnectedDevice !== 'undefined') {
    statusEl.innerHTML = `MIDI Connected: ${midiConnectedDevice}`;
    statusEl.classList.add('bg-green-600', 'text-white');
  }
}
