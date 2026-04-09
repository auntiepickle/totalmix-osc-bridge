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
// ====================== STATUS HEADER (matches your index.html) ======================
function updateStatusHeader() {
  const workspaceEl = document.getElementById('workspace');
  const snapshotEl = document.getElementById('snapshot');
  if (workspaceEl) workspaceEl.textContent = `Workspace: ${currentWorkspace || '—'}`;
  if (snapshotEl) snapshotEl.textContent = `Snapshot: ${currentSnapshot || '—'}`;
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
// ====================== RENDER CARDS – exact main-branch animated style ======================
function renderCards() {
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => {
    const m = macros[name];
    const cardHTML = `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-6 rounded-2xl transition-all hover:-translate-y-1 hover:shadow-2xl hover:shadow-orange-500/30">
    <div class="flex justify-between items-start mb-4">
        <div>
            <h3 class="text-2xl font-bold tracking-tight text-white">${name}</h3>
            <p class="text-zinc-400 text-sm mt-1">${m.description || ''}</p>
            <p class="text-orange-400 text-xs font-medium mt-3 tracking-widest">${m.routing_label || '—'}</p>
        </div>
        <div id="last-trigger-${name}" class="midi-badge text-[10px] font-mono bg-green-500/10 text-green-400 px-3 py-1 rounded-2xl opacity-0 group-hover:opacity-100">LAST TRIGGER</div>
    </div>
    
    <!-- PROGRESS BAR – now always visible but animates on trigger (main-branch style) -->
    <div id="progress-container-${name}" class="mt-6 h-3 bg-zinc-800 rounded-3xl overflow-hidden">
        <div id="progress-bar-${name}" 
             class="h-full bg-gradient-to-r from-orange-400 to-amber-500 transition-all"
             style="width: 0%"></div>
    </div>
    
    <div class="mt-6 grid grid-cols-3 gap-3">
        <button onclick="fireMacro('${name}', 1.0, false)" 
                class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-600 active:scale-[0.95] text-black font-bold py-5 rounded-2xl text-xl transition-all shadow-inner">
            FIRE
        </button>
        <button onclick="fireMacro('${name}', 1.0, true)" 
                class="border-2 border-amber-400 hover:bg-amber-400/10 text-amber-400 font-medium py-5 rounded-2xl transition-all">
            RAMP / LFO
        </button>
    </div>
    
    <button onclick="toggleDetail('${name}')" 
            class="mt-6 w-full text-xs font-medium text-zinc-400 hover:text-orange-400 flex items-center justify-center gap-2">
        DETAILS ▼
    </button>
    <div id="detail-${name}" class="hidden mt-4 text-sm font-mono bg-[#111111] p-4 rounded-2xl border border-zinc-700"></div>
</div>`;
    html += cardHTML;
  });
  grid.innerHTML = html;
}

// ====================== PROGRESS ANIMATION – now matches main-branch timing & visibility ======================
function animateProgress(name, durationMs) {
  const container = document.getElementById(`progress-container-${name}`);
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar || !container) return;
  
  // Show container
  container.classList.remove('hidden');
  container.style.opacity = '1';
  
  // Instant reset
  bar.style.transitionDuration = '0ms';
  bar.style.width = '0%';
  void bar.offsetWidth; // force reflow
  
  // Animate exactly to ramp/LFO duration
  bar.style.transitionDuration = `${durationMs}ms`;
  bar.style.width = '100%';
  
  // Auto-hide when finished
  setTimeout(() => {
    bar.style.width = '0%';
    setTimeout(() => {
      if (container) container.classList.add('hidden');
    }, 300);
  }, durationMs + 50);
}

// ====================== RICH DETAILS (still full JSON, now styled to match cards) ======================
function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = macros[name];
  if (!panel || !m) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    let html = `<div class="space-y-4 text-xs">`;
    html += `<div><strong class="text-orange-400">Routing:</strong> ${m.routing_label || '—'}</div>`;
    html += `<div><strong class="text-orange-400">OSC Preview:</strong> <code class="text-amber-300">${m.osc_preview || '—'}</code></div>`;
    html += `<div><strong class="text-orange-400">Duration:</strong> ${m.durationMs ? (m.durationMs/1000).toFixed(1)+'s' : '—'}</div>`;
    
    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong class="text-orange-400">MIDI Triggers:</strong><ul class="list-disc ml-4">`;
      m.midi_triggers.forEach(t => html += `<li>CC${t.number} ch${t.channel} ${t.use_value_as_param ? '(value as param)' : ''}</li>`);
      html += `</ul></div>`;
    }
    html += `<details><summary class="cursor-pointer text-orange-400 mt-4">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64 mt-2">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
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