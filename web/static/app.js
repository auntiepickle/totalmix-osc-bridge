/* app.js — global state, WebSocket, macro loading, status updates */

// ── Global state (shared by ui.js and midi.js) ───────────────────────────────
let macros = {};
let currentWorkspace = '—';
let currentSnapshot = '—';
let midiConnectedDevice = '';
let lastFiredMacro = null;  // { name, ts }

// ── WebSocket ─────────────────────────────────────────────────────────────────
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
let ws = null;
let _wsRetryMs = 1000;

// Reconnect with capped backoff: the socket carries ALL live behavior
// (LEDs, progress, WS/SS tracking, cross-tab sync) — without this, one
// drop silently froze the UI while the REST health dots stayed green
// (review finding). loadMacros() on open re-syncs whatever was missed.
function _connectWS() {
  ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);
  ws.onopen = () => {
    console.log('[WS] Connected to bridge');
    document.title = 'TotalMix OSC Bridge';
    _wsRetryMs = 1000;
    loadMacros();
  };
  ws.onclose = () => {
    console.warn(`[WS] Disconnected — retrying in ${_wsRetryMs / 1000}s`);
    document.title = '⚠ offline — TotalMix OSC Bridge';
    setTimeout(_connectWS, _wsRetryMs);
    _wsRetryMs = Math.min(_wsRetryMs * 2, 10000);
  };
  ws.onmessage = _onWSMessage;
}
_connectWS();

function _onWSMessage(event) {
  const data = JSON.parse(event.data);
  const layoutChanged =
    (data.current_workspace && data.current_workspace !== currentWorkspace) ||
    (data.current_snapshot && data.current_snapshot !== currentSnapshot);
  if (data.current_workspace) currentWorkspace = data.current_workspace;
  if (data.current_snapshot) currentSnapshot = data.current_snapshot;
  // #22: a switch re-pairs/renames channels — refresh the picker inventory
  // and recompute every card's validity icon against the NEW layout
  if (layoutChanged) _scheduleValidityRefresh();

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
      showSkipReason(ev.name, ev.reason);
      // #22: persist on the card too — a completion update may follow and
      // overwrite this with the truer ok/partial verdict
      if (macros[ev.name]) {
        macros[ev.name].last_fire = { status: 'skipped', reason: ev.reason,
                                      skipped_steps: [], at: Date.now() / 1000 };
        updateHealthLine(ev.name);
      }
    } else if (ev.type === 'sweep_complete') {
      loadPicker();      // fresh table — refresh the routing inventory
      checkBankWidth();
    } else if (ev.type === 'macro_created' || ev.type === 'macro_updated'
               || ev.type === 'macro_deleted') {
      // Another tab changed the macro set — re-sync cards. Skip when the
      // change is this tab's own doing (already reflected locally) or an
      // editor is open: renderCards() would wipe the just-opened panel.
      const editing = Object.keys(window._editBuffers || {}).length > 0;
      const alreadyApplied =
        (ev.type === 'macro_created' && !!macros[ev.name]) ||
        (ev.type === 'macro_deleted' && !macros[ev.name]) ||
        (ev.type === 'macro_updated'
          && window._lastLocalSave?.name === ev.name
          && Date.now() - window._lastLocalSave.ts < 3000);
      if (!editing && !alreadyApplied) loadMacros();
    }
  }

  if (data.macro_event && data.macro_event.type === 'knob_update') {
    const ev = data.macro_event;
    if (macros[ev.name]) {
      if (ev.value != null) macros[ev.name].knob_value = ev.value;
      macros[ev.name].device_value = ev.device_value;
      // device-side change (someone moved TotalMix): the screen slider
      // follows the mixer - it is a window, not a motor fight
      if (ev.source === 'device' && ev.device_value != null) {
        macros[ev.name].knob_value = ev.device_value;
      }
      if ('enable_value' in ev) macros[ev.name].enable_value = ev.enable_value;
      if (ev.companions) macros[ev.name].companions = ev.companions;
      updateKnobCard(ev.name);   // slider follows MIDI/API/hold alike; drag guard inside
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
    macros = await API.getMacros();
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
    const s = await API.getStatus();
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
    await API.initMappingsFromExample();
    document.getElementById('example-mappings-banner').classList.add('hidden');
    await loadMacros();
  } catch (e) {
    alert(`Init failed: ${e.message}`);
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
    // Web MIDI needs a secure context — say so instead of a bare 'No MIDI'
    // (midi.js sets this once at init, but this function runs on every WS
    // message and would clobber it)
    label.textContent = (!navigator.requestMIDIAccess && !window.isSecureContext)
      ? 'MIDI needs HTTPS' : 'No MIDI';
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
      `<option value="${_esc(ws)}"${ws === currentWorkspace ? ' selected' : ''}>${_esc(ws)}</option>`
    ).join('');

  // Snapshot dropdown — scoped to the confirmed workspace
  const ssValues = wsKnown && snapMap[currentWorkspace]
    ? _snapshotNames(snapMap[currentWorkspace])   // dual-shape safe (#22)
    : [];
  const ssKnown = ssValues.some(
    s => s.toLowerCase() === (currentSnapshot || '').toLowerCase()
  );
  ssSel.innerHTML =
    `<option value="" disabled${!ssKnown ? ' selected' : ''}>—</option>` +
    ssValues.map(ss =>
      `<option value="${_esc(ss)}"${ss.toLowerCase() === (currentSnapshot || '').toLowerCase() ? ' selected' : ''}>${_esc(ss)}</option>`
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
  const ssValues = snapMap[ws] ? _snapshotNames(snapMap[ws]) : [];
  if (ssSel) {
    ssSel.innerHTML =
      `<option value="" disabled selected>—</option>` +
      ssValues.map(s => `<option value="${_esc(s)}">${_esc(s)}</option>`).join('');
  }

  try {
    await API.switch(ws, ss || null);
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
  el.textContent = `⚡ ${_displayName(lastFiredMacro.name, macros[lastFiredMacro.name])} · ${ts}`;
  el.classList.remove('hidden');
}

// ── KNOB macros: coalesced value stream over the WebSocket ───────────────────
// A knob streams dozens of ticks a second; last-value-wins every 25ms keeps
// one in-flight write per knob and zero HTTP round-trips (WS when open,
// POST fallback otherwise).
window._knobPending = {};
let _knobFlushTimer = null;

window.sendKnob = function (name, value) {
  window._knobPending[name] = Math.max(0, Math.min(1, parseFloat(value) || 0));
  if (_knobFlushTimer) return;
  _knobFlushTimer = setTimeout(() => {
    _knobFlushTimer = null;
    const pending = window._knobPending;
    window._knobPending = {};
    Object.entries(pending).forEach(([n, v]) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'knob', name: n, value: v }));
      } else {
        API.setKnob(n, v).catch(() => {});
      }
    });
  }, 25);
};

