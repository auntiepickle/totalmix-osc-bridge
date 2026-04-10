/* app.js - main entry point (WS, globals, init) */

const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

let macros = {};
let currentWorkspace = '—';
let currentSnapshot = '—';
let midiConnectedDevice = '—';

ws.onopen = async () => {
  console.log('[WS] Connected to bridge');
  await loadMacros();
  ensureMIDIControls();
};

ws.onclose = () => console.log('[WS] Disconnected');

ws.onmessage = function (event) {
  const data = JSON.parse(event.data);
  console.log('[WS] Received:', data);
  if (data.current_workspace) currentWorkspace = data.current_workspace;
  if (data.current_snapshot) currentSnapshot = data.current_snapshot;
  if (data.macro_update) {
    const mu = data.macro_update;
    macros[mu.name] = { ...(macros[mu.name] || {}), ...mu };
    updateMacroCard(mu.name);
  }
  updateStatusHeader();
};

function ensureMIDIControls() {
  if (document.getElementById('midi-device-selector')) return;
  const header = document.querySelector('header') || document.querySelector('.flex');
  if (!header) return;
  const midiDiv = document.createElement('div');
  midiDiv.className = 'flex items-center gap-3 ml-auto';
  midiDiv.innerHTML = `
    <select id="midi-device-selector" class="bg-[#1E1E1E] text-white px-4 py-2 rounded-2xl border border-zinc-700 focus:outline-none text-sm"></select>
    <button onclick="connectSelectedMIDI()" class="px-5 py-2 bg-green-600 hover:bg-green-500 rounded-2xl text-sm font-medium transition-colors">Connect MIDI</button>
  `;
  header.appendChild(midiDiv);
}

function updateStatusHeader() {
  const workspaceEl = document.getElementById('workspace');
  const snapshotEl = document.getElementById('snapshot');
  if (workspaceEl) workspaceEl.textContent = `Workspace: ${currentWorkspace || '—'}`;
  if (snapshotEl) snapshotEl.textContent = `Snapshot: ${currentSnapshot || '—'}`;
}

async function loadMacros() {
  console.log("🚀 loadMacros() called — fetching /api/macros");
  try {
    const res = await fetch('/api/macros');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    macros = await res.json();
    console.log("✅ Loaded", Object.keys(macros).length, "macros");
    renderCards();
    updateStatusHeader();
  } catch (err) {
    console.error("❌ loadMacros failed:", err);
  }
}

window.onload = () => {
  initWebMIDI();
};
