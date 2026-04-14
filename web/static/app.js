/* app.js — global state, WebSocket, macro loading, status updates */

// ── Global state (shared by ui.js and midi.js) ───────────────────────────────
let macros = {};
let currentWorkspace = '—';
let currentSnapshot = '—';
let midiConnectedDevice = '';

// ── WebSocket ─────────────────────────────────────────────────────────────────
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

ws.onopen = () => {
  console.log('[WS] Connected to bridge');
  loadMacros();
};

ws.onclose = () => console.log('[WS] Disconnected');

ws.onmessage = function (event) {
  const data = JSON.parse(event.data);
  if (data.current_workspace) currentWorkspace = data.current_workspace;
  if (data.current_snapshot) currentSnapshot = data.current_snapshot;
  if (data.macro_update) {
    const mu = data.macro_update;
    macros[mu.name] = { ...(macros[mu.name] || {}), ...mu };
    updateMacroCard(mu.name);
  }
  updateStatusHeader();
};

// ── Macro loading ─────────────────────────────────────────────────────────────
async function loadMacros() {
  try {
    const res = await fetch('/api/macros');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    macros = await res.json();
    console.log(`[UI] Loaded ${Object.keys(macros).length} macros`);
    renderCards();
    updateStatusHeader();
  } catch (err) {
    console.error('[UI] loadMacros failed:', err);
  }
}

// ── Status header (workspace + snapshot + MIDI badge) ────────────────────────
function updateStatusHeader() {
  const workspaceEl = document.getElementById('workspace');
  const snapshotEl = document.getElementById('snapshot');
  if (workspaceEl) workspaceEl.textContent = `Workspace: ${currentWorkspace || '—'}`;
  if (snapshotEl) snapshotEl.textContent = `Snapshot: ${currentSnapshot || '—'}`;

  const midiStatusEl = document.getElementById('midi-status');
  if (!midiStatusEl) return;
  if (midiConnectedDevice) {
    midiStatusEl.textContent = `MIDI Connected: ${midiConnectedDevice}`;
    midiStatusEl.classList.remove('bg-zinc-800', 'text-zinc-400');
    midiStatusEl.classList.add('bg-green-600', 'text-white');
  } else {
    midiStatusEl.textContent = 'MIDI Disconnected';
    midiStatusEl.classList.remove('bg-green-600', 'text-white');
    midiStatusEl.classList.add('bg-zinc-800', 'text-zinc-400');
  }
}

// ── Live card update from WebSocket macro_update payload ─────────────────────
function updateMacroCard(name) {
  const m = macros[name];
  if (!m) return;

  // Update routing label if it changed
  const routingEl = document.querySelector(`#card-${name} .routing-label`);
  if (routingEl && m.routing_label) routingEl.textContent = m.routing_label;

  // Update MIDI badge with last trigger info
  const badge = document.getElementById(`last-trigger-${name}`);
  if (badge && m.last_trigger) {
    const ts = new Date(m.last_trigger * 1000).toLocaleTimeString();
    badge.innerHTML = `<span class="font-semibold">fired</span><br>${ts}`;
    badge.classList.add('bg-green-500/20', 'text-green-400');
  }

  // Animate progress bar for live value
  if (m.progress !== undefined) {
    const bar = document.getElementById(`progress-bar-${name}`);
    if (bar) {
      bar.style.transition = 'width 300ms ease';
      bar.style.width = `${m.progress}%`;
    }
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
  initWebMIDI();
});
