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

  // macro_event drives the progress bar (server-side timing)
  if (data.macro_event) {
    const ev = data.macro_event;
    if (ev.type === 'macro_start') {
      animateProgress(ev.name, ev.duration_ms);
    } else if (ev.type === 'macro_complete') {
      resetProgress(ev.name);
    }
  }

  // macro_update carries the rich card data after completion
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
    midiStatusEl.textContent = `${midiConnectedDevice}`;
    midiStatusEl.classList.remove('bg-zinc-800', 'text-zinc-400');
    midiStatusEl.classList.add('bg-green-700', 'text-white');
  } else {
    midiStatusEl.textContent = 'No MIDI';
    midiStatusEl.classList.remove('bg-green-700', 'text-white');
    midiStatusEl.classList.add('bg-zinc-800', 'text-zinc-400');
  }
}

// ── Live card update from WebSocket macro_update payload ─────────────────────
function updateMacroCard(name) {
  const m = macros[name];
  if (!m) return;

  const routingEl = document.querySelector(`#card-${name} .routing-label`);
  if (routingEl && m.routing_label) routingEl.textContent = m.routing_label;

  // Show fired timestamp in badge
  if (m.last_trigger) {
    const badge = document.getElementById(`last-trigger-${name}`);
    if (badge) {
      const ts = new Date(m.last_trigger * 1000).toLocaleTimeString();
      badge.innerHTML = `fired ${ts}`;
      badge.classList.remove('text-zinc-600');
      badge.classList.add('bg-green-500/20', 'text-green-400');
    }
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
  initWebMIDI();
});
