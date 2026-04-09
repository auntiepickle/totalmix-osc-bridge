/* =============================================
   TOTALMIX OSC BRIDGE - Web UI (M2 COMPLETE + FINAL FIXES)
   Fixes: progress bar sync, status header, MIDI status, rich details panel, green MIDI badge
   ============================================= */

const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

let macros = {};
let currentWorkspace = '—';
let currentSnapshot = '—';
let midiConnectedDevice = '—';

ws.onopen = async () => {
  console.log('[WS] Connected to bridge');
  await loadMacros();
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
  }

  updateStatusHeader();
  renderCards();   // safe re-render (progress bars are preserved)
};

// ====================== STATUS HEADER ======================
function updateStatusHeader() {
  const header = document.getElementById('status-header');
  if (!header) return;
  header.innerHTML = `
<div class="flex items-center justify-between bg-zinc-900 border-b border-zinc-700 px-6 py-4">
    <div class="flex items-center gap-8">
        <div>
            <span class="text-xs uppercase tracking-widest text-zinc-500">Workspace</span>
            <span id="workspace-name" class="ml-2 font-mono text-emerald-400">${currentWorkspace}</span>
        </div>
        <div>
            <span class="text-xs uppercase tracking-widest text-zinc-500">Snapshot</span>
            <span id="snapshot-name" class="ml-2 font-mono text-cyan-400">${currentSnapshot}</span>
        </div>
    </div>
    
    <div class="flex items-center gap-3">
        <div class="flex items-center gap-2 text-sm font-medium">
            <span class="text-emerald-400">●</span>
            MIDI: <span class="font-mono">${midiConnectedDevice}</span>
        </div>
        <select id="midi-device-selector" onchange="connectSelectedMIDI()" 
                class="bg-zinc-800 text-white text-sm px-3 py-1 rounded-xl border border-zinc-700"></select>
    </div>
</div>`;
  // Re-init MIDI selector if needed
  if (midiAccess) initWebMIDI();
}

// ====================== INITIAL LOAD ======================
async function loadMacros() {
  console.log("🚀 loadMacros() called — fetching /api/macros");
  try {
    const res = await fetch('/api/macros');
    console.log("✅ Fetch status:", res.status);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    macros = await res.json();
    console.log("✅ Loaded", Object.keys(macros).length, "macros");
    renderCards();
    updateStatusHeader();
  } catch (err) {
    console.error("❌ loadMacros failed:", err);
  }
}

