/* =============================================
   TOTALMIX OSC BRIDGE - FULLY FIXED app.js
   M2: rich cards + live status bar + last MIDI memory
   ============================================= */

// === SECURE WEBSOCKET ===
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

let macros = {};
let midiAccess = null;
let midiInput = null;

let lastMidiDevice = localStorage.getItem('lastMidiDevice') || '';
let lastMidiChannel = parseInt(localStorage.getItem('lastMidiChannel')) || 1;

// ====================== MIDI HELPERS ======================
function updateMIDIBadge(text) {
    let badge = document.getElementById('midi-status-badge');
    if (!badge) {
        const topBar = document.querySelector('.flex.justify-between');
        if (topBar) {
            badge = document.createElement('div');
            badge.id = 'midi-status-badge';
            badge.className = 'px-4 py-1 bg-green-600/90 text-white text-sm font-medium rounded-2xl flex items-center gap-2';
            topBar.appendChild(badge);
        }
    }
    if (badge) badge.innerHTML = `🎹 ${text}`;
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
    badge.innerHTML = `
        <span class="font-semibold">${deviceName}</span>
        <span>CC${cc} • ch${channel} • ${value}</span>
    `;
}

function handleMIDIMessage(message) {
    const [status, data1, data2] = message.data;
    const channel = (status & 0x0F) + 1;
    const cc = data1;
    const valueRaw = data2;
    const deviceName = message.target ? message.target.name : "Unknown MIDI";

    if ((status & 0xF0) !== 0xB0) return;

    const value = valueRaw / 127.0;

    Object.keys(macros).forEach(name => {
        const macro = macros[name];
        for (const trigger of macro.midi_triggers || []) {
            if (trigger.type === "control_change" && trigger.number === cc && trigger.channel === channel) {
                const range = macro.param_range || [0.0, 1.0];
                const scaled = Math.max(range[0], Math.min(range[1], value));
                console.log(`MIDI → ${name} (${deviceName} | CC${cc} ch${channel} → ${scaled.toFixed(3)})`);
                fireMacro(name, scaled, false);
                updateCardLastTrigger(name, cc, valueRaw, deviceName, channel);
                return;
            }
        }
    });
}

// ====================== MIDI INIT ======================
async function initWebMIDI() {
    if (!navigator.requestMIDIAccess) return;
    try {
        midiAccess = await navigator.requestMIDIAccess({ sysex: false });
        const inputs = Array.from(midiAccess.inputs.values());

        let selector = document.getElementById('midi-device-selector');
        if (!selector) return;

        selector.innerHTML = '<option value="">— select MIDI input —</option>';
        inputs.forEach(input => {
            const opt = document.createElement('option');
            opt.value = input.id;
            opt.textContent = input.name;
            if (input.name === lastMidiDevice) opt.selected = true;
            selector.appendChild(opt);
        });

        const target = inputs.find(i => i.name === lastMidiDevice) || inputs.find(i => i.name.toLowerCase().includes("midi"));
        if (target && !midiInput) {
            midiInput = target;
            midiInput.onmidimessage = handleMIDIMessage;
            updateMIDIBadge(`Connected: ${target.name}`);
        }
    } catch (err) {
        console.error("MIDI access failed", err);
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
        updateMIDIBadge(`Connected: ${input.name}`);
        localStorage.setItem('lastMidiDevice', input.name);
        lastMidiDevice = input.name;
        localStorage.setItem('lastMidiChannel', lastMidiChannel);
    }
};