// Card slider dragged by hand: readout immediately, device via the stream
window.knobInput = function (name, value) {
  const m = macros[name];
  const step = m ? _knobStepOf(m) : null;
  if (m) m.knob_value = parseFloat(value);
  const lbl = document.getElementById(`knob-val-${name}`);
  if (lbl && step) {
    const param = (step.target && step.target.param) || 'volume';
    lbl.textContent = fmtParamValue(param, _shapeKnob(parseFloat(value), step.operation));
  }
  sendKnob(name, value);
};

// Reflect a knob_update on the card; skip moving the slider while the user
// is dragging it (their pointer owns it until release)
function updateKnobCard(name, moveSlider = true) {
  const m = macros[name];
  const step = m ? _knobStepOf(m) : null;
  if (!step) return;
  const param = (step.target && step.target.param) || 'volume';
  const slider = document.getElementById(`knob-${name}`);
  const lbl = document.getElementById(`knob-val-${name}`);
  const dev = document.getElementById(`knob-dev-${name}`);
  const v = Number.isFinite(parseFloat(m.knob_value)) ? parseFloat(m.knob_value) : null;
  if (slider && v != null && window._knobDrag !== name) slider.value = v;
  if (lbl && v != null) lbl.textContent = fmtParamValue(param, _shapeKnob(v, step.operation));
  if (dev) dev.textContent = Number.isFinite(parseFloat(m.device_value))
    ? 'device ' + fmtParamValue(param, parseFloat(m.device_value)) : '';
  const chip = document.getElementById(`knob-en-${name}`);
  if (chip) chip.outerHTML = _enableChipHTML(name, m, step);
  (COMPANION_FOR[param] || []).forEach(cp => {
    const sl = document.getElementById(`knob-cps-${name}-${cp}`);
    const cv = parseFloat((m.companions || {})[cp]);
    if (sl && Number.isFinite(cv) && window._knobDrag !== `${name}:${cp}`) {
      sl.value = cv;
      const lbl = document.getElementById(`knob-cpv-${name}-${cp}`);
      const def = PARAM_DEFS[cp] || {};
      if (lbl) lbl.textContent = def.fmt ? def.fmt(cv) : Math.round(cv * 100) + '%';
    }
    const el = document.getElementById(`knob-cp-${name}-${cp}`);
    if (el) {
      const tmp = document.createElement('div');
      tmp.innerHTML = _companionChipsHTML(name, m, step);
      const fresh = tmp.querySelector(`#knob-cp-${name}-${cp}`);
      if (fresh) el.outerHTML = fresh.outerHTML;
    }
  });
}

