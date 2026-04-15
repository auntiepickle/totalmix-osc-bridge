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
      setLEDRunning(ev.name);
      lastFiredMacro = { name: ev.name, ts: Date.now() };
      updateLastFired();
    } else if (ev.type === 'macro_complete') {
      snapProgressToZero(ev.name);
      flashLEDComplete(ev.name);
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

// ── Example-mappings banner ───────────────────────────────────────────────────
async function checkMappingsSource() {
  try {
    const s = await fetch('/api/status').then(r => r.json());
    const banner = document.getElementById('example-mappings-banner');
    if (!banner) return;
    if (s.mappings_is_example) {
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
  } catch (_) {}
}

async function initMappingsFromExample() {
  const btn = document.getElementById('example-mappings-btn');
  if (btn) { btn.textContent = 'Initializing…'; btn.disabled = true; }
  try {
    const res = await fetch('/api/config/mappings/init-from-example', { method: 'POST' });
    if (res.ok) {
      document.getElementById('example-mappings-banner').classList.add('hidden');
      await loadMacros();
    } else {
      const err = await res.json().catch(() => ({}));
      alert(`Init failed: ${err.detail || 'unknown error'}`);
      if (btn) { btn.textContent = 'Use as my mappings.json'; btn.disabled = false; }
    }
  } catch (e) {
    alert(`Init error: ${e.message}`);
    if (btn) { btn.textContent = 'Use as my mappings.json'; btn.disabled = false; }
  }
}

// ── Status header + WS/SS nav dropdowns ──────────────────────────────────────
function updateStatusHeader() {
  _updateNavDropdowns();

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

// Populate and sync the workspace / snapshot nav dropdowns.
// A disabled '—' placeholder is always the first option. It stays selected
// until the WebSocket delivers a confirmed workspace/snapshot from the bridge.
function _updateNavDropdowns() {
  const wsSel = document.getElementById('workspace-select');
  const ssSel = document.getElementById('snapshot-select-nav');
  if (!wsSel || !ssSel) return;

  const snapMap    = window._snapshotMap || {};
  const workspaces = Object.keys(snapMap);
  const wsKnown    = workspaces.includes(currentWorkspace);

  // Workspace dropdown — placeholder selected when state not yet confirmed
  wsSel.innerHTML =
    `<option value="" disabled${!wsKnown ? ' selected' : ''}>—</option>` +
    workspaces.map(ws =>
      `<option value="${ws}"${ws === currentWorkspace ? ' selected' : ''}>${ws}</option>`
    ).join('');

  // Snapshot dropdown — scoped to the confirmed workspace
  const ssValues = wsKnown && snapMap[currentWorkspace]
    ? Object.values(snapMap[currentWorkspace].snapshots || {})
    : [];
  const ssKnown = ssValues.some(
    s => s.toLowerCase() === (currentSnapshot || '').toLowerCase()
  );
  ssSel.innerHTML =
    `<option value="" disabled${!ssKnown ? ' selected' : ''}>—</option>` +
    ssValues.map(ss =>
      `<option value="${ss}"${ss.toLowerCase() === (currentSnapshot || '').toLowerCase() ? ' selected' : ''}>${ss}</option>`
    ).join('');
}

// Called when either nav dropdown changes — fires POST /api/switch.
// Refreshes snapshot options immediately using the newly selected workspace
// (can't wait for WS round-trip to update currentWorkspace first).
window.switchToFromNav = async function() {
  const wsSel = document.getElementById('workspace-select');
  const ssSel = document.getElementById('snapshot-select-nav');
  const ws    = wsSel?.value;
  const ss    = ssSel?.value;
  if (!ws) return;

  // Refresh snapshot dropdown to match the workspace the user just picked
  const snapMap  = window._snapshotMap || {};
  const ssValues = snapMap[ws] ? Object.values(snapMap[ws].snapshots || {}) : [];
  if (ssSel) {
    ssSel.innerHTML =
      `<option value="" disabled selected>—</option>` +
      ssValues.map(s => `<option value="${s}">${s}</option>`).join('');
  }

  try {
    await fetch('/api/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace: ws, snapshot: ss || null }),
    });
  } catch (e) {
    console.error('[UI] switchToFromNav error:', e);
  }
};

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

// ── LED helpers ───────────────────────────────────────────────────────────────
// "last fired" hold — tracks which card carries the dim peak-hold glow
let _lastFiredName = null;

const _LED_ALL = ['bg-zinc-700','bg-white','bg-amber-400','bg-green-400','bg-red-500',
                  'bg-cyan-400',
                  'shadow-[0_0_8px_#fff]','shadow-[0_0_8px_#fbbf24]',
                  'shadow-[0_0_10px_#4ade80]','shadow-[0_0_8px_#ef4444]',
                  'shadow-[0_0_8px_#22d3ee]'];

function _ledSet(dot, color, shadow, durationMs) {
  if (!dot) return;
  dot.classList.remove(..._LED_ALL);
  dot.classList.add(color);
  if (shadow) dot.classList.add(shadow);
  if (durationMs) {
    setTimeout(() => {
      dot.classList.remove(color, shadow);
      dot.classList.add('bg-zinc-700');
    }, durationMs);
  }
}

// White flash — MIDI signal received (very brief, before macro fires)
function pulseLED(name, triggerTimestamp) {
  const dot = document.getElementById(`led-dot-${name}`);
  _ledSet(dot, 'bg-white', 'shadow-[0_0_8px_#fff]', 150);

  const m = macros[name];
  if (m && m.workspace && typeof window.pulseGroupLED === 'function') {
    window.pulseGroupLED(m.workspace, name, triggerTimestamp);
  }
}

// Amber solid — macro is executing. Clears peak-hold on the previous card first.
function setLEDRunning(name) {
  if (_lastFiredName && _lastFiredName !== name) {
    _clearLastFired(_lastFiredName);
  }
  const dot = document.getElementById(`led-dot-${name}`);
  _ledSet(dot, 'bg-amber-400', 'shadow-[0_0_8px_#fbbf24]', 0);
}

function _clearLastFired(name) {
  const dot  = document.getElementById(`led-dot-${name}`);
  const card = document.getElementById(`card-${name}`);
  if (dot)  { dot.classList.remove(..._LED_ALL); dot.classList.add('bg-zinc-700'); }
  if (card) card.classList.remove('!border-cyan-500', 'shadow-[0_0_14px_rgba(34,211,238,0.15)]');
}

// Green flash → cyan peak-hold on LED + card border
// Cyan (cool) vs amber (warm) — maximum perceptual contrast between
// "currently running" and "last fired", readable at a glance across the room.
function flashLEDComplete(name) {
  _lastFiredName = name;
  const dot  = document.getElementById(`led-dot-${name}`);
  const card = document.getElementById(`card-${name}`);
  if (!dot) return;
  dot.classList.remove(..._LED_ALL);
  dot.classList.add('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
  setTimeout(() => {
    dot.classList.remove('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
    dot.classList.remove(..._LED_ALL);
    dot.classList.add('bg-cyan-400', 'shadow-[0_0_8px_#22d3ee]');
    if (card) card.classList.add('!border-cyan-500', 'shadow-[0_0_14px_rgba(34,211,238,0.15)]');
  }, 600);
}

// Red flash — macro was skipped/dropped
function flashLEDSkipped(name) {
  const dot = document.getElementById(`led-dot-${name}`);
  _ledSet(dot, 'bg-red-500', 'shadow-[0_0_8px_#ef4444]', 800);
}

// ── Snapshot map — fetched once on load for detail panel validation ───────────
async function loadSnapshotMap() {
  try {
    const res = await fetch('/api/snapshot_map');
    window._snapshotMap = await res.json();
    console.log(`[UI] Snapshot map loaded — ${Object.keys(window._snapshotMap).length} workspaces`);
    _updateNavDropdowns();
  } catch (e) {
    console.warn('[UI] Could not load snapshot map:', e);
    window._snapshotMap = {};
  }
}

// ── Pre-fill bridge state from REST — no WebSocket wait ──────────────────────
// /api/status already carries current_workspace and current_snapshot so we
// can populate the nav dropdowns immediately on load rather than waiting for
// the first WS broadcast (which can take a second or two).
async function prefillBridgeState() {
  try {
    const s = await fetch('/api/status').then(r => r.json());
    if (s.workspace) currentWorkspace = s.workspace;
    if (s.snapshot)  currentSnapshot  = s.snapshot;
    _updateNavDropdowns();
    updateStatusHeader();
  } catch (_) {}
}

// ── Health polling — MQTT and OSC status dots ─────────────────────────────────
async function pollHealth() {
  try {
    const h = await fetch('/api/health').then(r => r.json());
    _applyHealthDot('mqtt-health-dot', h.mqtt_connected, 'MQTT');
    _applyHealthDot('osc-health-dot',  h.osc_configured,  'OSC');
  } catch (_) {
    _applyHealthDot('mqtt-health-dot', false, 'MQTT');
    _applyHealthDot('osc-health-dot',  false, 'OSC');
  }
}

function _applyHealthDot(id, ok, label) {
  const dot = document.getElementById(id);
  if (!dot) return;
  dot.classList.toggle('bg-green-500',   ok);
  dot.classList.toggle('shadow-[0_0_5px_#22c55e]', ok);
  dot.classList.toggle('bg-zinc-700',    !ok);
  dot.title = ok ? `${label}: connected` : `${label}: disconnected`;
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', async () => {
  initWebMIDI();
  // Load snapshot map and bridge state in parallel — populate dropdowns as
  // soon as both resolve rather than waiting for the first WS broadcast.
  await Promise.all([loadSnapshotMap(), prefillBridgeState()]);
  checkMappingsSource();
  pollHealth();
  setInterval(pollHealth, 15000);
});