// ====================== FIRE MACRO ======================
async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;

  console.log(`[UI] Firing macro: ${name} (value=${value}, ramp=${ramp})`);

  const durationMs = macro.durationMs || (ramp ? 3500 : 500);
  animateProgress(name, durationMs);   // start animation BEFORE HTTP call

  try {
    await fetch(`/api/trigger/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param: value })
    });
    console.log(`✅ Macro ${name} triggered via HTTP`);
  } catch (err) {
    console.error("❌ Trigger failed:", err);
  }
}

// ====================== PROGRESS ANIMATION (survives re-render + matches backend) ======================
function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) return;
  // Reset instantly
  bar.style.transitionDuration = '0ms';
  bar.style.width = '0%';
  void bar.offsetWidth; // force reflow
  // Animate exactly to the ramp/LFO duration
  bar.style.transitionDuration = `${durationMs}ms`;
  bar.style.width = '100%';
  // Auto-reset for next trigger
  setTimeout(() => { if (bar) bar.style.width = '0%'; }, durationMs + 50);
}

// ====================== RENDER CARDS (now includes progress bar + preserves animation) ======================
function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => {
    const m = macros[name];
    const cardHTML = `
<div id="card-${name}" class="macro-card border border-zinc-700 bg-zinc-900 rounded-xl p-4">
    <div class="flex justify-between items-start">
        <div>
            <h3 class="text-lg font-mono">${name}</h3>
            <p class="text-zinc-400 text-sm">${m.description || ''}</p>
            <p class="text-emerald-400 text-xs mt-1">${m.routing_label || '—'}</p>
        </div>
        <div class="midi-badge text-[10px] font-mono bg-green-500/20 text-green-400 px-2 py-0.5 rounded">LAST TRIGGER</div>
    </div>
    
    <!-- PROGRESS BAR -->
    <div class="mt-4 h-2 bg-zinc-800 rounded-full overflow-hidden">
        <div id="progress-bar-${name}" 
             class="h-full bg-gradient-to-r from-emerald-400 to-cyan-400 transition-all"
             style="width: 0%"></div>
    </div>
    
    <div class="mt-3 flex gap-2">
        <button onclick="fireMacro('${name}', 1.0, false)" 
                class="flex-1 bg-white text-black font-medium py-3 rounded-xl">FIRE</button>
        <button onclick="fireMacro('${name}', 1.0, true)" 
                class="flex-1 border border-white text-white font-medium py-3 rounded-xl">RAMP / LFO</button>
        <button onclick="toggleDetail('${name}')" 
                class="px-6 border border-white text-white font-medium rounded-xl">DETAILS ▼</button>
    </div>
    
    <div id="detail-${name}" class="hidden mt-4 text-xs font-mono bg-zinc-950 p-3 rounded-xl"></div>
</div>`;
    html += cardHTML;
  });
  grid.innerHTML = html;
}

// ====================== RICH DETAILS PANEL ======================
function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = macros[name];
  if (!panel || !m) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    let html = `<div class="space-y-3">`;
    html += `<div><strong>Routing:</strong> ${m.routing_label || '—'}</div>`;
    html += `<div><strong>OSC Preview:</strong> <code>${m.osc_preview || '—'}</code></div>`;
    html += `<div><strong>Duration:</strong> ${m.durationMs ? (m.durationMs / 1000).toFixed(1) + 's' : '—'}</div>`;

    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong>MIDI Triggers:</strong><ul>`;
      m.midi_triggers.forEach(t => {
        html += `<li class="ml-4">• CC${t.number} ch${t.channel} ${t.use_value_as_param ? '(value as param)' : ''}</li>`;
      });
      html += `</ul></div>`;
    }

    // Full steps + raw JSON
    html += `<details><summary class="cursor-pointer">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
} const panel = document.getElementById(`detail-${name}`);
const m = macros[name];
if (!panel || !m) return;
panel.classList.toggle('hidden');
if (!panel.classList.contains('hidden')) {
  let html = `<strong>Routing</strong><br>${m.routing_label || '—'}<br><br>`;
  html += `<strong>OSC Preview</strong><br>${m.osc_preview || '—'}<br><br>`;

  if (m.midi_triggers && m.midi_triggers.length) {
    html += `<strong>MIDI Triggers</strong><br>`;
    m.midi_triggers.forEach(t => {
      html += `• CC${t.number} ch${t.channel} ${t.use_value_as_param ? '(value as param)' : ''}<br>`;
    });
  }
  panel.innerHTML = html;
}
}

// ====================== MIDI (with green badge) ======================
let midiAccess = null;
let midiInput = null;
let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';

function handleMIDIMessage(message) {
  const [status, data1, data2] = message.data;
  const channel = (status & 0x0F) + 1;
  const cc = data1;
  const valueRaw = data2;
  const deviceName = message.target ? message.target.name : "Unknown";

  if ((status & 0xF0) !== 0xB0) return;

  const value = valueRaw / 127.0;

  Object.keys(macros).forEach(name => {
    const macro = macros[name];
    for (const trigger of macro.midi_triggers || []) {
      if (trigger.type === "control_change" && trigger.number === cc && trigger.channel === channel) {
        console.log(`[MIDI] Triggered ${name} (CC${cc} ch${channel})`);
        fireMacro(name, value, false);
        updateCardLastTrigger(name, cc, valueRaw, deviceName, channel);
        return;
      }
    }
  });
}

function updateCardLastTrigger(name, cc, value, deviceName, channel) {
  const card = document.getElementById(`card-${name}`);
  if (!card) return;
  let badge = card.querySelector('.midi-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.className = 'midi-badge text-[10px] font-mono bg-green-500/20 text-green-400 px-2 py-0.5 rounded mt-2 flex flex-col gap-px';
    card.appendChild(badge);
  }
  badge.innerHTML = `<span class="font-semibold">${deviceName}</span><br>CC${cc} • ch${channel} • ${value}`;
}

async function initWebMIDI() {
  if (!navigator.requestMIDIAccess || midiInput) return;
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: false });
    const inputs = Array.from(midiAccess.inputs.values());
    const selector = document.getElementById('midi-device-selector');
    if (selector) {
      selector.innerHTML = '<option value="">— select MIDI input —</option>';
      inputs.forEach(input => {
        const opt = document.createElement('option');
        opt.value = input.id;
        opt.textContent = input.name;
        if (input.name === lastMidiDevice) opt.selected = true;
        selector.appendChild(opt);
      });
    }
    const target = inputs.find(i => i.name === lastMidiDevice) || inputs[0];
    if (target) {
      midiInput = target;
      midiInput.onmidimessage = handleMIDIMessage;
      midiConnectedDevice = target.name;
      console.log(`[MIDI] Listening on ${target.name}`);
      updateStatusHeader();
    }
  } catch (err) {
    console.error('[MIDI] Failed:', err);
  }
}

window.connectSelectedMIDI = () => {
  const selector = document.getElementById('midi-device-selector');
  const selectedId = selector.value;
  if (!selectedId || !midiAccess) return;
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selectedId);
  if (input) {
    if (midiInput) midiInput.onmidimessage = null;
    midiInput = input;
    midiInput.onmidimessage = handleMIDIMessage;
    lastMidiDevice = input.name;
    midiConnectedDevice = input.name;
    localStorage.setItem('lastMidiDevice', input.name);
    updateStatusHeader();
  }
};

// ====================== INIT ======================
window.onload = () => {
  initWebMIDI();
};