// Companion chip click: step the enum to its next option on the device.
// If the knob PINS this param, the pin follows the choice (and is saved),
// otherwise the next knob move would snap it back.
window.cycleKnobParam = async function (name, param, count) {
  const m = macros[name];
  if (!m) return;
  const cur = parseFloat((m.companions || {})[param]);
  const idx = Number.isFinite(cur) ? Math.round(cur * (count - 1)) : -1;
  const next = (idx + 1) % count;
  const value = next / (count - 1);
  try {
    await API.setKnobParam(name, param, value);
    const step = _knobStepOf(m);
    const pins = step && step.operation && step.operation.companions;
    if (pins && pins[param] != null) {
      pins[param] = parseFloat(value.toFixed(4));
      await API.saveMacro(name, _cleanMacro(m));
      window._lastLocalSave = { name, ts: Date.now() };
    }
  } catch (e) { console.warn('[knob] companion write failed:', e.message); }
};

// Companion mini-slider stream (band gain / Q): coalesced HTTP writes
window._cpPending = {};
let _cpFlushTimer = null;
window.companionInput = function (name, param, value) {
  const m = macros[name];
  const def = PARAM_DEFS[param] || {};
  const v = Math.max(0, Math.min(1, parseFloat(value) || 0));
  if (m) { m.companions = m.companions || {}; m.companions[param] = v; }
  const lbl = document.getElementById(`knob-cpv-${name}-${param}`);
  if (lbl) lbl.textContent = def.fmt ? def.fmt(v) : Math.round(v * 100) + '%';
  window._cpPending[`${name}\u0000${param}`] = v;
  if (_cpFlushTimer) return;
  _cpFlushTimer = setTimeout(() => {
    _cpFlushTimer = null;
    const pending = window._cpPending; window._cpPending = {};
    Object.entries(pending).forEach(([k, val]) => {
      const [n, p] = k.split('\u0000');
      API.setKnobParam(n, p, val).catch(() => {});
    });
  }, 60);
};

// Enable chip click: flip the knob's section switch on the device
window.toggleKnobEnable = async function (name) {
  const m = macros[name];
  if (!m) return;
  const on = m.enable_value !== true;     // unknown/off -> on
  try { await API.setKnobEnable(name, on); }
  catch (e) { console.warn('[knob] enable toggle failed:', e.message); }
};

// ── Live card update from WebSocket macro_update payload ─────────────────────
function updateMacroCard(name) {
  const m = macros[name];
  if (!m) return;

  const routingEl = document.querySelector(`#card-${name} .routing-label`);
  if (routingEl && m.routing_label) routingEl.textContent = m.routing_label;

  updateHealthLine(name);   // #22: last-fire outcome rides macro_update
  if (m.last_trigger) pulseLED(name, m.last_trigger);
}

