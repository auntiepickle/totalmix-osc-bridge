// === SECURE WEBSOCKET (works on both HTTP and HTTPS) ===
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);
let macros = {};

// ==================== CLIENT-SIDE MIDI (M1) ====================
let midiAccess = null;
let midiInput = null;

async function initWebMIDI() {
    if (!navigator.requestMIDIAccess) {
        console.error("Web MIDI API not supported in this browser");
        return;
    }
    try {
        midiAccess = await navigator.requestMIDIAccess({ sysex: false });
        console.log("Web MIDI ready — available inputs:", Array.from(midiAccess.inputs.values()).map(i => i.name));
        
        // Auto-select first device whose name contains "midi" (works for Cirklon or ANY USB MIDI controller)
        for (const input of midiAccess.inputs.values()) {
            if (input.name.toLowerCase().includes("midi")) {
                midiInput = input;
                midiInput.onmidimessage = handleMIDIMessage;
                console.log(`✅ Connected to MIDI Device: ${input.name}`);
                updateMIDIBadge(`Connected: ${input.name}`);
                return;
            }
        }
        console.warn("No MIDI device found — connect one and click Reconnect MIDI");
    } catch (err) {
        console.error("MIDI access denied or failed", err);
    }
}

function handleMIDIMessage(message) {
    const [status, data1, data2] = message.data;
    const channel = (status & 0x0F) + 1;
    const cc = data1;
    const valueRaw = data2;

    if ((status & 0xF0) !== 0xB0) return;      // only CC messages

    const value = valueRaw / 127.0;

    Object.keys(macros).forEach(name => {
        const macro = macros[name];
        for (const trigger of macro.midi_triggers || []) {
            if (trigger.type === "control_change" &&
                trigger.number === cc &&
                trigger.channel === channel) {

                const range = macro.param_range || [0.0, 1.0];
                const scaled = Math.max(range[0], Math.min(range[1], value));

                console.log(`🎹 MIDI → ${name} (CC${cc} ch${channel} → ${scaled.toFixed(3)})`);
                fireMacro(name, scaled, false);
                updateCardLastTrigger(name, cc, valueRaw);
                return;
            }
        }
    });
}

function updateCardLastTrigger(name, cc, value) {
    const card = Array.from(document.querySelectorAll('.card')).find(c => 
        c.querySelector('h3').textContent === name);
    if (card) {
        let badge = card.querySelector('.midi-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'midi-badge text-[10px] font-mono bg-green-500/20 text-green-400 px-2 py-0.5 rounded mt-2';
            card.appendChild(badge);
        }
        badge.textContent = `CC${cc} • ${value}`;
    }
}

function updateMIDIBadge(text) {
    let badge = document.getElementById('midi-status-badge');
    if (!badge) {
        badge = document.createElement('div');
        badge.id = 'midi-status-badge';
        badge.className = 'px-4 py-1 bg-green-600/90 text-white text-sm font-medium rounded-2xl flex items-center gap-2';
        const topBar = document.querySelector('.flex.justify-between');
        if (topBar) topBar.appendChild(badge);
    }
    badge.innerHTML = `🎹 ${text}`;
}

// ==================== WEBSOCKET & UI ====================

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "state") {
        document.getElementById("workspace").textContent = `Workspace: ${data.workspace || '—'}`;
        document.getElementById("snapshot").textContent = `Snapshot: ${data.snapshot || '—'}`;
    }
};

async function loadMacros() {
    const res = await fetch('/api/macros');
    macros = await res.json();
    const grid = document.getElementById('macro-grid');
    grid.innerHTML = '';
    Object.keys(macros).forEach(name => {
        const m = macros[name];
        const card = document.createElement('div');
        card.className = 'card bg-[#1E1E1E] p-6 rounded-2xl';
        card.innerHTML = `
            <div class="flex justify-between">
                <div>
                    <h3 class="text-xl font-bold">${name}</h3>
                    <p class="text-sm text-gray-400">${m.description || ''}</p>
                </div>
                <div class="text-xs px-3 py-1 bg-orange-500/20 text-orange-400 rounded-full h-fit">MACRO</div>
            </div>
            <button onclick="fireMacro('${name}', 1.0, false)" 
                    class="fire-btn mt-6 w-full bg-orange-500 hover:bg-orange-600 text-black font-bold py-4 rounded-xl text-lg">
                FIRE
            </button>
            <button onclick="fireMacro('${name}', 1.0, true)" 
                    class="mt-3 w-full border border-orange-500 text-orange-400 hover:bg-orange-500/10 py-3 rounded-xl text-sm">
                RUN AS RAMP/LFO
            </button>
            <div id="progress-${name}" class="hidden mt-4 h-2 bg-gray-800 rounded-full overflow-hidden">
                <div class="progress-bar h-full bg-gradient-to-r from-orange-400 to-purple-400 transition-all" style="width:0%"></div>
            </div>
        `;
        grid.appendChild(card);
    });
}

async function fireMacro(name, param, isLFO = false) {
    const btns = document.querySelectorAll(`button[onclick^="fireMacro('${name}'"]`);
    btns.forEach(b => b.disabled = true);

    const barContainer = document.getElementById(`progress-${name}`);
    if (barContainer) {
        const bar = barContainer.querySelector('.progress-bar');
        barContainer.classList.remove('hidden');
        bar.style.transitionDuration = '0ms';
        bar.style.width = '0%';

        void bar.offsetWidth;

        const durationMs = isLFO ? 4000 : 3500;
        bar.style.transitionDuration = `${durationMs}ms`;
        bar.style.width = '100%';

        setTimeout(() => {
            barContainer.classList.add('hidden');
            bar.style.width = '0%';
            btns.forEach(b => b.disabled = false);
        }, durationMs + 800);
    }

    await fetch(`/api/trigger/${name}?param=${param}`, { method: 'POST' });
}

// ==================== STARTUP ====================
ws.onopen = () => {
    loadMacros().then(() => {
        initWebMIDI();
    });
};
ws.onerror = () => console.error("WebSocket error");