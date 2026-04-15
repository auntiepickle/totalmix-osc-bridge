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
// Rebuilds options from window._snapshotMap so they reflect the live map,
// then selects the currently active workspace and snapshot.
function _updateNavDropdowns() {
  const wsSel = document.getElementById('workspace-select');
  const ssSel = document.getElementById('snapshot-select-nav');
  if (!wsSel || !ssSel) return;

  const snapMap   = window._snapshotMap || {};
  const workspaces = Object.keys(snapMap);

  // Workspace dropdown
  wsSel.innerHTML = workspaces.length
    ? workspaces.map(ws =>
        `<option value="${ws}"${ws === currentWorkspace ? ' selected' : ''}>${ws}</option>`
      ).join('')
    : `<option value="">${currentWorkspace || 'Workspace: —'}</option>`;

  // Snapshot dropdown — scoped to the selected workspace
  const selectedWs = wsSel.value || currentWorkspace;
  const ssValues   = selectedWs && snapMap[selectedWs]
    ? Object.values(snapMap[selectedWs].snapshots || {})
    : [];
  ssSel.innerHTML = ssValues.length
    ? ssValues.map(ss =>
        `<option value="${ss}"${ss.toLowerCase() === (currentSnapshot || '').toLowerCase() ? ' selected' : ''}>${ss}</option>`
      ).join('')
    : `<option value="">${currentSnapshot || 'Snapshot: —'}</option>`;
}

// Called when either nav dropdown changes — fires POST /api/switch
window.switchToFromNav = async function() {
  const ws = document.getElementById('workspace-select')?.value;
  const ss = document.getElementById('snapshot-select-nav')?.value;
  if (!ws) return;
  // Refresh snapshot options when workspace changes
  _updateNavDropdowns();
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
                  'bg-orange-500/30',
                  'shadow-[0_0_8px_#fff]','shadow-[0_0_8px_#fbbf24]',
                  'shadow-[0_0_10px_#4ade80]','shadow-[0_0_8px_#ef4444]'];

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
  // Clear dim hold on whichever card previously ran (including this one if re-fired)
  if (_lastFiredName) {
    const prev = document.getElementById(`led-dot-${_lastFiredName}`);
    if (prev && _lastFiredName !== name) {
      prev.classList.remove(..._LED_ALL);
      prev.classList.add('bg-zinc-700');
    }
  }
  const dot = document.getElementById(`led-dot-${name}`);
  _ledSet(dot, 'bg-amber-400', 'shadow-[0_0_8px_#fbbf24]', 0);
}

// Green flash → dim orange peak-hold (like a VU meter holding its peak)
function flashLEDComplete(name) {
  _lastFiredName = name;
  const dot = document.getElementById(`led-dot-${name}`);
  if (!dot) return;
  dot.classList.remove(..._LED_ALL);
  dot.classList.add('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
  setTimeout(() => {
    dot.classList.remove('bg-green-400', 'shadow-[0_0_10px_#4ade80]');
    dot.classList.remove(..._LED_ALL);
    dot.classList.add('bg-orange-500/30');   // dim hold — clears when next macro fires
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
window.addEventListener('load', () => {
  initWebMIDI();
  loadSnapshotMap();
  checkMappingsSource();
  pollHealth();
  setInterval(pollHealth, 15000);
});