// Surgical refresh of the card's persistent health line (#22) — never a
// full card re-render, so open editors and animations are untouched
function updateHealthLine(name) {
  const slot = document.querySelector(`#card-${name} .health-line-slot`);
  const m = macros[name];
  if (slot && m && typeof _healthLineHTML === 'function') {
    slot.innerHTML = _healthLineHTML(m.last_fire);
  }
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
// A skipped macro must not look like a successful fire (field report):
// paint the reason on the card's routing label for a few seconds
function showSkipReason(name, reason) {
  const cards = document.querySelectorAll('.routing-label');
  const card = document.getElementById(`led-dot-${name}`)?.closest('[class*=card],div');
  const label = card ? card.querySelector('.routing-label') : null;
  if (!label) return;
  if (label.dataset.orig === undefined) label.dataset.orig = label.textContent;
  label.textContent = `⚠ step skipped: ${reason || 'unresolved target'}`;
  label.classList.add('text-red-400');
  clearTimeout(label._skipTimer);
  label._skipTimer = setTimeout(() => {
    label.textContent = label.dataset.orig;
    delete label.dataset.orig;
    label.classList.remove('text-red-400');
  }, 6000);
}

// Sweep action (#24): measure the physical table (~20s, read-only on the
// device) and refresh the picker when it lands
window.sweepNow = async function (btnId = 'sweep-btn') {
  const btn = document.getElementById(btnId);
  const origLabel = btn ? btn.textContent.trim() : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Measuring…'; }
  try {
    await API.startSweep();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      const d = await fetch('/api/device/sweep').then(r => r.json());
      if (d.status !== 'running') {
        if (btn) btn.textContent = d.status === 'done' ? 'Done ✓' : 'Failed — see log';
        break;
      }
    }
    await loadPicker();
  } catch (e) {
    if (btn) btn.textContent = 'Failed — see log';
    console.error('[sweep]', e);
  } finally {
    setTimeout(() => { const b = document.getElementById(btnId);
      if (b) { b.disabled = false; b.textContent = origLabel || 'Measure channels'; } }, 4000);
  }
};

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
    window._snapshotMap = await API.getSnapshotMap();
    console.log(`[UI] Snapshot map loaded — ${Object.keys(window._snapshotMap).length} workspaces`);
    _updateNavDropdowns();
  } catch (e) {
    console.warn('[UI] Could not load snapshot map:', e);
    window._snapshotMap = {};
  }
}

// ── Picker inventory (#24) — live names + physical-table hw starts ──────────
async function loadPicker() {
  try {
    window._picker = await API.getPicker();
    console.log(`[UI] Picker loaded — ${(window._picker.inputs || []).length} inputs, `
      + `${(window._picker.outputs || []).length} outputs `
      + `(${JSON.stringify(window._picker.source)})`);
  } catch (e) {
    console.warn('[UI] Could not load picker:', e);
    window._picker = { inputs: [], outputs: [], source: {} };
  }
}

// ── Pre-fill bridge state from REST — no WebSocket wait ──────────────────────
// /api/status already carries current_workspace and current_snapshot so we
// can populate the nav dropdowns immediately on load rather than waiting for
// the first WS broadcast (which can take a second or two).
async function prefillBridgeState() {
  try {
    const s = await API.getStatus();
    if (s.workspace) currentWorkspace = s.workspace;
    if (s.snapshot)  currentSnapshot  = s.snapshot;
    _updateNavDropdowns();
    updateStatusHeader();
  } catch (_) {}
}

// ── Health polling — MQTT and OSC status dots ─────────────────────────────────
async function pollHealth() {
  try {
    const h = await API.getHealth();
    _applyHealthDot('mqtt-health-dot', h.mqtt_connected, 'MQTT');
    _applyHealthDot('osc-health-dot',  h.osc_configured,  'OSC');
  } catch (_) {
    _applyHealthDot('mqtt-health-dot', false, 'MQTT');
    _applyHealthDot('osc-health-dot',  false, 'OSC');
  }
  pollGlobalTransport();
  checkBankWidth();
}

// #22: after a layout change, wait for the device to settle, refresh the
// picker (it provokes fresh row dumps itself), then recompute the warn
// icons. Debounced — a workspace+snapshot switch arrives as two broadcasts.
let _validityTimer = null;
function _scheduleValidityRefresh() {
  clearTimeout(_validityTimer);
  _validityTimer = setTimeout(async () => {
    try {
      await loadPicker();
      if (typeof refreshValidity === 'function') refreshValidity();
    } catch (_) {}
  }, 1200);
}