// ====================== RICH CARD RENDERING ======================
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
                    <div class="flex justify-between text-sm mb-1">
                        <span>Value</span>
                        <span class="font-mono">${m.value ? m.value.toFixed(3) : '0.000'}</span>
                    </div>
                    <div class="h-2 bg-gray-800 rounded-full overflow-hidden">
                        <div id="progress-bar-${name}" class="progress-bar h-full bg-gradient-to-r from-orange-400 to-purple-400 transition-all" 
                             style="width: ${m.progress || 100}%"></div>
                    </div>
                </div>

                <div class="flex gap-2 mt-4">
                    ${m.lfo_active ? `<span class="px-3 py-1 text-xs bg-purple-500/20 text-purple-300 rounded-full">LFO ACTIVE</span>` : ''}
                    ${m.midi_trigger ? `<span class="px-3 py-1 text-xs bg-green-500/20 text-green-400 rounded-full font-mono">CC${m.midi_trigger.number} ch${m.midi_trigger.channel}</span>` : ''}
                </div>

                <div class="grid grid-cols-2 gap-3 mt-8">
                    <button onclick="fireMacro('${name}', 1.0, false)" class="fire-btn bg-orange-500 hover:bg-orange-600 text-black font-bold py-4 rounded-xl">FIRE</button>
                    <button onclick="fireMacro('${name}', 1.0, true)" class="border border-orange-500 text-orange-400 hover:bg-orange-500/10 py-4 rounded-xl text-sm font-medium">RAMP / LFO</button>
                </div>

                <button onclick="toggleDetail('${name}')" class="mt-4 text-xs text-gray-400 hover:text-white w-full py-2">Show Details ▼</button>
                <div id="detail-${name}" class="detail-panel hidden mt-3 text-xs font-mono bg-black/50 p-4 rounded-xl overflow-auto max-h-64"></div>
            </div>
        `;
        grid.innerHTML += cardHTML;
    });
}

function toggleDetail(name) {
    const panel = document.getElementById(`detail-${name}`);
    const m = macros[name];
    if (!panel || !m) return;

    let html = `<div class="space-y-3">`;
    html += `<div><strong>Description</strong><br>${m.description || '—'}</div>`;
    html += `<div><strong>Routing</strong><br>${m.routing_label || '—'}</div>`;
    html += `<div><strong>OSC Preview</strong><br>${m.osc_preview || '—'}</div>`;
    html += `<div><strong>Last Trigger</strong><br>${new Date(m.last_trigger * 1000).toLocaleString()}</div>`;
    if (m.midi_trigger) {
        html += `<div><strong>MIDI Trigger</strong><br>CC${m.midi_trigger.number} ch${m.midi_trigger.channel}</div>`;
    }
    html += `</div>`;
    panel.innerHTML = html;
    panel.classList.toggle('hidden');
}

// ====================== FIRE MACRO ======================
window.fireMacro = async (name, param, isLFO = false) => {
    const btns = document.querySelectorAll(`button[onclick^="fireMacro('${name}'"]`);
    btns.forEach(b => b.disabled = true);

    await fetch(`/api/trigger/${name}?param=${param}`, { method: 'POST' });

    setTimeout(() => btns.forEach(b => b.disabled = false), 1500);
};

// ====================== WEBSOCKET (fixed merge + status bar) ======================
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === "state") {
        // Live status bar (HA changes now work)
        if (data.workspace) document.getElementById("workspace").textContent = `Workspace: ${data.workspace}`;
        if (data.snapshot) document.getElementById("snapshot").textContent = `Snapshot: ${data.snapshot || '—'}`;

        // Full macros list (initial load)
        if (data.macros) {
            macros = data.macros;
            renderCards();
        }

        // Real-time update from macro fire/ramp/LFO
        if (data.macro_update) {
            macros[data.macro_update.name] = data.macro_update;
            renderCards();

            // Highlight the active card
            const card = document.getElementById(`card-${data.macro_update.name}`);
            if (card) {
                card.classList.add('ring-2', 'ring-orange-400');
                setTimeout(() => card.classList.remove('ring-2', 'ring-orange-400'), 1500);
            }
        }
    }
};

// ====================== STARTUP ======================
ws.onopen = () => {
    console.log("WebSocket connected — rich cards + live status active");
    loadMacros().then(() => initWebMIDI());
};

async function loadMacros() {
    try {
        const res = await fetch('/api/macros');
        const data = await res.json();
        macros = data.macros || data;
        renderCards();
    } catch (e) {
        console.error("Failed to load macros", e);
    }
}

ws.onerror = (err) => console.error("WebSocket error", err);