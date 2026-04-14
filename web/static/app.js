/* app.js — global state, WebSocket, macro loading, status updates */

// ── Global state (shared by ui.js and midi.js) ───────────────────────────────
let macros = {};
let currentWorkspace = '—';
let currentSnapshot = '—';
let midiConnectedDevice = '';
let lastFiredMacro = null;  // { name, ts }

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

  if (data.macro_event) {
    const ev = data.macro_event;
    if (ev.type === 'macro_start') {
      animateProgress(ev.name, ev.duration_ms);
    } else if (ev.type === 'macro_complete') {
      snapProgressToZero(ev.name);
      lastFiredMacro = { name: ev.name, ts: Date.now() };
      updateLastFired();
    } else if (ev.type === 'macro_skipped') {
      flashLEDSkipped(ev.name);
    }
  }

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

// ── Status header ─────────────────────────────────────────────────────────────
function updateStatusHeader() {
  const workspaceEl = document.getElementById('workspace');
  const snapshotEl  = document.getElementById('snapshot');
  if (workspaceEl) workspaceEl.textContent = `Workspace: ${currentWorkspace || '—'}`;
  if (snapshotEl)  snapshotEl.textContent  = `Snapshot: ${currentSnapshot || '—'}`;

  const pill  = document.getElementById('midi-status');
  const dot   = document.getElementById('midi-status-dot');
  const label = document.getElementById('midi-status-text');
  if (!pill || !dot || !label) return;

  if (midiConnectedDevice) {
    label.textContent = midiConnectedDevice;
    dot.classList.remove('bg-zinc-600');
    dot.classList.add('bg-green-400', 'shadow-[0_0_6px_#4ade80]');
    pill.classList.remove('text-zinc-400', 'border-zinc-700');
    pill.classList.add('text-white', 'border-green-700');
  } else {
    label.textContent = 'No MIDI';
    dot.classList.remove('bg-green-400', 'shadow-[0_0_6px_#4ade80]');
    dot.classList.add('bg-zinc-600');
    pill.classList.remove('text-white', 'border-green-700');
    pill.classList.add('text-zinc-400', 'border-zinc-700');
  }
}

// ── Last fired display ────────────────────────────────────────────────────────
function updateLastFired() {
  const el = document.getElementById('last-fired-label');
  if (!el || !lastFiredMacro) return;
  const ts = new Date(lastFiredMacro.ts).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  el.textContent = `⚡ ${lastFiredMacro.name} · ${ts}`;
  el.classList.remove('hidden');
}

// ── Live card update from WebSocket macro_update payload ─────────────────────
function updateMacroCard(name) {
  const m = macros[name];
  if (!m) return;

  const routingEl = document.querySelector(`#card-${name} .routing-label`);
  if (routingEl && m.routing_label) routingEl.textContent = m.routing_label;

  if (m.last_trigger) pulseLED(name, m.last_trigger);
}

// ── LED: brief signal flash (green = signal received, not "running") ──────────
function pulseLED(name, triggerTimestamp) {
  const dot = document.getElementById(`led-dot-${name}`);
  if (!dot) return;

  dot.classList.remove('bg-zinc-700', 'bg-red-500');
  dot.classList.add('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
  // Short flash — green means signal received, not "executing"
  setTimeout(() => {
    dot.classList.remove('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
    dot.classList.add('bg-zinc-700');
  }, 500);

  const m = macros[name];
  if (m && m.workspace && typeof window.pulseGroupLED === 'function') {
    window.pulseGroupLED(m.workspace, name, triggerTimestamp);
  }
}

// ── LED: skipped flash (red = macro was dropped) ──────────────────────────────
function flashLEDSkipped(name) {
  const dot = document.getElementById(`led-dot-${name}`);
  if (!dot) return;

  dot.classList.remove('bg-zinc-700', 'bg-green-400');
  dot.classList.add('bg-red-500', 'shadow-[0_0_8px_#ef4444]');
  setTimeout(() => {
    dot.classList.remove('bg-red-500', 'shadow-[0_0_8px_#ef4444]');
    dot.classList.add('bg-zinc-700');
  }, 800);
}

// ── Snapshot map — fetched once on load for detail panel validation ───────────
async function loadSnapshotMap() {
  try {
    const res = await fetch('/api/snapshot_map');
    window._snapshotMap = await res.json();
    console.log(`[UI] Snapshot map loaded — ${Object.keys(window._snapshotMap).length} workspaces`);
  } catch (e) {
    console.warn('[UI] Could not load snapshot map:', e);
    window._snapshotMap = {};
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
  initWebMIDI();
  loadSnapshotMap();
});