// #22: the OSC dot upgraded to REAL device liveness when Global OSC runs —
// heartbeat age from the cyclic status stream (light read, no probe
// traffic) instead of "an IP is configured". Classic-only deployments keep
// the old dot untouched.
async function pollGlobalTransport() {
  try {
    const g = await API.getGlobalStatus();
    if (!g.running) return;               // classic-only — leave the dot be
    // Snapshot switched ON THE DEVICE (any source — TotalMix GUI, another
    // remote, even a raw OSC recall): the slot-state feed proved unreliable
    // in 2.1 b5 (live-verified: classic recalls didn't move it), but a
    // switch always floods the change log with MANY channels at once,
    // while a human wiggle touches one or two. Burst = refresh.
    try {
      const act = await API.getDeviceActivity(window._lastActivityTs || 0);
      const distinct = (act.channels || []).length;
      if (window._lastActivityTs !== undefined && distinct >= 6) {
        _scheduleValidityRefresh();
      }
      window._lastActivityTs = act.now;
    } catch (_) {}
    const dot = document.getElementById('osc-health-dot');
    if (!dot) return;
    const age = g.alive ? g.alive.age_s : g.heartbeat_age_s;
    const fresh = age != null && age < 5;
    const staleish = age != null && age < 30;
    dot.classList.remove('bg-green-400', 'bg-amber-400', 'bg-red-500', 'bg-zinc-700');
    dot.classList.add(fresh ? 'bg-green-400' : staleish ? 'bg-amber-400' : 'bg-red-500');
    dot.title = `Global OSC (${g.transport} transport) — device heartbeat ` +
      (age != null ? `${age.toFixed(1)}s ago` : 'never received') +
      (g.status && g.status.device ? ` · ${g.status.device}` : '');
  } catch (_) { /* endpoint absent/older bridge — classic dot stands */ }
}

// ── Bank-width warning ────────────────────────────────────────────────────────
// TotalMix's per-WORKSPACE 'Number of Faders per Bank' caps what OSC can see.
// If the live bank is narrower than the channel map's highest channel, part
// of the rig is invisible to routing — surface it instead of failing quietly.
async function checkBankWidth() {
  const bankBanner  = document.getElementById('bank-width-banner');
  if (!bankBanner) return;
  try {
    const s = await API.getStatus();

    // Bank too narrow for the map — channels the map knows are unreachable
    const width  = s.osc_bank_width;
    const needed = s.channel_map_max_channel || 0;
    const bankTooNarrow = width != null && needed > 0 && width < needed;
    if (bankBanner) {
      if (bankTooNarrow) {
        document.getElementById('bank-width-actual').textContent = width;
        document.getElementById('bank-width-needed').textContent = needed;
      }
      bankBanner.classList.toggle('hidden', !bankTooNarrow);
    }

    // (#24: no drift banners at all — the physical table is layout-
    // invariant and per-write confirmations carry correctness. The one
    // setup-time banner: the table has never been measured.)
    const sweepBanner = document.getElementById('sweep-needed-banner');
    if (sweepBanner) {
      const tbl = s.physical_table || {};
      const present = tbl.present || {};
      const needed = !(present.inputs && present.outputs);
      const running = s.sweep_status === 'running';
      const txt = document.getElementById('sweep-needed-text');
      const btn = document.getElementById('sweep-btn');
      if (needed && txt && btn) {
        if (running) {
          btn.classList.add('hidden');
          txt.innerHTML = `<b>Learning your mixer's channels</b> — about 20 seconds, one time only.`;
        } else {
          btn.classList.remove('hidden');
          txt.innerHTML = `<b>One-time setup</b> — the app needs ~20 seconds to learn your mixer's channels (read-only, nothing changes on the device).`;
        }
      }
      sweepBanner.classList.toggle('hidden', !needed);
    }

    // Device unresponsive — driven ONLY by a failed probe (an idle mixer
    // sends nothing; silence must never fire this)
    const deadBanner = document.getElementById('device-dead-banner');
    if (deadBanner) {
      const dead = s.device_probe && s.device_probe.alive === false;
      deadBanner.classList.toggle('hidden', !dead);
    }
  } catch (_) {}
}

// ── Device liveness probe (gear menu) ────────────────────────────────────────
async function probeDevice() {
  try {
    const r = await API.probeDevice();
    alert(r.alive
      ? `TotalMix is responding (feedback in ${r.elapsed_s}s).`
      : 'TotalMix is NOT responding to OSC — see the banner for what to check.');
  } catch (e) {
    alert(`Probe unavailable: ${e.message}`);
  }
  checkBankWidth();  // refresh banner state immediately
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
  await Promise.all([loadSnapshotMap(), prefillBridgeState(), loadPicker()]);
  checkMappingsSource();
  pollHealth();
  setInterval(pollHealth, 15000);
});
