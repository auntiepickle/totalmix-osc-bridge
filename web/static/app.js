const ws = new WebSocket(`ws://${window.location.host}/ws`);
let macros = {};

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
            <button onclick="fireMacro('${name}', 1.0)" 
                    class="fire-btn mt-6 w-full bg-orange-500 hover:bg-orange-600 text-black font-bold py-4 rounded-xl text-lg">
                FIRE
            </button>
            <button onclick="fireMacro('${name}', 1.0, true)" 
                    class="mt-3 w-full border border-orange-500 text-orange-400 hover:bg-orange-500/10 py-3 rounded-xl text-sm">
                RUN AS RAMP/LFO
            </button>
            <!-- Progress bar now always visible when running -->
            <div id="progress-${name}" class="hidden mt-4 h-2 bg-gray-800 rounded-full overflow-hidden">
                <div class="progress-bar h-full bg-gradient-to-r from-orange-400 to-purple-400 transition-all duration-300" style="width:0%"></div>
            </div>
        `;
        grid.appendChild(card);
    });
}

async function fireMacro(name, param, asRamp = false) {
    const btns = document.querySelectorAll(`button[onclick^="fireMacro('${name}'"]`);
    btns.forEach(b => b.disabled = true); // visual feedback

    await fetch(`/api/trigger/${name}?param=${param}`, { method: 'POST' });

    // Show + animate progress bar
    const barContainer = document.getElementById(`progress-${name}`);
    if (barContainer) {
        const bar = barContainer.querySelector('.progress-bar');
        barContainer.classList.remove('hidden');
        bar.style.width = '0%'; // reset

        // Animate to 100% over ~4 seconds (covers your ramps/LFOs)
        setTimeout(() => {
            bar.style.transitionDuration = asRamp ? '3400ms' : '4000ms';
            bar.style.width = '100%';
        }, 10);

        // Auto-hide after animation + extra buffer
        setTimeout(() => {
            barContainer.classList.add('hidden');
            bar.style.width = '0%';
            btns.forEach(b => b.disabled = false);
        }, asRamp ? 5000 : 6000);
    }
}

ws.onopen = loadMacros;
ws.onerror = () => console.error("WebSocket error");