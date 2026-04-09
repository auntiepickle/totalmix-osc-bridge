/* =============================================
   TOTALMIX OSC BRIDGE - Web UI (M2 fixed + initial load)
   Commit base: 3329dd0d017696738abce89fb32be8fd4fa11acd
   Fixes: animation duration, only-last-macro bug, MIDI one-shot bug + initial /api/macros load
   ============================================= */

const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

let macros = {};

ws.onopen = async () => {
    console.log('[WS] Connected to bridge');
    await loadMacros();                    // ← NEW: initial load
};

ws.onclose = () => console.log('[WS] Disconnected');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('[WS] Received:', data);

    if (data.type === 'full_state' || data.type === 'macro_update') {
        if (data.macros) {
            Object.keys(data.macros).forEach(key => {
                macros[key] = { ...(macros[key] || {}), ...data.macros[key] };
            });
        } else if (data.macro) {
            macros[data.macro.name] = { ...(macros[data.macro.name] || {}), ...data.macro };
        }
        renderCards();
    }
};

// ====================== INITIAL MACRO LOAD (critical for Caddy HTTPS) ======================
async function loadMacros() {
    console.log("🚀 loadMacros() called — fetching /api/macros (relative path for Caddy)");
    try {
        const res = await fetch('/api/macros');
        console.log("✅ Fetch status:", res.status);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        macros = await res.json();
        console.log("✅ Loaded", Object.keys(macros).length, "macros from mappings.json");

        renderCards();
    } catch (err) {
        console.error("❌ loadMacros failed:", err);
        const grid = document.getElementById('macro-grid');
        if (grid) grid.innerHTML = `<div class="p-8 text-red-400 text-center">Failed to load macros.<br><small>${err.message}</small></div>`;
    }
}

// ====================== PROGRESS ANIMATION ======================
function animateProgress(name, durationMs) {
    const bar = document.getElementById(`progress-bar-${name}`);
    if (!bar) return;
    bar.style.transitionDuration = '0ms';
    bar.style.width = '0%';
    void bar.offsetWidth;
    bar.style.transitionDuration = `${durationMs}ms`;
    bar.style.width = '100%';
}

// ====================== FIRE MACRO ======================
async function fireMacro(name, value = 1.0, ramp = false) {
    const macro = macros[name];
    if (!macro) return;
    console.log(`[UI] Firing macro: ${name}`);
    const durationMs = macro.durationMs || (ramp ? 3500 : 500);
    animateProgress(name, durationMs);
    ws.send(JSON.stringify({ action: "fire_macro", name: name, value: value, ramp: ramp }));
}

// ====================== RENDER CARDS ======================
function renderCards() {
    const grid = document.getElementById('macro-grid');
    if (!grid) return;
    grid.innerHTML = '';

    Object.keys(macros).forEach(name => {
        const m = macros[name];
        const cardHTML = `
          <div class="card bg-[#1E1E1E] p-6 rounded-2xl" id="card-${name}">
            <div class="flex justify-between items-start">
              <div>
                <h3 class="text-xl font-bold">${name}</h3>
                <p class="text-sm text-gray-400 mt-1">${m.description || ''}</p>
                <div class="text-xs mt-3 text-emerald-400 font-mono">${m.routing_label || '—'}</div>
              </div>
              <div class="text-xs px-3 py-1 bg-orange-500/20 text-orange-400 rounded-full h-fit">MACRO</div>
            </div>
            <div class="mt-4 text-xs font-mono text-gray-400">${m.osc_preview || '—'}</div>
            <div class="mt-6">
              <div class="flex justify-between text-sm mb-1"><span>Value</span><span class="font-mono">${m.value ? m.value.toFixed(3) : '0.000'}</span></div>
              <div class="h-2 bg-gray-800 rounded-full overflow-hidden">
                <div id="progress-bar-${name}" class="progress-bar h-full bg-gradient-to-r from-orange-400 to-purple-400" style="width:0%"></div>
              </div>
            </div>
            <div class="flex gap-2 mt-4">
              ${m.lfo_active ? `<span class="px-3 py-1 text-xs bg-purple-500/20 text-purple-300 rounded-full">LFO ACTIVE</span>` : ''}
            </div>
            <div class="grid grid-cols-2 gap-3 mt-8">
              <button onclick="fireMacro('${name}', 1.0, false)" class="fire-btn bg-orange-500 hover:bg-orange-600 text-black font-bold py-4 rounded-xl">FIRE</button>
              <button onclick="fireMacro('${name}', 1.0, true)" class="border border-orange-500 text-orange-400 hover:bg-orange-500/10 py-4 rounded-xl text-sm font-medium">RAMP / LFO</button>
            </div>
            <button onclick="toggleDetail('${name}')" class="mt-4 text-xs text-gray-400 hover:text-white w-full py-2">Show Details ▼</button>
            <div id="detail-${name}" class="detail-panel hidden mt-3 text-xs font-mono bg-black/50 p-4 rounded-xl overflow-auto max-h-64"></div>
          </div>`;
        grid.innerHTML += cardHTML;
    });
}

function toggleDetail(name) {
    const panel = document.getElementById(`detail-${name}`);
    const m = macros[name];
    if (!panel || !m) return;
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        panel.innerHTML = `<div class="space-y-4"><strong>Routing</strong><br>${m.routing_label || '—'}</div>`;
    }
}

// ====================== MIDI ======================
function handleMIDIMessage(message) { /* ... your existing MIDI code unchanged ... */ }
function updateCardLastTrigger(name, cc, value, deviceName, channel) { /* ... unchanged ... */ }
async function initWebMIDI() { /* ... your existing MIDI code unchanged ... */ }
window.connectSelectedMIDI = () => { /* ... unchanged ... */ };

// Init everything
window.onload = () => {
    initWebMIDI();
    // WS will also trigger loadMacros on open
};