/* =============================================
   TOTALMIX OSC BRIDGE - Web UI (M2 FINAL + MAIN-STYLE RENDER FIXED)
   Fixes: progress bar now visibly animates (pure JS, no Tailwind conflict),
          full MIDI code restored, buttons match main thickness/shadows,
          renderCards only once
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
    updateMacroCard(mu.name);        // incremental ONLY — protects animation
  } else {
    renderCards();                   // full render ONLY on initial load
  }

  updateStatusHeader();
};

// ====================== ENSURE MIDI UI ======================
function ensureMIDIControls() {
  if (document.getElementById('midi-device-selector')) return;
  const header = document.querySelector('header') || document.querySelector('.flex.justify-between') || document.querySelector('.flex');
  if (!header) return;
  const midiDiv = document.createElement('div');
  midiDiv.className = 'flex items-center gap-3 ml-auto';
  midiDiv.innerHTML = `
    <select id="midi-device-selector" class="bg-[#1E1E1E] text-white px-4 py-2 rounded-2xl border border-zinc-700 focus:outline-none text-sm"></select>
    <button onclick="connectSelectedMIDI()" class="px-5 py-2 bg-green-600 hover:bg-green-500 rounded-2xl text-sm font-medium transition-colors">Connect MIDI</button>
  `;
  header.appendChild(midiDiv);
}

// ====================== STATUS HEADER + MIDI BADGE ======================
function updateStatusHeader() {
  const workspaceEl = document.getElementById('workspace');
  const snapshotEl = document.getElementById('snapshot');
  if (workspaceEl) workspaceEl.textContent = `Workspace: ${currentWorkspace || '—'}`;
  if (snapshotEl) snapshotEl.textContent = `Snapshot: ${currentSnapshot || '—'}`;

  let midiBadge = document.getElementById('midi-connected-badge');
  if (midiConnectedDevice && midiConnectedDevice !== '—') {
    if (!midiBadge) {
      midiBadge = document.createElement('div');
      midiBadge.id = 'midi-connected-badge';
      midiBadge.className = 'px-4 py-1 bg-green-600 text-white text-sm font-medium rounded-2xl flex items-center gap-2';
      const header = document.querySelector('header') || document.querySelector('.flex.justify-between') || document.querySelector('.flex');
      if (header) header.appendChild(midiBadge);
    }
    midiBadge.innerHTML = `🎹 ${midiConnectedDevice}`;
  } else if (midiBadge) {
    midiBadge.remove();
  }
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

// ====================== INCREMENTAL CARD UPDATE ======================
function updateMacroCard(name) {
  const card = document.getElementById(`card-${name}`);
  if (!card) return;
  console.log(`[UI] Incremental update for ${name}`);
}

// ====================== FIRE MACRO ======================
async function fireMacro(name, value = 1.0, ramp = false) {
  const macro = macros[name];
  if (!macro) return;

  console.log(`[UI] Firing macro: ${name} (value=${value}, ramp=${ramp})`);

  const durationMs = macro.durationMs || (ramp ? 3500 : 3500);
  animateProgress(name, durationMs);

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

// ====================== RENDER CARDS (progress bar has NO transition-all) ======================
function renderCards() {
  console.log("🔄 renderCards() — should only run once on load");
  const grid = document.getElementById('macro-grid');
  if (!grid) return;
  let html = '';
  Object.keys(macros).forEach(name => {
    const m = macros[name];
    const cardHTML = `
<div id="card-${name}" class="card bg-[#1E1E1E] border border-zinc-700 p-6 rounded-2xl">
    <div class="flex justify-between items-start mb-5">
        <div>
            <h3 class="text-2xl font-bold text-white">${name}</h3>
            <p class="text-zinc-400 text-sm">${m.description || ''}</p>
            <p class="text-orange-400 text-xs font-medium mt-4 tracking-widest">${m.routing_label || '—'}</p>
        </div>
        <div id="last-trigger-${name}" class="midi-badge text-xs font-mono bg-green-500/10 text-green-400 px-3 py-1 rounded-2xl"></div>
    </div>
    
    <!-- PROGRESS BAR — NO Tailwind transition (pure JS control) -->
    <div class="h-2.5 bg-zinc-800 rounded-full overflow-hidden mb-6">
      <div id="progress-bar-${name}" 
           class="h-full bg-gradient-to-r from-orange-400 to-amber-500"
           style="width: 0%"></div>
    </div>
    
    <div class="grid grid-cols-3 gap-3">
        <button onclick="fireMacro('${name}', 1.0, false)" 
                class="fire-btn col-span-2 bg-orange-500 hover:bg-orange-600 active:scale-95 text-black font-bold py-6 rounded-2xl text-2xl transition-all shadow-inner">
            FIRE
        </button>
        <button onclick="fireMacro('${name}', 1.0, true)" 
                class="border-2 border-amber-400 hover:bg-amber-400/10 text-amber-400 font-medium py-6 rounded-2xl transition-all active:scale-95 shadow-inner">
            RAMP
        </button>
    </div>
    
    <button onclick="toggleDetail('${name}')" 
            class="mt-6 w-full text-zinc-400 hover:text-orange-400 text-sm font-medium flex items-center justify-center gap-2">
        DETAILS ▼
    </button>
    <div id="detail-${name}" class="hidden mt-4 p-4 bg-[#111111] rounded-2xl border border-zinc-700 font-mono text-sm"></div>
</div>`;
    html += cardHTML;
  });
  grid.innerHTML = html;
}

// ====================== PROGRESS ANIMATION — PURE JS (no Tailwind conflict) ======================
function animateProgress(name, durationMs) {
  const bar = document.getElementById(`progress-bar-${name}`);
  if (!bar) {
    console.warn(`[UI] Progress bar element not found for ${name}`);
    return;
  }

  console.log(`🎬 STARTING ANIMATION for ${name} — ${durationMs}ms`);

  // Force reset
  bar.style.transition = 'none';
  bar.style.width = '0%';
  void bar.offsetWidth;   // critical reflow

  // Now animate
  bar.style.transition = `width ${durationMs}ms ease-out`;
  bar.style.width = '100%';

  // Auto-reset when done
  setTimeout(() => {
    bar.style.transition = 'width 400ms ease-out';
    bar.style.width = '0%';
    console.log(`🎬 Animation complete for ${name}`);
  }, durationMs);
}

// ====================== RICH DETAILS ======================
function toggleDetail(name) {
  const panel = document.getElementById(`detail-${name}`);
  const m = macros[name];
  if (!panel || !m) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    let html = `<div class="space-y-4">`;
    html += `<div><strong class="text-orange-400">Routing:</strong> ${m.routing_label || '—'}</div>`;
    html += `<div><strong class="text-orange-400">OSC Preview:</strong> <code class="text-amber-300">${m.osc_preview || '—'}</code></div>`;
    html += `<div><strong class="text-orange-400">Duration:</strong> ${m.durationMs ? (m.durationMs/1000).toFixed(1)+'s' : '—'}</div>`;
    if (m.midi_triggers && m.midi_triggers.length) {
      html += `<div><strong class="text-orange-400">MIDI Triggers:</strong><ul class="list-disc ml-4">`;
      m.midi_triggers.forEach(t => {
        html += `<li>CC${t.number} ch${t.channel} ${t.use_value_as_param ? '(value as param)' : ''}</li>`;
      });
      html += `</ul></div>`;
    }
    html += `<details class="mt-4"><summary class="cursor-pointer text-orange-400">Full macro JSON</summary><pre class="text-[10px] overflow-auto max-h-64 mt-2">${JSON.stringify(m, null, 2)}</pre></details>`;
    html += `</div>`;
    panel.innerHTML = html;
  }
}

// ====================== MIDI (FULL CODE — nothing abbreviated) ======================
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
  const badgeContainer = document.getElementById(`last-trigger-${name}`);
  if (badgeContainer) {
    badgeContainer.innerHTML = `<span class="font-semibold">${deviceName}</span><br>CC${cc} • ch${channel}`;
    badgeContainer.classList.add('bg-green-500/20');
  }
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
      if (midiInput) midiInput.onmidimessage = null;
      midiInput = target;
      midiInput.onmidimessage = handleMIDIMessage;
      midiConnectedDevice = target.name;
      console.log(`[MIDI] Auto-connected to ${target.name}`);
      updateStatusHeader();
    }
  } catch (err) {
    console.error('[MIDI] Failed:', err);
  }
}

window.connectSelectedMIDI = () => {
  const selector = document.getElementById('midi-device-selector');
  if (!selector || !midiAccess) return;
  const selectedId = selector.value;
  const input = Array.from(midiAccess.inputs.values()).find(i => i.id === selectedId);
  if (input) {
    if (midiInput) midiInput.onmidimessage = null;
    midiInput = input;
    midiInput.onmidimessage = handleMIDIMessage;
    lastMidiDevice = input.name;
    midiConnectedDevice = input.name;
    localStorage.setItem('lastMidiDevice', input.name);
    updateStatusHeader();
    console.log(`[MIDI] Connected to ${input.name}`);
  }
};

// ====================== INIT ======================
window.onload = () => {
  initWebMIDI();
};